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
    MotifSortie,
    StatutAgent,
    StatutJustification,
    StatutPointage,
    TypeAnomalie,
    TypePointage,
)
from app.models.justificatif import Justificatif
from app.models.pointage import Pointage
from app.schemas.pointage import PointageFacialCreate, PointageQrBadgeCreate, PointageWebAuthnCreate
from app.services import (
    anomalie_service,
    empreinte_service,
    horaire_service,
    journal_audit_service,
    parametre_service,
    webauthn_service,
)

# Motifs de sortie considérés comme exceptionnels (donc pas une simple sortie
# de fin de service) : un départ avant l'heure de référence pour l'un de ces
# motifs ne doit pas rester une anomalie "en attente" à traiter par la
# secrétaire — elle est auto-justifiée à la volée, cf. `_detecter_anomalie_horaire`.
_MOTIFS_SORTIE_EXCEPTIONNELS = {
    MotifSortie.URGENCE,
    MotifSortie.RAISON_FAMILIALE,
    MotifSortie.RAISON_MEDICALE,
    MotifSortie.AUTORISATION_HIERARCHIE,
    MotifSortie.AUTRE,
}

_LIBELLES_MOTIF_SORTIE = {
    MotifSortie.URGENCE: "Sortie urgente déclarée au poste de pointage",
    MotifSortie.RAISON_FAMILIALE: "Sortie pour cas familial déclarée au poste de pointage",
    MotifSortie.RAISON_MEDICALE: "Sortie pour raison médicale déclarée au poste de pointage",
    MotifSortie.AUTORISATION_HIERARCHIE: "Sortie autorisée par la hiérarchie, déclarée au poste de pointage",
    MotifSortie.AUTRE: "Sortie exceptionnelle déclarée au poste de pointage",
}

# Écart minimal exigé, en identification 1:N, entre la distance du meilleur
# candidat et celle du deuxième meilleur, pour trancher sans ambiguïté entre
# deux agents dont les visages se ressemblent. Valeur empirique modeste :
# à affiner selon les retours terrain une fois le mode 1:N en usage réel.
_MARGE_AMBIGUITE_FACIALE = 0.05

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

def _dernier_pointage_valide_du_jour(db: Session, id_agent: int, instant: datetime) -> Optional[Pointage]:
    debut_jour = datetime.combine(instant.date(), datetime.min.time())
    fin_jour = datetime.combine(instant.date(), datetime.max.time())
    stmt = (
        select(Pointage)
        .where(
            Pointage.id_agent == id_agent,
            Pointage.statut == StatutPointage.VALIDE,
            Pointage.date_heure.between(debut_jour, fin_jour),
        )
        .order_by(Pointage.date_heure.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _pointage_deja_enregistre_aujourdhui(db: Session, id_agent: int, type_pointage: TypePointage, instant: datetime) -> bool:
    """
    Empêche un pointage incohérent avec la séquence de la journée — et non
    plus un simple comptage "déjà un pointage de ce type aujourd'hui", qui
    empêchait à tort une nouvelle entrée après une sortie exceptionnelle
    (urgence, cas familial, raison médicale, autorisation de la
    hiérarchie...) alors que l'agent est censé revenir dans la journée.

    Règles :
      - ENTRÉE : refusée seulement si l'agent est déjà "dedans" (le dernier
        pointage validé du jour est une entrée sans sortie depuis), ou si sa
        journée est déjà officiellement terminée (dernier pointage = sortie
        "normale", fin de service — pas de retour attendu).
      - SORTIE : refusée seulement si l'agent est déjà "dehors" (le dernier
        pointage validé du jour est déjà une sortie, quel qu'en soit le
        motif — on ne peut pas sortir deux fois sans être rentré entre-temps).
    """
    dernier = _dernier_pointage_valide_du_jour(db, id_agent, instant)
    if dernier is None:
        return False

    if type_pointage == TypePointage.ENTREE:
        if dernier.type_pointage == TypePointage.ENTREE:
            return True  # déjà "dedans", pas encore ressorti
        # dernier est une sortie : une nouvelle entrée n'est autorisée que si
        # cette sortie était exceptionnelle (l'agent revient dans la journée).
        # Une sortie "normale" (ou sans motif renseigné, cas historique)
        # clôture la journée : pas de nouvelle entrée attendue.
        return dernier.motif_sortie is None or dernier.motif_sortie == MotifSortie.NORMALE

    # type_pointage == SORTIE
    return dernier.type_pointage == TypePointage.SORTIE


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


def identifier_par_visage(db: Session, vecteur_capture: List[float]) -> Agent:
    """
    Identification 1:N : compare le vecteur facial capté à l'empreinte de
    TOUS les agents actifs ayant consenti à la reconnaissance faciale et
    disposant d'une empreinte enregistrée, et retourne l'agent le plus
    proche — à condition que cette distance minimale reste sous le seuil
    configuré ET qu'elle se détache clairement du deuxième meilleur candidat
    (cf. `_MARGE_AMBIGUITE_FACIALE` ci-dessous), pour éviter de trancher à
    tort entre deux agents dont les empreintes sont proches.

    Utilisée quand le poste de pointage facial n'a pas fait saisir de
    matricule au préalable (cf. PointageFacialCreate, matricule/id_agent
    optionnels) : c'est la capture elle-même qui détermine l'identité,
    contrairement à `_identite_verifiee` qui vérifie une identité déjà
    présumée (1:1).
    """
    stmt = select(Agent).where(
        Agent.statut == StatutAgent.ACTIF,
        Agent.consentement_facial.is_(True),
    ).options(joinedload(Agent.empreinte_biometrique))
    agents = db.execute(stmt).unique().scalars().all()

    seuil = parametre_service.get_float(db, "seuil_distance_faciale", default=0.6)

    candidats: List[Tuple[Agent, float]] = []
    for agent in agents:
        if agent.empreinte_biometrique is None:
            continue
        empreinte_reference = empreinte_service.decoder_vecteur(agent.empreinte_biometrique.encodage_facial)
        try:
            distance = _distance_euclidienne(vecteur_capture, empreinte_reference)
        except HTTPException:
            continue  # empreinte de dimension incompatible : on l'ignore plutôt que de planter la recherche
        candidats.append((agent, distance))

    if not candidats:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité non vérifiée : aucun agent correspondant au visage capté.",
        )

    candidats.sort(key=lambda c: c[1])
    meilleur_agent, meilleure_distance = candidats[0]

    if meilleure_distance > seuil:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité non vérifiée : aucun agent correspondant au visage capté.",
        )

    if len(candidats) > 1:
        _, deuxieme_distance = candidats[1]
        if deuxieme_distance - meilleure_distance < _MARGE_AMBIGUITE_FACIALE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identité ambiguë : plusieurs agents correspondent de façon trop proche au visage "
                "capté. Merci de réessayer (meilleur cadrage/éclairage) ou de pointer par matricule.",
            )

    return meilleur_agent


