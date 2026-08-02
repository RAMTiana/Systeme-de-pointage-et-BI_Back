"""
Service métier — Module Absences (registre local, sans circuit d'approbation,
identique dans son principe au module Congés).

Ajouté pour éviter qu'une absence déjà connue de l'administration centrale
soit signalée à tort comme anomalie locale : `anomalie_service.detecter_absences`
ne pointe jamais un agent couvert par un enregistrement ici, ce qui
déclencherait sinon une anomalie 'absence' — et donc systématiquement une
alerte à la hiérarchie (cf. `anomalie_service._alerte_necessaire`) — pour un
motif d'absence pourtant déjà remonté au niveau national.

Pas de circuit d'approbation ici : l'administration centrale dispose déjà de
sa propre application de gestion des absences de tous les agents publics,
commune à tous les ministères — le SRB Haute Matsiatra n'est qu'un service
parmi d'autres, pas l'autorité de validation. Ce module se contente
d'enregistrer localement une absence déjà connue/justifiée ailleurs, pour que
la détection d'absence en tienne compte.

Workflow (identique à `conge_service`) :
  1. La Secrétaire enregistre l'absence une fois qu'elle en a connaissance
     (`creer`), statut ACTIF dès la création.
  2. `agents_en_absence` (utilisé par le job quotidien de détection des
     absences) et `est_en_absence` s'appuient uniquement sur ce statut.
  3. Enregistrer une absence justifie aussi rétroactivement les anomalies
     'absence' déjà consignées sur la période couverte
     (`_justifier_absences_couvertes`) — utile quand l'absence est saisie
     après coup, une fois déjà détectée par le job de la veille.
  4. La Secrétaire peut annuler une absence saisie par erreur (`annuler`).
"""
from datetime import date as date_
from datetime import datetime
from typing import List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.absence import Absence
from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import StatutAbsence, StatutJustification, TypeAnomalie
from app.models.justificatif import Justificatif
from app.services import agent_service, journal_audit_service


# --------------------------------------------------------------------
# Enregistrement et consultation
# --------------------------------------------------------------------

def _justifier_absences_couvertes(db: Session, absence: Absence, id_utilisateur: int) -> int:
    """
    Marque comme justifiées les anomalies 'absence' déjà consignées pour cet
    agent sur la période qui vient d'être enregistrée — cas d'une absence
    saisie après coup, alors que le job quotidien l'avait déjà détectée la
    veille. Retourne le nombre d'anomalies ainsi régularisées.
    """
    stmt = select(Anomalie).where(
        Anomalie.id_agent == absence.id_agent,
        Anomalie.type_anomalie == TypeAnomalie.ABSENCE,
        Anomalie.statut_justification != StatutJustification.JUSTIFIEE,
        Anomalie.date_detection >= datetime.combine(absence.date_debut, datetime.min.time()),
        Anomalie.date_detection <= datetime.combine(absence.date_fin, datetime.max.time()),
    )
    anomalies = list(db.execute(stmt).scalars().all())

    for anomalie in anomalies:
        db.add(Justificatif(
            id_anomalie=anomalie.id_anomalie,
            motif=(
                f"Absence déjà enregistrée au niveau central "
                f"({absence.date_debut.isoformat()} → {absence.date_fin.isoformat()})."
                + (f" {absence.motif}" if absence.motif else "")
            ),
        ))
        anomalie.statut_justification = StatutJustification.JUSTIFIEE
        anomalie.id_utilisateur_traitant = id_utilisateur

    return len(anomalies)


