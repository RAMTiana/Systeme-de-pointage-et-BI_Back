"""
Script de seed — données de référence (section 5 du schéma SQL d'origine).

Alembic (`alembic/versions/04be6d39f33b_init_schema.py`) ne crée que le
schéma (tables, contraintes, index) : il ne rejoue pas les `INSERT`
de données de référence du script SQL fourni au départ, une migration de
schéma n'ayant pas vocation à embarquer des données métier figées. Ce script
comble cet écart, en s'exécutant après les migrations, sur n'importe quelle
installation (locale, recette, production).

Couvre :
  - les 3 rôles et 6 permissions de la section 5 du schéma SQL d'origine ;
  - leur affectation (table `role_permission`, absente du script SQL
    d'origine — celui-ci ne fait qu'insérer les lignes `role` et
    `permission` indépendamment, sans les relier) ; le choix retenu ici
    découle directement des lanes BPMN et du chapitre IV du cahier des
    charges (cf. commentaires ci-dessous) ;
  - les 5 paramètres système de la section 5, plus quatre paramètres apparus
    au fil de l'implémentation et documentés dans leur module respectif :
    `seuil_distance_faciale` (module Pointage, reconnaissance faciale),
    `telephone_hierarchie` (module Anomalies, alertes SMS), et
    `heure_debut_travail` / `heure_fin_travail` (horaire standard 8h-17h
    utilisé en secours par `horaire_service` quand un service n'a pas
    d'horaire de référence explicite, cf. module Anomalies).

Idempotent : peut être rejoué sans risque sur une base déjà peuplée.
  - Rôles/permissions : créés s'ils manquent ; la description d'une
    permission déjà présente est resynchronisée sur celle définie ici (pur
    texte d'affichage, jamais modifié manuellement en production).
  - Associations role_permission : uniquement complétées, jamais retirées
    (une association ajoutée manuellement par un administrateur n'est pas
    effacée par un rejeu du script).
  - Paramètres système : créés s'ils manquent, mais leur `valeur` n'est
    JAMAIS écrasée si le paramètre existe déjà — un administrateur a pu
    l'ajuster depuis l'installation initiale (cf. cahier des charges,
    "Personnalisation des seuils et règles métier par l'administrateur").

Usage :
    python -m scripts.seed_reference_data
"""
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.parametre_systeme import ParametreSysteme
from app.models.rbac import Permission, Role

# ---------------------------------------------------------------------
# Rôles (section 5 du schéma SQL d'origine — noms exacts, ne pas modifier :
# `alerte_service.NOM_ROLE_CHEF_SERVICE` et le RBAC en dépendent tels quels)
# ---------------------------------------------------------------------
NOMS_ROLES: List[str] = ["Administrateur", "Chef de service", "Secretaire"]

# ---------------------------------------------------------------------
# Permissions (section 5 du schéma SQL d'origine — noms et descriptions
# recopiés à l'identique)
# ---------------------------------------------------------------------
PERMISSIONS: List[Tuple[str, str]] = [
    ("gerer_agents", "Ajouter, modifier, désactiver les agents"),
    ("gerer_services", "Créer et administrer les services"),
    ("traiter_anomalies", "Examiner et justifier les anomalies"),
    ("generer_rapports", "Générer et exporter les rapports"),
    ("consulter_bi", "Accéder au tableau de bord décisionnel"),
    ("valider_roles", "Valider les rôles et permissions des agents"),
]

# ---------------------------------------------------------------------
# Affectation role -> permissions.
#
# Le script SQL d'origine ne fixe pas cette affectation (seules les tables
# `role` et `permission` y sont peuplées, sans lignes `role_permission`).
# Le choix ci-dessous découle des lanes de la conception BPMN et du chapitre
# IV du cahier des charges ("Acteurs du système") :
#
#   - Administrateur : "Gestion globale du système" (chapitre IV) — reçoit
#     toutes les permissions, y compris la configuration des seuils
#     (parametre_systeme, gérée hors RBAC applicatif) et la validation des
#     rôles.
#   - Chef de service : présent uniquement dans les lanes des processus de
#     restitution/validation (Processus 2 : "attribue des rôles ... validés
#     par le chef de service" -> valider_roles ; Processus 5, seul acteur
#     métier de la lane -> consulter_bi), jamais dans les lanes d'exécution
#     opérationnelle (Processus 1, 3, 4 où seule la Secrétaire agit) : rôle
#     de supervision et de validation, pas de saisie.
#   - Secretaire : porte toutes les tâches utilisateur des Processus 1-4
#     (gestion agents/services, traitement des anomalies, génération des
#     rapports), conformément à "Gestion des agents et des services • Suivi
#     quotidien des pointages • Correction des anomalies • Génération des
#     rapports" (chapitre IV) — mais n'apparaît pas dans les lanes du
#     Processus 5 (BI), donc pas de `consulter_bi`.
# ---------------------------------------------------------------------
AFFECTATIONS: Dict[str, List[str]] = {
    "Administrateur": [nom for nom, _ in PERMISSIONS],
    "Chef de service": ["valider_roles", "consulter_bi"],
    "Secretaire": ["gerer_agents", "gerer_services", "traiter_anomalies", "generer_rapports"],
}

