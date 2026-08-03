"""
Service métier — Module Rapports (Processus 4 du BPMN "Génération de rapports").

Couvre les étapes du diagramme :
  - étapes 1/2 : déclenchement planifié (Timer) ou à la demande (None Start Event)
  - étape 3-5  : collecte et agrégation des données de pointage/anomalies sur
                 la période demandée
  - étape 6    : génération du document (connecteur d'export PDF/Excel —
                 reportlab / openpyxl)
  - étape 7    : consignation du rapport en base (table `rapport`)
  - étape 11   : mise à disposition pour consultation (téléchargement)

Les indicateurs sont calculés à partir des tables déjà alimentées par les
Processus 1 et 3 (`pointage`, `anomalie`), conformément au principe de
conception BPMN "Processus 1 et 3 alimentent les Processus 4 et 5" (pas de
duplication de données, uniquement de l'agrégation en lecture).
"""
import os
import re
from datetime import date as date_
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as canvas_mod
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core import jours_feries
from app.core.config import settings
from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.conge import Conge
from app.models.enums import FormatRapport, JourSemaine, StatutAgent, StatutConge, StatutPointage, TypeAnomalie, \
    TypePeriode, TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.models.rapport import Rapport
from app.models.service import Service
from app.services import horaire_service, journal_audit_service
from app.services.rapport_commun import (
    _AMBRE,
    _AMBRE_HEX,
    _BANNIERE_H,
    _BLANC,
    _BLEU,
    _BLEU_HEX,
    _CORAIL,
    _CORAIL_HEX,
    _GRIS_BORD,
    _GRIS_BORD_HEX,
    _GRIS_ZEBRE,
    _GRIS_ZEBRE_HEX,
    _JOURS_PAR_INDEX,
    _LIBELLE_PERIODE,
    _MARGE,
    _NOM_ORG,
    _PAGE_H,
    _PAGE_W,
    _PIED_H,
    _SOUS_NOM_ORG,
    _TEAL,
    _TEAL_HEX,
    _TEXTE,
    _TEXTE_HEX,
    _TEXTE_MUT,
    _TEXTE_MUT_HEX,
    _hex_argb,
)
from app.services.rapport_excel import _rendre_excel
from app.services.rapport_indicateurs import (
    agreger_indicateurs,
    bornes_periode,
    calculer_indicateurs,
    compter_anomalies_par_type,
    heures_travaillees_agent,
    indicateurs_agent,
    jours_conge_agent,
    jours_ouvres_service,
    jours_pointes_agent,
)
from app.services.rapport_pdf import _rendre_pdf

# --------------------------------------------------------------------
# Étape 6 : génération du document (connecteurs d'export PDF / Excel)
# --------------------------------------------------------------------

def _nom_fichier(type_periode: TypePeriode, format_rapport: FormatRapport, id_service: Optional[int], date_debut: date_) -> str:
    perimetre = f"service-{id_service}" if id_service is not None else "global"
    extension = "pdf" if format_rapport == FormatRapport.PDF else "xlsx"
    return f"rapport_{_LIBELLE_PERIODE[type_periode]}_{date_debut.isoformat()}_{perimetre}.{extension}"


def generer_rapport(
    db: Session,
    type_periode: TypePeriode,
    format_rapport: FormatRapport,
    id_service: Optional[int] = None,
    id_utilisateur: Optional[int] = None,
    date_reference: Optional[date_] = None,
) -> Rapport:
    """
    Orchestre les étapes 3 à 7 du Processus 4 : calcule les indicateurs,
    génère le fichier (PDF ou Excel) sur disque, puis consigne le rapport en
    base. `id_utilisateur` est None lorsque la génération est déclenchée par
    le job planifié (Timer Start Event) plutôt que par un utilisateur du
    back-office.
    """
    indicateurs = calculer_indicateurs(db, type_periode, id_service, date_reference)

    dossier_absolu = os.path.abspath(settings.REPORTS_DIR)
    os.makedirs(dossier_absolu, exist_ok=True)
    nom_fichier = _nom_fichier(type_periode, format_rapport, id_service, indicateurs["periode_debut"])
    chemin_absolu = os.path.join(dossier_absolu, nom_fichier)
    chemin_relatif = os.path.join(settings.REPORTS_DIR, nom_fichier)

    if format_rapport == FormatRapport.PDF:
        _rendre_pdf(chemin_absolu, indicateurs)
    else:
        _rendre_excel(chemin_absolu, indicateurs)

    rapport = Rapport(
        id_utilisateur=id_utilisateur,
        id_service=id_service,
        type_periode=type_periode,
        format=format_rapport,
        chemin_fichier=chemin_relatif,
    )
    db.add(rapport)
    db.commit()
    db.refresh(rapport)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur,
        action="generation_rapport",
        details=(
            f"rapport={rapport.id_rapport} type_periode={type_periode.value} format={format_rapport.value} "
            f"service={id_service if id_service is not None else 'tous'} "
            f"periode={indicateurs['periode_debut'].isoformat()}..{indicateurs['periode_fin'].isoformat()}"
        ),
    )
    return rapport


