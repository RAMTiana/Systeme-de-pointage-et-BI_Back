"""
Service métier — Module Pointage (Processus 1 du BPMN "Pointage d'un agent").

Couvre les étapes 1 à 18 du diagramme :
  - lecture QR/badge ou capture+comparaison faciale (identification)
  - vérification de l'absence de double pointage sur la journée
  - horodatage et enregistrement du pointage
  - journalisation (audit)
  - calcul retard / départ anticipé par comparaison aux horaires de référence

La qualification/le traitement complet des anomalies (seuils, récidive,
alertes vers la hiérarchie, examen et justificatif par la secrétaire) est
délégué à `app/services/anomalie_service.py` (Processus 3 du BPMN). Ici, la
création de la ligne `anomalie` (statut `en_attente`) puis l'appel à
`anomalie_service.qualifier_et_alerter` matérialisent le message BPMN
«MessageAnomalie» envoyé du Processus 1 vers le Processus 3 : dans ce
backend monolithique, le lien inter-processus décrit dans la conception
BPMN comme «dépendance de données en base» est réalisé par un appel direct
dans la même transaction, plutôt que par un événement de message
asynchrone séparé.

Choix : les tentatives de pointage refusées (identité non vérifiée,
doublon) sont malgré tout persistées (`statut = 'rejete'` / `'doublon'`)
plutôt qu'ignorées, afin de conserver une traçabilité complète des
tentatives — cohérent avec la présence de ces valeurs dans
`statut_pointage_enum` et avec l'exigence de traçabilité du cahier des
charges (utile notamment pour repérer des tentatives de fraude répétées
par reconnaissance faciale).
"""
import math
from datetime import date as date_
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import (
    JourSemaine,
    ModePointage,
    StatutAgent,
    StatutPointage,
    TypeAnomalie,
    TypePointage,
)
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.schemas.pointage import PointageFacialCreate, PointageQrBadgeCreate, PointageWebAuthnCreate
from app.services import anomalie_service, empreinte_service, journal_audit_service, parametre_service, webauthn_service

_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]


# --------------------------------------------------------------------
# Résolution et garde-fous sur l'agent
# --------------------------------------------------------------------

def _agent_ou_404(db: Session, matricule: Optional[str], id_agent: Optional[int]) -> Agent:
    stmt = select(Agent).options(
        joinedload(Agent.empreinte_biometrique),
        joinedload(Agent.identifiant_webauthn),
    )
    if id_agent is not None:
        stmt = stmt.where(Agent.id_agent == id_agent)
    else:
        stmt = stmt.where(Agent.matricule == matricule)
    agent = db.execute(stmt).unique().scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent introuvable.")
    return agent


def _verifier_agent_actif(agent: Agent) -> None:
    if agent.statut != StatutAgent.ACTIF:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent désactivé : pointage refusé.",
        )


# --------------------------------------------------------------------
# Détection de doublon (étapes 9-11)
# --------------------------------------------------------------------

def _pointage_deja_enregistre_aujourdhui(db: Session, id_agent: int, type_pointage: TypePointage, instant: datetime) -> bool:
    """Empêche un double pointage du même type (entrée ou sortie) sur la même journée."""
    debut_jour = datetime.combine(instant.date(), datetime.min.time())
    fin_jour = datetime.combine(instant.date(), datetime.max.time())
    stmt = select(func.count()).select_from(Pointage).where(
        Pointage.id_agent == id_agent,
        Pointage.type_pointage == type_pointage,
        Pointage.statut == StatutPointage.VALIDE,
        Pointage.date_heure.between(debut_jour, fin_jour),
    )
    return db.execute(stmt).scalar_one() > 0


# --------------------------------------------------------------------
# Reconnaissance faciale (étapes 5-6) : comparaison de vecteurs
# --------------------------------------------------------------------

def _distance_euclidienne(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le vecteur facial transmis n'a pas la même dimension que l'empreinte de référence.",
        )
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _identite_verifiee(db: Session, agent: Agent, vecteur_capture: List[float]) -> bool:
    empreinte_reference = empreinte_service.decoder_vecteur(agent.empreinte_biometrique.encodage_facial)
    seuil = parametre_service.get_float(db, "seuil_distance_faciale", default=0.6)
    distance = _distance_euclidienne(vecteur_capture, empreinte_reference)
    return distance <= seuil


# --------------------------------------------------------------------
# Calcul d'anomalie horaire (étape 14) — retard / départ anticipé
# --------------------------------------------------------------------

def _ajouter_minutes(heure, minutes: int):
    reference = datetime.combine(date_.today(), heure) + timedelta(minutes=minutes)
    return reference.time()