# ---------------------------------------------------------------------
# Paramètres système.
# Les 5 premiers sont recopiés à l'identique de la section 5 du schéma SQL
# d'origine ; les deux derniers documentent des paramètres consommés par le
# code (cf. `pointage_service._identite_verifiee` et
# `alerte_service._telephones_hierarchie`) mais absents du script SQL
# d'origine, qui ne prévoyait pas encore la reconnaissance faciale ni le
# canal SMS au moment de sa rédaction.
# ---------------------------------------------------------------------
PARAMETRES: List[Tuple[str, str, str]] = [
    ("seuil_retard_minutes", "15",
     "Nombre de minutes au-delà duquel un pointage est considéré en retard"),
    ("seuil_recidive", "3",
     "Nombre de retards sur la période glissante déclenchant une alerte"),
    ("periode_glissante_jours", "30",
     "Fenêtre en jours utilisée pour le calcul de la récidive"),
    ("heure_debut_travail", "08:00",
     "Heure d'entrée standard (format HH:MM), appliquée aux services sans "
     "horaire de référence spécifique pour le calcul des retards/absences"),
    ("heure_fin_travail", "17:00",
     "Heure de fin de service standard (format HH:MM), appliquée aux services "
     "sans horaire de référence spécifique pour le calcul des départs anticipés"),
    ("code_verification_expiration_minutes", "15",
     "Durée de validité d'un code de réinitialisation de mot de passe"),
    ("code_verification_tentatives_max", "5",
     "Nombre maximal de tentatives de saisie avant invalidation du code"),
    ("seuil_distance_faciale", "0.6",
     "Distance euclidienne maximale entre le vecteur facial capté et l'empreinte "
     "enregistrée pour valider l'identité (plus la valeur est basse, plus le "
     "contrôle est strict)"),
    ("telephone_hierarchie", "",
     "Numéros de téléphone de la hiérarchie pour les alertes SMS, séparés par des "
     "virgules (canal optionnel — laisser vide désactive l'envoi de SMS)"),
]


def _assurer_roles(db: Session) -> Dict[str, Role]:
    roles: Dict[str, Role] = {}
    for nom_role in NOMS_ROLES:
        role = db.execute(select(Role).where(Role.nom_role == nom_role)).scalar_one_or_none()
        if role is None:
            role = Role(nom_role=nom_role)
            db.add(role)
            db.flush()  # pour disposer de id_role avant les associations
            print(f"  + rôle créé : {nom_role}")
        roles[nom_role] = role
    return roles


def _assurer_permissions(db: Session) -> Dict[str, Permission]:
    permissions: Dict[str, Permission] = {}
    for nom_permission, description in PERMISSIONS:
        permission = db.execute(
            select(Permission).where(Permission.nom_permission == nom_permission)
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(nom_permission=nom_permission, description=description)
            db.add(permission)
            db.flush()
            print(f"  + permission créée : {nom_permission}")
        elif permission.description != description:
            permission.description = description  # resynchronise le texte d'affichage uniquement
        permissions[nom_permission] = permission
    return permissions


def _assurer_affectations(db: Session, roles: Dict[str, Role], permissions: Dict[str, Permission]) -> None:
    for nom_role, noms_permissions in AFFECTATIONS.items():
        role = roles[nom_role]
        deja_affectees = {p.nom_permission for p in role.permissions}
        for nom_permission in noms_permissions:
            if nom_permission in deja_affectees:
                continue
            role.permissions.append(permissions[nom_permission])
            print(f"  + permission « {nom_permission} » affectée au rôle « {nom_role} »")


def _assurer_parametres(db: Session) -> None:
    for nom_parametre, valeur_defaut, description in PARAMETRES:
        parametre = db.execute(
            select(ParametreSysteme).where(ParametreSysteme.nom_parametre == nom_parametre)
        ).scalar_one_or_none()
        if parametre is None:
            db.add(ParametreSysteme(nom_parametre=nom_parametre, valeur=valeur_defaut, description=description))
            print(f"  + paramètre créé : {nom_parametre} = {valeur_defaut!r}")
        elif parametre.description != description:
            parametre.description = description  # ne touche jamais à `valeur`


def seed_reference_data(db: Session) -> None:
    print("Rôles :")
    roles = _assurer_roles(db)
    print("Permissions :")
    permissions = _assurer_permissions(db)
    print("Affectations role_permission :")
    _assurer_affectations(db, roles, permissions)
    print("Paramètres système :")
    _assurer_parametres(db)
    db.commit()
    print("Terminé — données de référence à jour.")


if __name__ == "__main__":
    session = SessionLocal()
    try:
        seed_reference_data(session)
    finally:
        session.close()