def creer(
    db: Session,
    id_agent: int,
    date_debut: date_,
    date_fin: date_,
    id_utilisateur_saisie: int,
    motif: Optional[str] = None,
) -> Absence:
    agent_service.get_by_id_or_404(db, id_agent)  # 404 si l'agent n'existe pas

    if date_fin < date_debut:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La date de fin doit être postérieure ou égale à la date de début.",
        )

    absence = Absence(
        id_agent=id_agent,
        date_debut=date_debut,
        date_fin=date_fin,
        motif=motif,
        id_utilisateur_saisie=id_utilisateur_saisie,
    )
    db.add(absence)
    db.flush()

    # Statut ACTIF dès la création (pas d'étape d'approbation locale) : les
    # anomalies 'absence' déjà consignées sur la période sont donc
    # régularisées immédiatement.
    nb_absences_regularisees = _justifier_absences_couvertes(db, absence, id_utilisateur_saisie)

    db.commit()
    db.refresh(absence)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur_saisie,
        action="creation_absence",
        details=(
            f"absence={absence.id_absence} agent={id_agent} "
            f"{date_debut}->{date_fin} anomalies_regularisees={nb_absences_regularisees}"
        ),
    )
    return absence


def get_by_id_or_404(db: Session, id_absence: int) -> Absence:
    stmt = (
        select(Absence)
        .options(joinedload(Absence.agent))
        .where(Absence.id_absence == id_absence)
    )
    absence = db.execute(stmt).unique().scalar_one_or_none()
    if absence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Absence introuvable.")
    return absence


def lister(
    db: Session,
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    statut: Optional[StatutAbsence] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Absence], int]:
    stmt = select(Absence).options(joinedload(Absence.agent))
    if id_service is not None:
        stmt = stmt.join(Agent, Absence.id_agent == Agent.id_agent).where(Agent.id_service == id_service)

    conditions = []
    if id_agent is not None:
        conditions.append(Absence.id_agent == id_agent)
    if statut is not None:
        conditions.append(Absence.statut == statut)
    # Chevauchement avec [date_debut, date_fin] plutôt qu'une égalité stricte :
    # une absence qui déborde de la fenêtre demandée doit quand même apparaître.
    if date_debut is not None:
        conditions.append(Absence.date_fin >= date_debut)
    if date_fin is not None:
        conditions.append(Absence.date_debut <= date_fin)

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Absence.id_absence).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Absence.date_debut.desc()).offset(skip).limit(limit)
    absences = list(db.execute(stmt).unique().scalars().all())

    return absences, total


def annuler(db: Session, id_absence: int, id_utilisateur: int) -> Absence:
    """
    Annulation par la Secrétaire (ex. erreur de saisie) : possible tant que
    l'absence est encore active. N'annule pas la justification déjà
    accordée à des anomalies passées (une régularisation déjà actée n'est
    pas remise en cause rétroactivement).
    """
    absence = get_by_id_or_404(db, id_absence)
    if absence.statut == StatutAbsence.ANNULE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cette absence est déjà annulée.",
        )

    absence.statut = StatutAbsence.ANNULE
    db.commit()
    db.refresh(absence)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur,
        action="annulation_absence",
        details=f"absence={absence.id_absence} agent={absence.id_agent}",
    )
    return absence


# --------------------------------------------------------------------
# Consultation utilisée par la détection d'absences
# (`anomalie_service.detecter_absences`)
# --------------------------------------------------------------------

def agents_en_absence(db: Session, jour: date_, ids_agents: Optional[List[int]] = None) -> Set[int]:
    """
    Ensemble des `id_agent` couverts par un enregistrement ACTIF sur `jour`.
    Requête unique (plutôt qu'un appel par agent) pour rester efficace dans
    la boucle du job quotidien `anomalie_service.detecter_absences`.
    """
    stmt = select(Absence.id_agent).where(
        Absence.statut == StatutAbsence.ACTIF,
        Absence.date_debut <= jour,
        Absence.date_fin >= jour,
    )
    if ids_agents is not None:
        stmt = stmt.where(Absence.id_agent.in_(ids_agents))
    return set(db.execute(stmt).scalars().all())


def est_en_absence(db: Session, id_agent: int, jour: date_) -> bool:
    return id_agent in agents_en_absence(db, jour, ids_agents=[id_agent])