def _detecter_anomalie_horaire(db: Session, agent: Agent, pointage: Pointage) -> Optional[Anomalie]:
    if agent.id_service is None:
        return None  # pas de service principal -> pas d'horaire de référence exploitable

    jour = _JOURS_PAR_INDEX[pointage.date_heure.weekday()]
    stmt = select(HoraireReference).where(
        HoraireReference.id_service == agent.id_service,
        HoraireReference.jour_semaine == jour,
    )
    horaire = db.execute(stmt).scalar_one_or_none()
    if horaire is None:
        return None  # aucun horaire de référence défini pour ce service/jour

    heure_pointage = pointage.date_heure.time()
    type_anomalie: Optional[TypeAnomalie] = None

    if pointage.type_pointage == TypePointage.ENTREE:
        seuil_minutes = parametre_service.get_int(db, "seuil_retard_minutes", default=15)
        limite = _ajouter_minutes(horaire.heure_debut, seuil_minutes)
        if heure_pointage > limite:
            type_anomalie = TypeAnomalie.RETARD
    else:  # SORTIE
        if heure_pointage < horaire.heure_fin:
            type_anomalie = TypeAnomalie.DEPART_ANTICIPE

    if type_anomalie is None:
        return None

    anomalie = Anomalie(id_agent=agent.id_agent, id_pointage=pointage.id_pointage, type_anomalie=type_anomalie)
    db.add(anomalie)
    db.commit()
    db.refresh(anomalie)

    # Matérialise la suite du Processus 3 (seuils/récidive, alerte à la
    # hiérarchie) dans la même transaction applicative, cf. anomalie_service.
    anomalie_service.qualifier_et_alerter(db, anomalie)
    return anomalie


# --------------------------------------------------------------------
# Enregistrement + journalisation communs (étapes 12-13)
# --------------------------------------------------------------------

def _enregistrer_et_journaliser(db: Session, agent: Agent, pointage: Pointage) -> Pointage:
    db.add(pointage)
    db.commit()
    db.refresh(pointage)
    journal_audit_service.log_action(
        db,
        id_utilisateur=None,
        action="pointage",
        details=(
            f"agent={agent.matricule} type={pointage.type_pointage.value} "
            f"mode={pointage.mode_pointage.value} statut={pointage.statut.value}"
        ),
    )
    return pointage


# --------------------------------------------------------------------
# Flux principaux
# --------------------------------------------------------------------

def pointer_qr_badge(
    db: Session, payload: PointageQrBadgeCreate, mode: ModePointage
) -> Tuple[Pointage, Optional[Anomalie]]:
    agent = _agent_ou_404(db, payload.matricule, payload.id_agent)
    _verifier_agent_actif(agent)

    maintenant = datetime.now()
    doublon = _pointage_deja_enregistre_aujourdhui(db, agent.id_agent, payload.type_pointage, maintenant)

    pointage = Pointage(
        id_agent=agent.id_agent,
        date_heure=maintenant,
        type_pointage=payload.type_pointage,
        mode_pointage=mode,
        statut=StatutPointage.DOUBLON if doublon else StatutPointage.VALIDE,
    )
    pointage = _enregistrer_et_journaliser(db, agent, pointage)

    if doublon:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un pointage '{payload.type_pointage.value}' est déjà enregistré aujourd'hui pour cet agent.",
        )

    anomalie = _detecter_anomalie_horaire(db, agent, pointage)
    return pointage, anomalie


def pointer_facial(db: Session, payload: PointageFacialCreate) -> Tuple[Pointage, Optional[Anomalie]]:
    agent = _agent_ou_404(db, payload.matricule, payload.id_agent)
    _verifier_agent_actif(agent)

    if not agent.consentement_facial or agent.empreinte_biometrique is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pointage par reconnaissance faciale indisponible pour cet agent "
            "(consentement non donné ou empreinte non enregistrée).",
        )

    maintenant = datetime.now()
    # Deux modes : embedding pré-calculé côté client (nominal), ou image brute
    # (fallback navigateur sans face-api.js). En mode image, on fait confiance au
    # matricule + consentement enregistré, faute de reco serveur. TODO : intégrer.
    if payload.encodage_facial:
        identite_ok = _identite_verifiee(db, agent, payload.encodage_facial)
    else:
        identite_ok = True  # fallback image_base64 : identité présumée par matricule

    if not identite_ok:
        pointage = Pointage(
            id_agent=agent.id_agent,
            date_heure=maintenant,
            type_pointage=payload.type_pointage,
            mode_pointage=ModePointage.FACIAL,
            statut=StatutPointage.REJETE,
        )
        _enregistrer_et_journaliser(db, agent, pointage)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité non vérifiée : le visage capté ne correspond pas à l'empreinte enregistrée.",
        )

    doublon = _pointage_deja_enregistre_aujourdhui(db, agent.id_agent, payload.type_pointage, maintenant)
    pointage = Pointage(
        id_agent=agent.id_agent,
        date_heure=maintenant,
        type_pointage=payload.type_pointage,
        mode_pointage=ModePointage.FACIAL,
        statut=StatutPointage.DOUBLON if doublon else StatutPointage.VALIDE,
    )
    pointage = _enregistrer_et_journaliser(db, agent, pointage)

    if doublon:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un pointage '{payload.type_pointage.value}' est déjà enregistré aujourd'hui pour cet agent.",
        )

    anomalie = _detecter_anomalie_horaire(db, agent, pointage)
    return pointage, anomalie


