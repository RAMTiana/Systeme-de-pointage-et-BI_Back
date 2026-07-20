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


# ---- Palette (reprise des design tokens du frontend — src/styles.scss) ----
_BLEU = colors.HexColor("#0f3d5c")
_TEAL = colors.HexColor("#0f6e56")
_CORAIL = colors.HexColor("#d85a30")
_AMBRE = colors.HexColor("#ba7517")
_GRIS_BORD = colors.HexColor("#e1e5ea")
_GRIS_ZEBRE = colors.HexColor("#f7f9fb")
_TEXTE = colors.HexColor("#1c2733")
_TEXTE_MUT = colors.HexColor("#5b6b7a")
_BLANC = colors.white

_PAGE_W, _PAGE_H = A4
_MARGE = 1.4 * cm
_BANNIERE_H = 2.6 * cm
_PIED_H = 1.2 * cm
_NOM_ORG = "SRB Haute Matsiatra"
_SOUS_NOM_ORG = "Système de gestion biométrique des agents"


def _couleur_taux(taux: Optional[float]):
    if taux is None:
        return _TEXTE_MUT
    if taux >= 0.90:
        return _TEAL
    if taux >= 0.75:
        return _AMBRE
    return _CORAIL


def _carte_kpi(valeur: str, label: str, couleur_accent) -> Table:
    """Petite carte KPI moderne : barre d'accent colorée + grande valeur + libellé."""
    style_valeur = ParagraphStyle("KpiValeur", fontName="Helvetica-Bold", fontSize=15.5, textColor=_TEXTE, leading=18)
    style_label = ParagraphStyle("KpiLabel", fontName="Helvetica", fontSize=7.3, textColor=_TEXTE_MUT, leading=9)
    carte = Table(
        [[Paragraph(valeur, style_valeur)], [Paragraph(label, style_label)]],
        colWidths=[2.86 * cm],
    )
    carte.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 0), (-1, -1), _BLANC),
        ("LINEABOVE", (0, 0), (-1, 0), 2.6, couleur_accent),
        ("BOX", (0, 0), (-1, -1), 0.6, _GRIS_BORD),
        ("ROUNDEDCORNERS", [7, 7, 7, 7]),
    ]))
    return carte


def _style_tableau_detail(taux_par_ligne: List[Optional[float]]) -> TableStyle:
    """Style de tableau moderne (zébrage + code couleur sur la colonne 'Présence')."""
    commandes = [
        ("BACKGROUND", (0, 0), (-1, 0), _BLEU),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BLANC),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.3),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, _BLEU),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_BLANC, _GRIS_ZEBRE]),
        ("BOX", (0, 0), (-1, -1), 0.6, _GRIS_BORD),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
    ]
    for i, taux in enumerate(taux_par_ligne, start=1):
        commandes.append(("TEXTCOLOR", (2, i), (2, i), _couleur_taux(taux)))
        commandes.append(("FONTNAME", (2, i), (2, i), "Helvetica-Bold"))
    return TableStyle(commandes)


def _dessiner_banniere(c: canvas_mod.Canvas, titre: str, sous_titre: str) -> None:
    c.saveState()
    c.setFillColor(_BLEU)
    c.rect(0, _PAGE_H - _BANNIERE_H, _PAGE_W, _BANNIERE_H, fill=1, stroke=0)
    c.setFillColor(_TEAL)
    c.rect(0, _PAGE_H - _BANNIERE_H, _PAGE_W, 0.09 * cm, fill=1, stroke=0)

    cx = _MARGE + 0.62 * cm
    cy = _PAGE_H - _BANNIERE_H / 2
    c.setFillColor(_BLANC)
    c.circle(cx, cy, 0.62 * cm, fill=1, stroke=0)
    c.setFillColor(_BLEU)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(cx, cy - 4, "SRB")

    tx = cx + 1.15 * cm
    c.setFillColor(_BLANC)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(tx, cy + 3, titre)
    c.setFont("Helvetica", 8.6)
    c.setFillColor(colors.HexColor("#cfe0ea"))
    c.drawString(tx, cy - 11, sous_titre)

    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(_BLANC)
    c.drawRightString(_PAGE_W - _MARGE, cy + 3, _NOM_ORG)
    c.setFont("Helvetica", 7.6)
    c.setFillColor(colors.HexColor("#cfe0ea"))
    c.drawRightString(_PAGE_W - _MARGE, cy - 11, _SOUS_NOM_ORG)
    c.restoreState()


class _CanvasAvecPagination(canvas_mod.Canvas):
    """Canvas qui reporte l'écriture de la bannière/pied de page après la mise
    en page complète, afin d'afficher 'Page X / N' (N n'est connu qu'une fois
    toutes les pages construites)."""

    def __init__(self, *args, titre_banniere: str = "", sous_titre_banniere: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages_en_attente: List[dict] = []
        self._titre_banniere = titre_banniere
        self._sous_titre_banniere = sous_titre_banniere

    def showPage(self) -> None:
        self._pages_en_attente.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages_en_attente)
        for i, etat in enumerate(self._pages_en_attente, start=1):
            self.__dict__.update(etat)
            _dessiner_banniere(self, self._titre_banniere, self._sous_titre_banniere)
            self._dessiner_pied(i, total)
            super().showPage()
        super().save()

    def _dessiner_pied(self, page_courante: int, total_pages: int) -> None:
        self.saveState()
        self.setStrokeColor(_GRIS_BORD)
        self.setLineWidth(0.6)
        self.line(_MARGE, _PIED_H, _PAGE_W - _MARGE, _PIED_H)
        self.setFont("Helvetica", 7.6)
        self.setFillColor(_TEXTE_MUT)
        self.drawString(
            _MARGE, _PIED_H - 12,
            f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')} — document à usage interne",
        )
        self.drawRightString(_PAGE_W - _MARGE, _PIED_H - 12, f"Page {page_courante} / {total_pages}")
        self.restoreState()


