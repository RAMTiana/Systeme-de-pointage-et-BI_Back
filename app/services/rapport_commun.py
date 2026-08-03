"""
Palette, constantes et helpers partagés du module Rapports (voir `rapport_service`).
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


def _hex_argb(couleur) -> str:
    """Convertit une couleur reportlab (0.0-1.0 par canal) en hex RRGGBB pour openpyxl."""
    return "".join(f"{int(round(c * 255)):02X}" for c in (couleur.red, couleur.green, couleur.blue))


# Mêmes couleurs que le PDF, réexprimées en hex pour openpyxl — une seule
# palette de référence pour les deux formats d'export.
_BLEU_HEX = _hex_argb(_BLEU)
_TEAL_HEX = _hex_argb(_TEAL)
_CORAIL_HEX = _hex_argb(_CORAIL)
_AMBRE_HEX = _hex_argb(_AMBRE)
_GRIS_BORD_HEX = _hex_argb(_GRIS_BORD)
_GRIS_ZEBRE_HEX = _hex_argb(_GRIS_ZEBRE)
_TEXTE_HEX = _hex_argb(_TEXTE)
_TEXTE_MUT_HEX = _hex_argb(_TEXTE_MUT)
