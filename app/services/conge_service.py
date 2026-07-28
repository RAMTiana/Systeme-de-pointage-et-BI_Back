"""
Service métier — Module Congés.

Ajouté pour éviter qu'un agent en congé (annuel, maladie, maternité...) soit
signalé à tort comme absent : `anomalie_service.detecter_absences` ne pointe
jamais un agent en congé, ce qui déclenchait auparavant une anomalie
'absence' — donc potentiellement une alerte à la hiérarchie — pour un motif
d'absence pourtant parfaitement légitime.

Pas de circuit d'approbation ici : la fonction publique malgache dispose
déjà d'un système national dédié aux demandes de congé/absence, commun à
tous les ministères — le SRB Haute Matsiatra n'est qu'un service parmi
d'autres, pas l'autorité de validation. Ce module se contente d'enregistrer
localement un congé déjà confirmé, pour que la détection d'absence en tienne
compte.

Workflow :
  1. La Secrétaire enregistre le congé une fois confirmé (`creer`), statut
     ACTIF dès la création.
  2. `agents_en_conge` (utilisé par le job quotidien de détection des
     absences) et `est_en_conge` s'appuient uniquement sur ce statut.
  3. Enregistrer un congé justifie aussi rétroactivement les anomalies
     'absence' déjà consignées sur la période couverte
     (`_justifier_absences_couvertes`) — utile quand le congé (ex. arrêt
     maladie) est saisi après coup, une fois l'absence déjà détectée par le
     job de la veille.
  4. La Secrétaire peut annuler un congé saisi par erreur (`annuler`).
"""
from datetime import date as date_
from datetime import datetime
from typing import List, Optional, Set, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.conge import Conge
from app.models.enums import StatutConge, StatutJustification, TypeAnomalie, TypeConge
from app.models.justificatif import Justificatif
from app.services import agent_service, journal_audit_service

_LIBELLES_TYPE_CONGE = {
    TypeConge.CONGE_ANNUEL: "Congé annuel",
    TypeConge.MALADIE: "Congé maladie",
    TypeConge.MATERNITE: "Congé maternité",
    TypeConge.PATERNITE: "Congé paternité",
    TypeConge.EVENEMENT_FAMILIAL: "Événement familial",
    TypeConge.SANS_SOLDE: "Congé sans solde",
    TypeConge.AUTRE: "Congé",
}


# --------------------------------------------------------------------
# Enregistrement et consultation
# --------------------------------------------------------------------

def _justifier_absences_couvertes(db: Session, conge: Conge, id_utilisateur: int) -> int:
    """
    Marque comme justifiées les anomalies 'absence' déjà consignées pour cet
    agent sur la période du congé qui vient d'être enregistré — cas d'un
    congé saisi après coup (ex. arrêt maladie transmis avec retard), alors
    que le job quotidien avait déjà détecté l'absence la veille. Retourne le
    nombre d'anomalies ainsi régularisées.
    """
    stmt = select(Anomalie).where(
        Anomalie.id_agent == conge.id_agent,
        Anomalie.type_anomalie == TypeAnomalie.ABSENCE,
        Anomalie.statut_justification != StatutJustification.JUSTIFIEE,
        Anomalie.date_detection >= datetime.combine(conge.date_debut, datetime.min.time()),
        Anomalie.date_detection <= datetime.combine(conge.date_fin, datetime.max.time()),
    )
    anomalies = list(db.execute(stmt).scalars().all())

    libelle = _LIBELLES_TYPE_CONGE.get(conge.type_conge, "Congé")
    for anomalie in anomalies:
        db.add(Justificatif(
            id_anomalie=anomalie.id_anomalie,
            motif=f"{libelle} ({conge.date_debut.isoformat()} → {conge.date_fin.isoformat()}).",
        ))
        anomalie.statut_justification = StatutJustification.JUSTIFIEE
        anomalie.id_utilisateur_traitant = id_utilisateur

    return len(anomalies)