def _rendre_pdf(chemin_absolu: str, indicateurs: dict) -> None:
    frame = Frame(
        _MARGE, _PIED_H + 0.2 * cm, _PAGE_W - 2 * _MARGE, _PAGE_H - _BANNIERE_H - _PIED_H - 0.6 * cm,
        id="normal",
    )
    doc = BaseDocTemplate(chemin_absolu, pagesize=A4)
    doc.addPageTemplates([PageTemplate(id="rapport", frames=[frame])])

    style_titre_section = ParagraphStyle(
        "TitreSection", fontName="Helvetica-Bold", fontSize=11.5, textColor=_BLEU, spaceBefore=4, spaceAfter=8,
    )
    style_meta = ParagraphStyle("Meta", fontName="Helvetica", fontSize=8.6, textColor=_TEXTE_MUT, spaceAfter=14)

    libelle_periode = _LIBELLE_PERIODE[indicateurs["type_periode"]]
    titre_banniere = f"Rapport {libelle_periode} de présence"
    sous_titre_banniere = (
        f"Du {indicateurs['periode_debut'].strftime('%d/%m/%Y')} "
        f"au {indicateurs['periode_fin'].strftime('%d/%m/%Y')}"
    )

    elements = [
        Paragraph(
            f"Périmètre : {indicateurs['nom_service']} &nbsp;&bull;&nbsp; "
            f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            style_meta,
        )
    ]

    g = indicateurs["globaux"]
    taux_global = f"{g['taux_presence'] * 100:.1f} %" if g["taux_presence"] is not None else "n/d"
    kpis = [
        (str(g["nombre_agents"]), "Agents concernés", _BLEU),
        (taux_global, "Taux de présence", _couleur_taux(g["taux_presence"])),
        (str(g["nombre_retards"]), "Retards", _AMBRE),
        (str(g["nombre_absences"]), "Absences", _CORAIL),
        (str(g["nombre_departs_anticipes"]), "Départs anticipés", _AMBRE),
        (f"{g['heures_travaillees']:.1f} h", "Heures travaillées", _BLEU),
    ]
    cartes = [_carte_kpi(v, l, c) for v, l, c in kpis]
    table_kpi = Table([cartes], colWidths=[3.03 * cm] * 6)
    table_kpi.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements += [table_kpi, Spacer(1, 20)]

    if indicateurs["detail_services"]:
        elements.append(Paragraph("Détail par service", style_titre_section))
        entetes = ["Service", "Agents", "Présence", "Retards", "Absences", "Départs anticipés", "Heures"]
        lignes = [entetes]
        taux_lignes: List[Optional[float]] = []
        for s in indicateurs["detail_services"]:
            taux_s = f"{s['taux_presence'] * 100:.1f} %" if s["taux_presence"] is not None else "n/d"
            lignes.append([
                s["nom_service"], str(s["nombre_agents"]), taux_s, str(s["nombre_retards"]),
                str(s["nombre_absences"]), str(s["nombre_departs_anticipes"]), f"{s['heures_travaillees']:.1f} h",
            ])
            taux_lignes.append(s["taux_presence"])
        table = Table(
            lignes, hAlign="LEFT", repeatRows=1,
            colWidths=[3.6 * cm, 1.7 * cm, 2.1 * cm, 1.9 * cm, 1.9 * cm, 1.9 * cm, 2.1 * cm],
        )
        table.setStyle(_style_tableau_detail(taux_lignes))
        elements += [table, Spacer(1, 20)]

    if indicateurs["detail_agents"]:
        elements.append(Paragraph("Détail par agent", style_titre_section))
        entetes = ["Matricule", "Agent", "Présence", "Retards", "Absences", "Départs anticipés", "Heures"]
        lignes = [entetes]
        taux_lignes = []
        for a in indicateurs["detail_agents"]:
            taux_a = f"{a['taux_presence'] * 100:.1f} %" if a["taux_presence"] is not None else "n/d"
            lignes.append([
                a["matricule"], f"{a['prenom']} {a['nom']}", taux_a, str(a["nombre_retards"]),
                str(a["nombre_absences"]), str(a["nombre_departs_anticipes"]), f"{a['heures_travaillees']:.1f} h",
            ])
            taux_lignes.append(a["taux_presence"])
        table = Table(
            lignes, hAlign="LEFT", repeatRows=1,
            colWidths=[2.6 * cm, 4.1 * cm, 2.1 * cm, 1.9 * cm, 1.9 * cm, 1.9 * cm, 2.1 * cm],
        )
        table.setStyle(_style_tableau_detail(taux_lignes))
        elements.append(table)

    doc.build(
        elements,
        canvasmaker=lambda *a, **kw: _CanvasAvecPagination(
            *a, titre_banniere=titre_banniere, sous_titre_banniere=sous_titre_banniere, **kw
        ),
    )


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
