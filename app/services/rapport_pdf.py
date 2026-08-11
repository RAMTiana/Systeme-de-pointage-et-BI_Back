"""
Rendu PDF des rapports (connecteur d'export reportlab).
Extrait de `rapport_service` pour respecter la limite de 500 lignes par fichier.
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
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.utils import ImageReader
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
    _CHEMIN_LOGO,
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
    _fmt_heures,
    _fmt_heures_arrondi,
    _fmt_nombre,
    _fmt_pourcent,
    _hex_argb,
    _logo_disponible,
)



def _couleur_taux(taux: Optional[float]):
    if taux is None:
        return _TEXTE_MUT
    if taux >= 0.90:
        return _TEAL
    if taux >= 0.75:
        return _AMBRE
    return _CORAIL


def _carte_kpi(valeur: str, label: str, couleur_accent) -> Table:
    """Petite carte KPI moderne : barre d'accent colorée + grande valeur + libellé.

    La taille de police de la valeur s'ajuste à sa longueur (les grands
    totaux d'heures type "18 344,5 h" ne tiennent pas à 15.5pt dans une
    carte de 2.9cm) afin qu'elle reste toujours sur une seule ligne."""
    if len(valeur) <= 6:
        taille_valeur = 15.5
    elif len(valeur) <= 9:
        taille_valeur = 13
    else:
        taille_valeur = 11
    style_valeur = ParagraphStyle(
        "KpiValeur", fontName="Helvetica-Bold", fontSize=taille_valeur, textColor=_TEXTE,
        leading=taille_valeur + 2.5,
    )
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


_STYLE_ENTETE_TABLEAU = ParagraphStyle(
    "EnteteTableau", fontName="Helvetica-Bold", fontSize=7.8, textColor=_BLANC, leading=9.2, alignment=TA_CENTER,
)
_STYLE_ENTETE_TABLEAU_GAUCHE = ParagraphStyle(
    "EnteteTableauGauche", parent=_STYLE_ENTETE_TABLEAU, alignment=TA_LEFT,
)


def _entetes_tableau(libelles: List[str]) -> List[Paragraph]:
    """Enveloppe chaque libellé d'en-tête dans un Paragraph pour qu'il se
    replie sur plusieurs lignes au lieu de déborder sur la colonne voisine —
    une chaîne brute trop longue pour sa colonne (ex. "Départs anticipés"
    dans 1.9cm) n'est pas coupée par reportlab et chevauche silencieusement
    la cellule suivante."""
    return [
        Paragraph(libelle, _STYLE_ENTETE_TABLEAU_GAUCHE if i == 0 else _STYLE_ENTETE_TABLEAU)
        for i, libelle in enumerate(libelles)
    ]


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
    rayon = 0.62 * cm
    c.setFillColor(_BLANC)
    c.circle(cx, cy, rayon, fill=1, stroke=0)
    if _logo_disponible():
        # Emblème officiel inséré dans le médaillon blanc, légèrement en
        # retrait du bord pour conserver le liséré blanc tout autour.
        diametre_logo = 2 * rayon - 0.14 * cm
        c.drawImage(
            ImageReader(_CHEMIN_LOGO),
            cx - diametre_logo / 2, cy - diametre_logo / 2, diametre_logo, diametre_logo,
            mask="auto", preserveAspectRatio=True, anchor="c",
        )
    else:
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
    libelle_periode = _LIBELLE_PERIODE[indicateurs["type_periode"]]
    titre_banniere = f"Rapport {libelle_periode} de présence"
    sous_titre_banniere = (
        f"Du {indicateurs['periode_debut'].strftime('%d/%m/%Y')} "
        f"au {indicateurs['periode_fin'].strftime('%d/%m/%Y')}"
    )

    doc = BaseDocTemplate(
        chemin_absolu,
        pagesize=A4,
        title=f"{titre_banniere} — {indicateurs['nom_service']}",
        author=_NOM_ORG,
        subject=f"Rapport de présence {libelle_periode}, période du "
                f"{indicateurs['periode_debut'].isoformat()} au {indicateurs['periode_fin'].isoformat()}",
        creator="Système de pointage et BI — SRB Haute Matsiatra",
    )
    doc.addPageTemplates([PageTemplate(id="rapport", frames=[frame])])

    style_titre_section = ParagraphStyle(
        "TitreSection", fontName="Helvetica-Bold", fontSize=11.5, textColor=_BLEU, spaceBefore=4, spaceAfter=8,
    )
    style_meta = ParagraphStyle("Meta", fontName="Helvetica", fontSize=8.6, textColor=_TEXTE_MUT, spaceAfter=14)

    elements = [
        Paragraph(
            f"Périmètre : {indicateurs['nom_service']} &nbsp;&bull;&nbsp; "
            f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            style_meta,
        )
    ]

    g = indicateurs["globaux"]
    taux_global = _fmt_pourcent(g["taux_presence"])
    kpis = [
        (_fmt_nombre(g["nombre_agents"]), "Agents concernés", _BLEU),
        (taux_global, "Taux de présence", _couleur_taux(g["taux_presence"])),
        (_fmt_nombre(g["nombre_retards"]), "Retards", _AMBRE),
        (_fmt_nombre(g["nombre_absences"]), "Absences", _CORAIL),
        (_fmt_nombre(g["nombre_departs_anticipes"]), "Départs anticipés", _AMBRE),
        (_fmt_heures_arrondi(g["heures_travaillees"]), "Heures travaillées", _BLEU),
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
        lignes = [_entetes_tableau(entetes)]
        taux_lignes: List[Optional[float]] = []
        for s in indicateurs["detail_services"]:
            lignes.append([
                s["nom_service"], _fmt_nombre(s["nombre_agents"]), _fmt_pourcent(s["taux_presence"]),
                _fmt_nombre(s["nombre_retards"]), _fmt_nombre(s["nombre_absences"]),
                _fmt_nombre(s["nombre_departs_anticipes"]), _fmt_heures(s["heures_travaillees"]),
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
        lignes = [_entetes_tableau(entetes)]
        taux_lignes = []
        for a in indicateurs["detail_agents"]:
            lignes.append([
                a["matricule"], f"{a['prenom']} {a['nom']}", _fmt_pourcent(a["taux_presence"]),
                _fmt_nombre(a["nombre_retards"]), _fmt_nombre(a["nombre_absences"]),
                _fmt_nombre(a["nombre_departs_anticipes"]), _fmt_heures(a["heures_travaillees"]),
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