# --------------------------------------------------------------------
# Calcul d'anomalie horaire (étape 14) — retard / départ anticipé
# --------------------------------------------------------------------

def _ajouter_minutes(heure, minutes: int):
    reference = datetime.combine(date_.today(), heure) + timedelta(minutes=minutes)
    return reference.time()


def _entree_apres_sortie_exceptionnelle(db: Session, agent: Agent, pointage: Pointage) -> bool:
    """
    Une entrée qui fait suite, le même jour, à une sortie exceptionnelle
    (urgence, raison familiale, raison médicale, autorisation de la
    hiérarchie... — cf. `_MOTIFS_SORTIE_EXCEPTIONNELS`) est le retour de
    l'agent après une absence déjà autorisée, pas une arrivée tardive au
    poste : elle ne doit donc jamais être qualifiée de "retard", quelle que
    soit l'heure de ce retour. Seule une sortie "normale" (fin de service)
    ferme la journée sans attendre de retour ; les autres motifs de sortie
    exonèrent l'entrée suivante de toute détection de retard.
    """
    debut_jour = datetime.combine(pointage.date_heure.date(), datetime.min.time())
    stmt = (
        select(Pointage)
        .where(
            Pointage.id_agent == agent.id_agent,
            Pointage.type_pointage == TypePointage.SORTIE,
            Pointage.statut == StatutPointage.VALIDE,
            Pointage.date_heure >= debut_jour,
            Pointage.date_heure < pointage.date_heure,
        )
        .order_by(Pointage.date_heure.desc())
        .limit(1)
    )
    derniere_sortie = db.execute(stmt).scalar_one_or_none()
    return (
        derniere_sortie is not None
        and derniere_sortie.motif_sortie is not None
        and derniere_sortie.motif_sortie in _MOTIFS_SORTIE_EXCEPTIONNELS
    )