def creer(
    db: Session,
    id_agent: int,
    type_conge: TypeConge,
    date_debut: date_,
    date_fin: date_,
    id_utilisateur_saisie: int,
    motif: Optional[str] = None,
) -> Conge:
    agent_service.get_by_id_or_404(db, id_agent)  # 404 si l'agent n'existe pas

    if date_fin < date_debut:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La date de fin doit être postérieure ou égale à la date de début.",
        )

    conge = Conge(
        id_agent=id_agent,
        type_conge=type_conge,
        date_debut=date_debut,
        date_fin=date_fin,
        motif=motif,
        id_utilisateur_saisie=id_utilisateur_saisie,
    )
    db.add(conge)
    db.flush()

    # Statut ACTIF dès la création (pas d'étape d'approbation locale) : les
    # anomalies 'absence' déjà consignées sur la période sont donc
    # régularisées immédiatement.
    nb_absences_regularisees = _justifier_absences_couvertes(db, conge, id_utilisateur_saisie)

    db.commit()
    db.refresh(conge)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur_saisie,
        action="creation_conge",
        details=(
            f"conge={conge.id_conge} agent={id_agent} type={type_conge.value} "
            f"{date_debut}->{date_fin} absences_regularisees={nb_absences_regularisees}"
        ),
    )
    return conge


def get_by_id_or_404(db: Session, id_conge: int) -> Conge:
    stmt = (
        select(Conge)
        .options(joinedload(Conge.agent))
        .where(Conge.id_conge == id_conge)
    )
    conge = db.execute(stmt).unique().scalar_one_or_none()
    if conge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Congé introuvable.")
    return conge


def lister(
    db: Session,
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    statut: Optional[StatutConge] = None,
    type_conge: Optional[TypeConge] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Conge], int]:
    stmt = select(Conge).options(joinedload(Conge.agent))
    if id_service is not None:
        stmt = stmt.join(Agent, Conge.id_agent == Agent.id_agent).where(Agent.id_service == id_service)

    conditions = []
    if id_agent is not None:
        conditions.append(Conge.id_agent == id_agent)
    if statut is not None:
        conditions.append(Conge.statut == statut)
    if type_conge is not None:
        conditions.append(Conge.type_conge == type_conge)
    # Chevauchement avec [date_debut, date_fin] plutôt qu'une égalité stricte :
    # un congé qui déborde de la fenêtre demandée doit quand même apparaître.
    if date_debut is not None:
        conditions.append(Conge.date_fin >= date_debut)
    if date_fin is not None:
        conditions.append(Conge.date_debut <= date_fin)

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Conge.id_conge).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Conge.date_debut.desc()).offset(skip).limit(limit)
    conges = list(db.execute(stmt).unique().scalars().all())

    return conges, total


def annuler(db: Session, id_conge: int, id_utilisateur: int) -> Conge:
    """
    Annulation par la Secrétaire (ex. erreur de saisie, congé finalement non
    pris) : possible tant que le congé est encore actif. N'annule pas la
    justification déjà accordée à des anomalies passées (une régularisation
    déjà actée n'est pas remise en cause rétroactivement).
    """
    conge = get_by_id_or_404(db, id_conge)
    if conge.statut == StatutConge.ANNULE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce congé est déjà annulé.",
        )

    conge.statut = StatutConge.ANNULE
    db.commit()
    db.refresh(conge)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur,
        action="annulation_conge",
        details=f"conge={conge.id_conge} agent={conge.id_agent}",
    )
    return conge


# --------------------------------------------------------------------
# Consultation utilisée par la détection d'absences
# (`anomalie_service.detecter_absences`)
# --------------------------------------------------------------------

def agents_en_conge(db: Session, jour: date_, ids_agents: Optional[List[int]] = None) -> Set[int]:
    """
    Ensemble des `id_agent` en congé ACTIF couvrant `jour`. Requête unique
    (plutôt qu'un appel par agent) pour rester efficace dans la boucle du job
    quotidien `anomalie_service.detecter_absences`.
    """
    stmt = select(Conge.id_agent).where(
        Conge.statut == StatutConge.ACTIF,
        Conge.date_debut <= jour,
        Conge.date_fin >= jour,
    )
    if ids_agents is not None:
        stmt = stmt.where(Conge.id_agent.in_(ids_agents))
    return set(db.execute(stmt).scalars().all())


def est_en_conge(db: Session, id_agent: int, jour: date_) -> bool:
    return id_agent in agents_en_conge(db, jour, ids_agents=[id_agent])