# --------------------------------------------------------------------
# Consultation (back-office)
# --------------------------------------------------------------------

def get_by_id_or_404(db: Session, id_pointage: int) -> Pointage:
    stmt = select(Pointage).options(joinedload(Pointage.agent)).where(Pointage.id_pointage == id_pointage)
    pointage = db.execute(stmt).unique().scalar_one_or_none()
    if pointage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pointage introuvable.")
    return pointage


def lister_pointages(
    db: Session,
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    type_pointage: Optional[TypePointage] = None,
    statut: Optional[StatutPointage] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Pointage], int]:
    stmt = select(Pointage).options(joinedload(Pointage.agent))
    if id_service is not None:
        stmt = stmt.join(Agent, Pointage.id_agent == Agent.id_agent).where(Agent.id_service == id_service)

    conditions = []
    if id_agent is not None:
        conditions.append(Pointage.id_agent == id_agent)
    if type_pointage is not None:
        conditions.append(Pointage.type_pointage == type_pointage)
    if statut is not None:
        conditions.append(Pointage.statut == statut)
    if date_debut is not None:
        conditions.append(Pointage.date_heure >= datetime.combine(date_debut, datetime.min.time()))
    if date_fin is not None:
        conditions.append(Pointage.date_heure <= datetime.combine(date_fin, datetime.max.time()))

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Pointage.id_pointage).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Pointage.date_heure.desc()).offset(skip).limit(limit)
    pointages = list(db.execute(stmt).unique().scalars().all())

    return pointages, total



def options_webauthn(db: Session, matricule: str) -> dict:
    """Génère le challenge d'authentification WebAuthn pour l'agent identifié par son matricule."""
    agent = _agent_ou_404(db, matricule, None)
    _verifier_agent_actif(agent)
    return webauthn_service.options_pointage(agent)


def pointer_webauthn(db: Session, payload: PointageWebAuthnCreate) -> Tuple[Pointage, Optional[Anomalie]]:
    """Pointage biométrique via WebAuthn (Touch ID / Windows Hello / empreinte téléphone).

    L'assertion transmise par le client est vérifiée cryptographiquement
    (signature + compteur anti-rejeu) contre la clé publique WebAuthn
    préalablement enregistrée pour l'agent (cf. PUT /agents/{id}/webauthn et
    app/services/webauthn_service.py). Le challenge attendu est celui généré
    par GET /pointage/webauthn/options juste avant, conservé côté serveur.
    """
    agent = _agent_ou_404(db, payload.matricule, payload.id_agent)
    _verifier_agent_actif(agent)

    # Lève une HTTPException 401/403/400 si l'assertion, le challenge ou le
    # credential enregistré ne concordent pas — aucun pointage n'est créé
    # dans ce cas (identité non prouvée).
    webauthn_service.verifier_assertion(db, agent, payload.webauthn)

    maintenant = datetime.now()
    doublon = _pointage_deja_enregistre_aujourdhui(db, agent.id_agent, payload.type_pointage, maintenant)

    pointage = Pointage(
        id_agent=agent.id_agent,
        date_heure=maintenant,
        type_pointage=payload.type_pointage,
        mode_pointage=ModePointage.WEBAUTHN,
        statut=StatutPointage.DOUBLON if doublon else StatutPointage.VALIDE,
    )
    pointage = _enregistrer_et_journaliser(db, agent, pointage)

    if doublon:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un pointage '{payload.type_pointage.value}' est déjà enregistré aujourd'hui pour cet agent.",
        )

    anomalie = _detecter_anomalie_horaire(db, agent, pointage)
    return pointage, anomalie