def _detecter_anomalie_horaire(db: Session, agent: Agent, pointage: Pointage) -> Optional[Anomalie]:
    if agent.id_service is None:
        return None  # pas de service principal -> pas d'horaire de référence exploitable

    jour = _JOURS_PAR_INDEX[pointage.date_heure.weekday()]
    horaire = horaire_service.horaire_effectif(db, agent.id_service, jour)
    if horaire is None:
        return None  # jour non travaillé (ni horaire configuré, ni jour ouvré par défaut)
    heure_debut, heure_fin = horaire

    heure_pointage = pointage.date_heure.time()
    type_anomalie: Optional[TypeAnomalie] = None

    if pointage.type_pointage == TypePointage.ENTREE:
        if _entree_apres_sortie_exceptionnelle(db, agent, pointage):
            return None  # retour après sortie exceptionnelle : jamais un retard
        seuil_minutes = parametre_service.get_int(db, "seuil_retard_minutes", default=15)
        limite = _ajouter_minutes(heure_debut, seuil_minutes)
        if heure_pointage > limite:
            type_anomalie = TypeAnomalie.RETARD
    else:  # SORTIE
        if heure_pointage < heure_fin:
            type_anomalie = TypeAnomalie.DEPART_ANTICIPE

    if type_anomalie is None:
        return None

    anomalie = Anomalie(id_agent=agent.id_agent, id_pointage=pointage.id_pointage, type_anomalie=type_anomalie)
    db.add(anomalie)
    db.commit()
    db.refresh(anomalie)

    # Sortie exceptionnelle déclarée (urgence, cas familial, raison médicale,
    # autorisation de la hiérarchie...) : le départ anticipé qui en résulte est
    # légitime et déjà expliqué par l'agent au poste de pointage. On l'auto-
    # justifie immédiatement (statut JUSTIFIEE, sans alerte ni file d'attente
    # pour la secrétaire), tout en conservant la traçabilité complète (motif +
    # commentaire éventuel dans le justificatif).
    if (
        type_anomalie == TypeAnomalie.DEPART_ANTICIPE
        and pointage.motif_sortie is not None
        and pointage.motif_sortie in _MOTIFS_SORTIE_EXCEPTIONNELS
    ):
        motif_libelle = _LIBELLES_MOTIF_SORTIE[pointage.motif_sortie]
        if pointage.commentaire:
            motif_libelle = f"{motif_libelle} — {pointage.commentaire}"
        justificatif = Justificatif(id_anomalie=anomalie.id_anomalie, motif=motif_libelle)
        db.add(justificatif)
        anomalie.statut_justification = StatutJustification.JUSTIFIEE
        db.commit()
        db.refresh(anomalie)
        journal_audit_service.log_action(
            db,
            id_utilisateur=None,
            action="sortie_exceptionnelle_auto_justifiee",
            details=(
                f"agent={agent.matricule} anomalie={anomalie.id_anomalie} "
                f"motif_sortie={pointage.motif_sortie.value}"
            ),
        )
        return anomalie

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
    details = (
        f"agent={agent.matricule} type={pointage.type_pointage.value} "
        f"mode={pointage.mode_pointage.value} statut={pointage.statut.value}"
    )
    if pointage.motif_sortie is not None:
        details += f" motif_sortie={pointage.motif_sortie.value}"
    journal_audit_service.log_action(
        db,
        id_utilisateur=None,
        action="pointage",
        details=details,
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
        motif_sortie=payload.motif_sortie if payload.type_pointage == TypePointage.SORTIE else None,
        commentaire=payload.commentaire if payload.type_pointage == TypePointage.SORTIE else None,
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
    identifiant_fourni = bool(payload.matricule) or payload.id_agent is not None

    if identifiant_fourni:
        # Vérification 1:1 (comportement historique) : l'identité est
        # présumée par le matricule/id_agent transmis, puis confirmée par
        # comparaison biométrique contre l'empreinte de CET agent uniquement.
        agent = _agent_ou_404(db, payload.matricule, payload.id_agent)
        _verifier_agent_actif(agent)

        if not agent.consentement_facial or agent.empreinte_biometrique is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pointage par reconnaissance faciale indisponible pour cet agent "
                "(consentement non donné ou empreinte non enregistrée).",
            )

        identite_ok = _identite_verifiee(db, agent, payload.encodage_facial)
        if not identite_ok:
            pointage_rejete = Pointage(
                id_agent=agent.id_agent,
                date_heure=datetime.now(),
                type_pointage=payload.type_pointage,
                mode_pointage=ModePointage.FACIAL,
                statut=StatutPointage.REJETE,
            )
            _enregistrer_et_journaliser(db, agent, pointage_rejete)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identité non vérifiée : le visage capté ne correspond pas à l'empreinte enregistrée.",
            )
    else:
        # Identification 1:N : aucun matricule saisi au poste de pointage —
        # c'est la capture faciale elle-même qui détermine qui est l'agent,
        # par comparaison à l'ensemble des empreintes enregistrées.
        agent = identifier_par_visage(db, payload.encodage_facial)
        _verifier_agent_actif(agent)

    maintenant = datetime.now()
    doublon = _pointage_deja_enregistre_aujourdhui(db, agent.id_agent, payload.type_pointage, maintenant)
    pointage = Pointage(
        id_agent=agent.id_agent,
        date_heure=maintenant,
        type_pointage=payload.type_pointage,
        mode_pointage=ModePointage.FACIAL,
        statut=StatutPointage.DOUBLON if doublon else StatutPointage.VALIDE,
        motif_sortie=payload.motif_sortie if payload.type_pointage == TypePointage.SORTIE else None,
        commentaire=payload.commentaire if payload.type_pointage == TypePointage.SORTIE else None,
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
        motif_sortie=payload.motif_sortie if payload.type_pointage == TypePointage.SORTIE else None,
        commentaire=payload.commentaire if payload.type_pointage == TypePointage.SORTIE else None,
    )
    pointage = _enregistrer_et_journaliser(db, agent, pointage)

    if doublon:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Un pointage '{payload.type_pointage.value}' est déjà enregistré aujourd'hui pour cet agent.",
        )

    anomalie = _detecter_anomalie_horaire(db, agent, pointage)
    return pointage, anomalie