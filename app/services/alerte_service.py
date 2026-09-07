"""
Service métier — dispatch des alertes hiérarchiques (Processus 3, étape 5
"Envoyer alerte automatique" + étape 6 "Recevoir l'alerte" côté Chef de
service, qui est une simple réception passive côté back-office).

Destinataires :
  - Email : tous les comptes `utilisateur` actifs dont le rôle est
    "Chef de service" (rôle de référence créé par le Processus 2). Diffuser
    à tous les chefs de service actifs plutôt qu'à un seul évite de dépendre
    d'un lien service -> responsable qui n'existe pas dans le schéma de
    données d'origine.
  - SMS : optionnel, piloté par le paramètre système `telephone_hierarchie`
    (liste de numéros séparés par des virgules). Ce choix réutilise la table
    `parametre_systeme` déjà prévue pour la "personnalisation des seuils et
    règles métier par l'administrateur" plutôt que d'ajouter une colonne
    téléphone au schéma d'origine pour un canal optionnel.
"""
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import notifications
from app.models.alerte import Alerte
from app.models.anomalie import Anomalie
from app.models.enums import CanalAlerte, StatutAlerte
from app.models.rbac import Role
from app.models.utilisateur import Utilisateur
from app.services import parametre_service

NOM_ROLE_CHEF_SERVICE = "Chef de service"


def _emails_chefs_service(db: Session) -> List[str]:
    stmt = (
        select(Utilisateur.email)
        .join(Role, Utilisateur.id_role == Role.id_role)
        .where(Role.nom_role == NOM_ROLE_CHEF_SERVICE, Utilisateur.actif.is_(True))
    )
    return [email for email in db.execute(stmt).scalars().all()]


def _telephones_hierarchie(db: Session) -> List[str]:
    valeur = parametre_service.get_valeur(db, "telephone_hierarchie")
    if not valeur:
        return []
    return [numero.strip() for numero in valeur.split(",") if numero.strip()]


def _contenu_alerte(anomalie: Anomalie) -> tuple[str, str]:
    agent = anomalie.agent
    sujet = f"[SRB] Anomalie de pointage — {agent.nom} {agent.prenom}"
    corps = (
        f"Une anomalie de type « {anomalie.type_anomalie.value} » a été détectée "
        f"pour l'agent {agent.prenom} {agent.nom} (matricule {agent.matricule}) "
        f"le {anomalie.date_detection.strftime('%d/%m/%Y à %H:%M')}.\n"
        "Ce message est généré automatiquement par le système de pointage SRB "
        "Haute Matsiatra suite au dépassement d'un seuil ou à une récidive."
    )
    return sujet, corps


def envoyer_alertes(db: Session, anomalie: Anomalie) -> List[Alerte]:
    """
    Envoie l'alerte (étape 5 du Processus 3) à la hiérarchie pour l'anomalie
    donnée et journalise une ligne `alerte` par destinataire/canal, que
    l'envoi ait réussi ou échoué (traçabilité complète).
    """
    sujet, corps = _contenu_alerte(anomalie)
    alertes: List[Alerte] = []

    for email in _emails_chefs_service(db):
        succes = notifications.envoyer_email(email, sujet, corps)
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire=email,
                statut=StatutAlerte.ENVOYEE if succes else StatutAlerte.ECHEC,
            )
        )

    for telephone in _telephones_hierarchie(db):
        succes = notifications.envoyer_sms(telephone, corps)
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.SMS,
                destinataire=telephone,
                statut=StatutAlerte.ENVOYEE if succes else StatutAlerte.ECHEC,
            )
        )

    if not alertes:
        # Aucun chef de service actif et aucun téléphone configuré : rien à
        # notifier, mais on le trace quand même pour ne pas masquer un défaut
        # de configuration (aucun compte "Chef de service" créé, typiquement).
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire="(aucun destinataire configuré)",
                statut=StatutAlerte.ECHEC,
            )
        )

    db.add_all(alertes)
    db.commit()
    for alerte in alertes:
        db.refresh(alerte)
    return alertes


def envoyer_alertes_escalade(db: Session, anomalie: Anomalie) -> List[Alerte]:
    """
    Envoie une alerte d'escalade (ex. vers les Administrateurs) lorsqu'un
    cas grave est détecté (trois absences consécutives, etc.). Journalise
    les tentatives d'envoi comme pour `envoyer_alertes`.
    """
    sujet = f"[SRB] Escalade : Absences consécutives — {anomalie.agent.nom} {anomalie.agent.prenom}"
    corps = (
        f"L'agent {anomalie.agent.prenom} {anomalie.agent.nom} (matricule {anomalie.agent.matricule}) "
        f"présente plusieurs absences consécutives détectées par le système, dernière détection le {anomalie.date_detection.strftime('%d/%m/%Y')}.\n"
        "Merci de vérifier et d'initier les actions nécessaires."
    )

    alertes: List[Alerte] = []
    # Destinataires : Administrateurs
    stmt = (
        select(Utilisateur.email)
        .join(Role, Utilisateur.id_role == Role.id_role)
        .where(Role.nom_role == 'Administrateur', Utilisateur.actif.is_(True))
    )
    emails = [e for e in db.execute(stmt).scalars().all()]

    for email in emails:
        succes = notifications.envoyer_email(email, sujet, corps)
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire=email,
                statut=StatutAlerte.ENVOYEE if succes else StatutAlerte.ECHEC,
            )
        )

    if not alertes:
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire="(aucun administrateur configuré)",
                statut=StatutAlerte.ECHEC,
            )
        )

    db.add_all(alertes)
    db.commit()
    for alerte in alertes:
        db.refresh(alerte)
    return alertes


def envoyer_alertes_escalade_retards(db: Session, anomalie: Anomalie) -> List[Alerte]:
    """
    Envoie une alerte d'escalade spécifique pour retards consécutifs.
    Destinataires : Administrateurs (par défaut).
    """
    sujet = f"[SRB] Escalade : Retards consécutifs — {anomalie.agent.nom} {anomalie.agent.prenom}"
    corps = (
        f"L'agent {anomalie.agent.prenom} {anomalie.agent.nom} (matricule {anomalie.agent.matricule}) "
        f"présente plusieurs retards consécutifs détectés par le système, dernière détection le {anomalie.date_detection.strftime('%d/%m/%Y')}.
Merci de vérifier et d'initier les actions nécessaires."
    )

    alertes: List[Alerte] = []
    stmt = (
        select(Utilisateur.email)
        .join(Role, Utilisateur.id_role == Role.id_role)
        .where(Role.nom_role == 'Administrateur', Utilisateur.actif.is_(True))
    )
    emails = [e for e in db.execute(stmt).scalars().all()]

    for email in emails:
        succes = notifications.envoyer_email(email, sujet, corps)
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire=email,
                statut=StatutAlerte.ENVOYEE if succes else StatutAlerte.ECHEC,
            )
        )

    if not alertes:
        alertes.append(
            Alerte(
                id_anomalie=anomalie.id_anomalie,
                canal=CanalAlerte.EMAIL,
                destinataire="(aucun administrateur configuré)",
                statut=StatutAlerte.ECHEC,
            )
        )

    db.add_all(alertes)
    db.commit()
    for alerte in alertes:
        db.refresh(alerte)
    return alertes
