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
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import FormatRapport, JourSemaine, StatutAgent, StatutPointage, TypeAnomalie, TypePeriode, \
    TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.models.rapport import Rapport
from app.models.service import Service
from app.services import journal_audit_service

_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]

_LIBELLE_PERIODE = {
    TypePeriode.JOUR: "journalier",
    TypePeriode.SEMAINE: "hebdomadaire",
    TypePeriode.MOIS: "mensuel",
    TypePeriode.ANNEE: "annuel",
}


# --------------------------------------------------------------------
# Bornes de la période (étape 1-2 : la variable typePeriode du Timer/
# formulaire détermine la fenêtre de calcul)
# --------------------------------------------------------------------

def bornes_periode(type_periode: TypePeriode, date_reference: date_) -> Tuple[date_, date_]:
    if type_periode == TypePeriode.JOUR:
        return date_reference, date_reference

    if type_periode == TypePeriode.SEMAINE:
        debut = date_reference - timedelta(days=date_reference.weekday())  # lundi de la semaine
        return debut, debut + timedelta(days=6)

    if type_periode == TypePeriode.MOIS:
        debut = date_reference.replace(day=1)
        if debut.month == 12:
            fin = debut.replace(year=debut.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            fin = debut.replace(month=debut.month + 1, day=1) - timedelta(days=1)
        return debut, fin

    # ANNEE
    return date_reference.replace(month=1, day=1), date_reference.replace(month=12, day=31)


# --------------------------------------------------------------------
# Étapes 3-5 : collecte et agrégation des indicateurs
# --------------------------------------------------------------------

def jours_ouvres_service(
    db: Session, id_service: Optional[int], date_debut: date_, date_fin: date_
) -> int:
    """
    Nombre de jours de la période où le service a un horaire de référence
    défini (jour effectivement travaillé) — sert de dénominateur au taux de
    présence. Un service sans aucun horaire (ou id_service=None, agent sans
    service) est considéré travaillé tous les jours de la période (pas de
    référence disponible pour exclure les week-ends).
    """
    stmt = select(HoraireReference.jour_semaine).where(HoraireReference.id_service == id_service).distinct()
    jours_travailles = set(db.execute(stmt).scalars().all())
    if not jours_travailles:
        return (date_fin - date_debut).days + 1

    total = 0
    jour = date_debut
    while jour <= date_fin:
        if _JOURS_PAR_INDEX[jour.weekday()] in jours_travailles:
            total += 1
        jour += timedelta(days=1)
    return total


def heures_travaillees_agent(db: Session, id_agent: int, date_debut: date_, date_fin: date_) -> float:
    """
    Pour chaque jour de la période, additionne l'écart entre le premier
    pointage d'entrée valide et le dernier pointage de sortie valide.
    Approximation volontairement simple (pas de gestion des pauses
    multiples intra-journée), suffisante pour un indicateur de synthèse.
    """
    stmt = select(Pointage).where(
        Pointage.id_agent == id_agent,
        Pointage.statut == StatutPointage.VALIDE,
        Pointage.date_heure >= datetime.combine(date_debut, datetime.min.time()),
        Pointage.date_heure <= datetime.combine(date_fin, datetime.max.time()),
    ).order_by(Pointage.date_heure)
    pointages = list(db.execute(stmt).scalars().all())

    par_jour: Dict[date_, Dict[str, datetime]] = {}
    for p in pointages:
        jour = p.date_heure.date()
        entree_sortie = par_jour.setdefault(jour, {})
        if p.type_pointage == TypePointage.ENTREE and "entree" not in entree_sortie:
            entree_sortie["entree"] = p.date_heure
        elif p.type_pointage == TypePointage.SORTIE:
            entree_sortie["sortie"] = p.date_heure  # conserve la dernière sortie du jour

    total_heures = 0.0
    for entree_sortie in par_jour.values():
        if "entree" in entree_sortie and "sortie" in entree_sortie and entree_sortie["sortie"] > entree_sortie["entree"]:
            total_heures += (entree_sortie["sortie"] - entree_sortie["entree"]).total_seconds() / 3600

    return round(total_heures, 2)


def compter_anomalies_par_type(
    db: Session, ids_agents: List[int], date_debut: date_, date_fin: date_
) -> Dict[Tuple[int, TypeAnomalie], int]:
    if not ids_agents:
        return {}
    stmt = select(Anomalie.id_agent, Anomalie.type_anomalie, func.count()).where(
        Anomalie.id_agent.in_(ids_agents),
        Anomalie.date_detection >= datetime.combine(date_debut, datetime.min.time()),
        Anomalie.date_detection <= datetime.combine(date_fin, datetime.max.time()),
    ).group_by(Anomalie.id_agent, Anomalie.type_anomalie)

    resultat: Dict[Tuple[int, TypeAnomalie], int] = {}
    for id_agent, type_anomalie, total in db.execute(stmt).all():
        resultat[(id_agent, type_anomalie)] = total
    return resultat


def indicateurs_agent(
    agent: Agent,
    jours_ouvres: int,
    heures_travaillees: float,
    compte_anomalies: Dict[Tuple[int, TypeAnomalie], int],
) -> dict:
    nombre_retards = compte_anomalies.get((agent.id_agent, TypeAnomalie.RETARD), 0)
    nombre_absences = compte_anomalies.get((agent.id_agent, TypeAnomalie.ABSENCE), 0)
    nombre_departs = compte_anomalies.get((agent.id_agent, TypeAnomalie.DEPART_ANTICIPE), 0)
    jours_presents = max(jours_ouvres - nombre_absences, 0)
    taux_presence = round(jours_presents / jours_ouvres, 4) if jours_ouvres > 0 else None

    return {
        "id_agent": agent.id_agent,
        "matricule": agent.matricule,
        "nom": agent.nom,
        "prenom": agent.prenom,
        "jours_ouvres": jours_ouvres,
        "jours_presents": jours_presents,
        "nombre_retards": nombre_retards,
        "nombre_absences": nombre_absences,
        "nombre_departs_anticipes": nombre_departs,
        "heures_travaillees": heures_travaillees,
        "taux_presence": taux_presence,
    }


def agreger_indicateurs(indicateurs_agents: List[dict]) -> dict:
    nombre_agents = len(indicateurs_agents)
    jours_ouvres = sum(a["jours_ouvres"] for a in indicateurs_agents)
    jours_presents = sum(a["jours_presents"] for a in indicateurs_agents)
    return {
        "nombre_agents": nombre_agents,
        "jours_ouvres": jours_ouvres,
        "jours_presents": jours_presents,
        "nombre_retards": sum(a["nombre_retards"] for a in indicateurs_agents),
        "nombre_absences": sum(a["nombre_absences"] for a in indicateurs_agents),
        "nombre_departs_anticipes": sum(a["nombre_departs_anticipes"] for a in indicateurs_agents),
        "heures_travaillees": round(sum(a["heures_travaillees"] for a in indicateurs_agents), 2),
        "taux_presence": round(jours_presents / jours_ouvres, 4) if jours_ouvres > 0 else None,
    }


def calculer_indicateurs(
    db: Session,
    type_periode: TypePeriode,
    id_service: Optional[int],
    date_reference: Optional[date_] = None,
) -> dict:
    """
    Étapes 3-5 du Processus 4 : calcule les indicateurs pour la période et le
    périmètre demandés.

    - `id_service` fourni  -> détail par agent du service (comparaison intra-service).
    - `id_service` = None  -> détail par service (vue consolidée multi-services,
      cf. besoin "Vue consolidée multi-services" du cahier des charges),
      sans descendre au niveau agent (réservé au module BI / rapport de service).
    """
    date_reference = date_reference or (date_.today() - timedelta(days=1))
    date_debut, date_fin = bornes_periode(type_periode, date_reference)

    nom_service = "Tous services"
    if id_service is not None:
        service = db.get(Service, id_service)
        if service is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service introuvable.")
        nom_service = service.nom_service

    if id_service is not None:
        agents = list(db.execute(
            select(Agent).where(Agent.id_service == id_service, Agent.statut == StatutAgent.ACTIF)
        ).scalars().all())
        ids_agents = [a.id_agent for a in agents]
        compte_anomalies = compter_anomalies_par_type(db, ids_agents, date_debut, date_fin)
        jours_ouvres = jours_ouvres_service(db, id_service, date_debut, date_fin)

        detail_agents = [
            indicateurs_agent(
                agent, jours_ouvres, heures_travaillees_agent(db, agent.id_agent, date_debut, date_fin), compte_anomalies
            )
            for agent in agents
        ]
        globaux = agreger_indicateurs(detail_agents) if detail_agents else {
            "nombre_agents": 0, "jours_ouvres": jours_ouvres, "jours_presents": 0,
            "nombre_retards": 0, "nombre_absences": 0, "nombre_departs_anticipes": 0,
            "heures_travaillees": 0.0, "taux_presence": None,
        }
        return {
            "type_periode": type_periode,
            "periode_debut": date_debut,
            "periode_fin": date_fin,
            "id_service": id_service,
            "nom_service": nom_service,
            "globaux": globaux,
            "detail_agents": detail_agents,
            "detail_services": [],
        }

    # Rapport consolidé : agrégation par service
    services = list(db.execute(select(Service)).scalars().all())
    detail_services: List[dict] = []
    tous_agents_indicateurs: List[dict] = []

    for service in services:
        agents = list(db.execute(
            select(Agent).where(Agent.id_service == service.id_service, Agent.statut == StatutAgent.ACTIF)
        ).scalars().all())
        if not agents:
            continue
        ids_agents = [a.id_agent for a in agents]
        compte_anomalies = compter_anomalies_par_type(db, ids_agents, date_debut, date_fin)
        jours_ouvres = jours_ouvres_service(db, service.id_service, date_debut, date_fin)

        indicateurs_agents_service = [
            indicateurs_agent(
                agent, jours_ouvres, heures_travaillees_agent(db, agent.id_agent, date_debut, date_fin), compte_anomalies
            )
            for agent in agents
        ]
        tous_agents_indicateurs.extend(indicateurs_agents_service)
        agrege = agreger_indicateurs(indicateurs_agents_service)
        detail_services.append({
            "id_service": service.id_service,
            "nom_service": service.nom_service,
            "nombre_agents": agrege["nombre_agents"],
            "jours_ouvres": jours_ouvres,
            "jours_presents": agrege["jours_presents"],
            "nombre_retards": agrege["nombre_retards"],
            "nombre_absences": agrege["nombre_absences"],
            "nombre_departs_anticipes": agrege["nombre_departs_anticipes"],
            "heures_travaillees": agrege["heures_travaillees"],
            "taux_presence": agrege["taux_presence"],
        })

    globaux = agreger_indicateurs(tous_agents_indicateurs) if tous_agents_indicateurs else {
        "nombre_agents": 0, "jours_ouvres": 0, "jours_presents": 0,
        "nombre_retards": 0, "nombre_absences": 0, "nombre_departs_anticipes": 0,
        "heures_travaillees": 0.0, "taux_presence": None,
    }

    return {
        "type_periode": type_periode,
        "periode_debut": date_debut,
        "periode_fin": date_fin,
        "id_service": None,
        "nom_service": nom_service,
        "globaux": globaux,
        "detail_agents": [],
        "detail_services": detail_services,
    }


# --------------------------------------------------------------------
# Étape 6 : génération du document (connecteurs d'export PDF / Excel)
# --------------------------------------------------------------------

def _nom_fichier(type_periode: TypePeriode, format_rapport: FormatRapport, id_service: Optional[int], date_debut: date_) -> str:
    perimetre = f"service-{id_service}" if id_service is not None else "global"
    extension = "pdf" if format_rapport == FormatRapport.PDF else "xlsx"
    return f"rapport_{_LIBELLE_PERIODE[type_periode]}_{date_debut.isoformat()}_{perimetre}.{extension}"


def _rendre_pdf(chemin_absolu: str, indicateurs: dict) -> None:
    doc = SimpleDocTemplate(chemin_absolu, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    titre_style = ParagraphStyle("Titre", parent=styles["Heading1"], fontSize=16, spaceAfter=6)
    sous_titre_style = ParagraphStyle("SousTitre", parent=styles["Normal"], fontSize=10, textColor=colors.grey, spaceAfter=14)

    elements = [
        Paragraph(f"Rapport {_LIBELLE_PERIODE[indicateurs['type_periode']]} de présence — SRB Haute Matsiatra", titre_style),
        Paragraph(
            f"Période du {indicateurs['periode_debut'].strftime('%d/%m/%Y')} au "
            f"{indicateurs['periode_fin'].strftime('%d/%m/%Y')} — Périmètre : {indicateurs['nom_service']} — "
            f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            sous_titre_style,
        ),
    ]

    g = indicateurs["globaux"]
    taux = f"{g['taux_presence'] * 100:.1f} %" if g["taux_presence"] is not None else "n/d"
    kpi_data = [
        ["Agents concernés", "Taux de présence", "Retards", "Absences", "Départs anticipés", "Heures travaillées"],
        [str(g["nombre_agents"]), taux, str(g["nombre_retards"]), str(g["nombre_absences"]),
         str(g["nombre_departs_anticipes"]), f"{g['heures_travaillees']:.1f} h"],
    ]
    kpi_table = Table(kpi_data, hAlign="LEFT")
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements += [kpi_table, Spacer(1, 18)]

    if indicateurs["detail_services"]:
        elements.append(Paragraph("Détail par service", styles["Heading2"]))
        entetes = ["Service", "Agents", "Présence", "Retards", "Absences", "Départs anticipés", "Heures"]
        lignes = [entetes]
        for s in indicateurs["detail_services"]:
            taux_s = f"{s['taux_presence'] * 100:.1f} %" if s["taux_presence"] is not None else "n/d"
            lignes.append([
                s["nom_service"], str(s["nombre_agents"]), taux_s, str(s["nombre_retards"]),
                str(s["nombre_absences"]), str(s["nombre_departs_anticipes"]), f"{s['heures_travaillees']:.1f} h",
            ])
        table = Table(lignes, hAlign="LEFT", repeatRows=1)
        table.setStyle(_style_tableau_detail())
        elements.append(table)

    if indicateurs["detail_agents"]:
        elements.append(Paragraph("Détail par agent", styles["Heading2"]))
        entetes = ["Matricule", "Agent", "Présence", "Retards", "Absences", "Départs anticipés", "Heures"]
        lignes = [entetes]
        for a in indicateurs["detail_agents"]:
            taux_a = f"{a['taux_presence'] * 100:.1f} %" if a["taux_presence"] is not None else "n/d"
            lignes.append([
                a["matricule"], f"{a['prenom']} {a['nom']}", taux_a, str(a["nombre_retards"]),
                str(a["nombre_absences"]), str(a["nombre_departs_anticipes"]), f"{a['heures_travaillees']:.1f} h",
            ])
        table = Table(lignes, hAlign="LEFT", repeatRows=1)
        table.setStyle(_style_tableau_detail())
        elements.append(table)

    doc.build(elements)


def _style_tableau_detail() -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e2f3")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ])


def _rendre_excel(chemin_absolu: str, indicateurs: dict) -> None:
    wb = Workbook()

    ws_synthese = wb.active
    ws_synthese.title = "Synthèse"
    entete_font = Font(bold=True, color="FFFFFF")
    entete_fill = "1F3864"

    ws_synthese["A1"] = f"Rapport {_LIBELLE_PERIODE[indicateurs['type_periode']]} de présence — SRB Haute Matsiatra"
    ws_synthese["A1"].font = Font(bold=True, size=14)
    ws_synthese["A2"] = (
        f"Période du {indicateurs['periode_debut'].isoformat()} au {indicateurs['periode_fin'].isoformat()} "
        f"— Périmètre : {indicateurs['nom_service']}"
    )
    ws_synthese["A3"] = f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}"

    g = indicateurs["globaux"]
    entetes_kpi = ["Agents concernés", "Taux de présence", "Retards", "Absences", "Départs anticipés", "Heures travaillées"]
    valeurs_kpi = [
        g["nombre_agents"],
        round(g["taux_presence"] * 100, 1) if g["taux_presence"] is not None else "n/d",
        g["nombre_retards"], g["nombre_absences"], g["nombre_departs_anticipes"], g["heures_travaillees"],
    ]
    for col, (entete, valeur) in enumerate(zip(entetes_kpi, valeurs_kpi), start=1):
        ws_synthese.cell(row=5, column=col, value=entete)
        ws_synthese.cell(row=6, column=col, value=valeur)
        ws_synthese.column_dimensions[get_column_letter(col)].width = 20

    for cell in ws_synthese[5]:
        cell.font = entete_font
        cell.alignment = Alignment(horizontal="center")
        cell.fill = _fill(entete_fill)

    if indicateurs["detail_services"]:
        ws = wb.create_sheet("Détail par service")
        entetes = ["Service", "Agents", "Taux de présence (%)", "Retards", "Absences", "Départs anticipés", "Heures travaillées"]
        ws.append(entetes)
        for cell in ws[1]:
            cell.font = entete_font
            cell.fill = _fill(entete_fill)
        for s in indicateurs["detail_services"]:
            ws.append([
                s["nom_service"], s["nombre_agents"],
                round(s["taux_presence"] * 100, 1) if s["taux_presence"] is not None else "n/d",
                s["nombre_retards"], s["nombre_absences"], s["nombre_departs_anticipes"], s["heures_travaillees"],
            ])
        for col in range(1, len(entetes) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 20

    if indicateurs["detail_agents"]:
        ws = wb.create_sheet("Détail par agent")
        entetes = ["Matricule", "Nom", "Prénom", "Taux de présence (%)", "Retards", "Absences", "Départs anticipés", "Heures travaillées"]
        ws.append(entetes)
        for cell in ws[1]:
            cell.font = entete_font
            cell.fill = _fill(entete_fill)
        for a in indicateurs["detail_agents"]:
            ws.append([
                a["matricule"], a["nom"], a["prenom"],
                round(a["taux_presence"] * 100, 1) if a["taux_presence"] is not None else "n/d",
                a["nombre_retards"], a["nombre_absences"], a["nombre_departs_anticipes"], a["heures_travaillees"],
            ])
        for col in range(1, len(entetes) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 18

    wb.save(chemin_absolu)


def _fill(couleur_hex: str) -> PatternFill:
    return PatternFill(start_color=couleur_hex, end_color=couleur_hex, fill_type="solid")


# --------------------------------------------------------------------
# Étape 7 : consignation en base + orchestration complète
# --------------------------------------------------------------------

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