def generer_rapports_planifies(
    db: Session,
    type_periode: TypePeriode,
    formats: List[FormatRapport],
    date_reference: Optional[date_] = None,
) -> List[Rapport]:
    """
    Étapes 1-7 côté Timer Start Event : un rapport consolidé (tous services)
    plus un rapport par service ayant au moins un agent actif, pour chacun
    des formats demandés. Destiné à un ordonnanceur externe (cron), au même
    titre que `anomalie_service.detecter_absences`.
    """
    ids_services_actifs = list(db.execute(
        select(Agent.id_service).where(Agent.statut == StatutAgent.ACTIF, Agent.id_service.is_not(None)).distinct()
    ).scalars().all())

    perimetres: List[Optional[int]] = [None] + ids_services_actifs
    rapports: List[Rapport] = []
    for id_service in perimetres:
        for format_rapport in formats:
            rapports.append(generer_rapport(
                db, type_periode, format_rapport, id_service=id_service, id_utilisateur=None,
                date_reference=date_reference,
            ))
    return rapports


# --------------------------------------------------------------------
# Consultation (étape 11, non bloquante)
# --------------------------------------------------------------------

def bornes_depuis_rapport(rapport: Rapport) -> Tuple[Optional[date_], Optional[date_]]:
    """
    La table `rapport` (schéma d'origine) ne conserve pas les bornes de la
    période couverte, seulement `date_generation` — qui peut différer de la
    période elle-même (ex. rapport mensuel généré le 1er du mois suivant).
    `_nom_fichier` encode la date de début de période dans le nom du fichier ;
    comme `bornes_periode` est déterministe et idempotente sur une date de
    début canonique (lundi de semaine, 1er du mois, 1er janvier...), la borne
    de fin s'en déduit exactement sans avoir à modifier le schéma de données.
    """
    correspondance = re.search(r"_(\d{4}-\d{2}-\d{2})_", rapport.chemin_fichier)
    if not correspondance:
        return None, None
    try:
        date_debut = date_.fromisoformat(correspondance.group(1))
    except ValueError:
        return None, None
    return bornes_periode(rapport.type_periode, date_debut)


def get_by_id_or_404(db: Session, id_rapport: int) -> Rapport:
    stmt = select(Rapport).options(joinedload(Rapport.service)).where(Rapport.id_rapport == id_rapport)
    rapport = db.execute(stmt).unique().scalar_one_or_none()
    if rapport is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rapport introuvable.")
    return rapport


def lister_rapports(
    db: Session,
    type_periode: Optional[TypePeriode] = None,
    format_rapport: Optional[FormatRapport] = None,
    id_service: Optional[int] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Rapport], int]:
    stmt = select(Rapport).options(joinedload(Rapport.service))

    conditions = []
    if type_periode is not None:
        conditions.append(Rapport.type_periode == type_periode)
    if format_rapport is not None:
        conditions.append(Rapport.format == format_rapport)
    if id_service is not None:
        conditions.append(Rapport.id_service == id_service)
    if date_debut is not None:
        conditions.append(Rapport.date_generation >= datetime.combine(date_debut, datetime.min.time()))
    if date_fin is not None:
        conditions.append(Rapport.date_generation <= datetime.combine(date_fin, datetime.max.time()))

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Rapport.id_rapport).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Rapport.date_generation.desc()).offset(skip).limit(limit)
    rapports = list(db.execute(stmt).unique().scalars().all())

    return rapports, total
