"""
Calcul et agrégation des indicateurs du module Rapports (étapes 3-5 du BPMN).
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
    présence. Un service sans aucun horaire configuré (ou id_service=None,
    agent sans service) applique les jours ouvrés par défaut (lundi-vendredi),
    cohérent avec `horaire_service.horaire_effectif()` : le week-end n'est
    jamais compté comme jour travaillé tant qu'il n'est pas explicitement
    configuré comme tel. Les jours fériés officiels (`app.core.jours_feries`)
    sont systématiquement exclus, quel que soit l'horaire du service.
    """
    stmt = select(HoraireReference.jour_semaine).where(HoraireReference.id_service == id_service).distinct()
    jours_travailles = set(db.execute(stmt).scalars().all())
    reference = jours_travailles or horaire_service.JOURS_OUVRES_PAR_DEFAUT

    total = 0
    jour = date_debut
    while jour <= date_fin:
        if _JOURS_PAR_INDEX[jour.weekday()] in reference and not jours_feries.est_jour_ferie(jour):
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


def jours_pointes_agent(db: Session, id_agent: int, date_debut: date_, date_fin: date_) -> int:
    """
    Nombre de jours distincts de la période où l'agent a enregistré au moins
    un pointage d'entrée valide.

    Sert de garde-fou pour les indicateurs de ponctualité/présence : le
    dénombrement `jours_presents` (= jours_ouvres - nombre_absences) suppose
    que toute journée non explicitement signalée comme "absence" (table
    `anomalie`) correspond à une présence réelle. Or la détection d'anomalies
    est un traitement différé : un agent qui n'a encore jamais pointé (fiche
    tout juste créée, badge non activé, etc.) n'aura pas encore d'anomalie
    "absence" à son nom et se retrouverait donc compté comme présent à 100 %.
    `jours_pointes_agent` reflète l'activité réellement observée dans la
    table `pointage`, indépendamment du traitement d'anomalies.
    """
    stmt = select(func.date(Pointage.date_heure)).where(
        Pointage.id_agent == id_agent,
        Pointage.type_pointage == TypePointage.ENTREE,
        Pointage.statut == StatutPointage.VALIDE,
        Pointage.date_heure >= datetime.combine(date_debut, datetime.min.time()),
        Pointage.date_heure <= datetime.combine(date_fin, datetime.max.time()),
    ).distinct()
    return len(db.execute(stmt).all())


def jours_conge_agent(db: Session, id_agent: int, id_service: Optional[int], date_debut: date_, date_fin: date_) -> int:
    """
    Nombre de jours ouvrés de la période où l'agent est couvert par un congé
    ACTIF (il n'est donc pas tenu de pointer ces jours-là — cf. la même
    exclusion appliquée par `anomalie_service.detecter_absences`). Sert à
    calculer un dénominateur de présence qui ne pénalise pas les congés
    légitimes tout en restant exact indépendamment du job de détection
    d'absences (cf. `jours_pointes_agent`).
    """
    stmt_conges = select(Conge.date_debut, Conge.date_fin).where(
        Conge.id_agent == id_agent,
        Conge.statut == StatutConge.ACTIF,
        Conge.date_debut <= date_fin,
        Conge.date_fin >= date_debut,
    )
    intervalles = db.execute(stmt_conges).all()
    if not intervalles:
        return 0

    stmt_horaire = select(HoraireReference.jour_semaine).where(HoraireReference.id_service == id_service).distinct()
    jours_travailles = set(db.execute(stmt_horaire).scalars().all())
    reference = jours_travailles or horaire_service.JOURS_OUVRES_PAR_DEFAUT

    total = 0
    jour = date_debut
    while jour <= date_fin:
        est_ouvre = _JOURS_PAR_INDEX[jour.weekday()] in reference and not jours_feries.est_jour_ferie(jour)
        if est_ouvre and any(debut_c <= jour <= fin_c for debut_c, fin_c in intervalles):
            total += 1
        jour += timedelta(days=1)
    return total


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
    jours_pointes: int = 0,
    jours_conge: int = 0,
) -> dict:
    """
    Calcule les indicateurs de présence d'un agent sur la période.

    `jours_presents` / `taux_presence` sont dérivés des données réelles :
    - `jours_pointes` (jours où l'agent a effectivement pointé, cf.
      `jours_pointes_agent`) plutôt que d'une soustraction
      `jours_ouvres - nombre_absences`, qui dépendait du job différé
      `anomalie_service.detecter_absences` (exécuté en fin de journée ou le
      lendemain) : tant que ce job n'était pas passé, tout jour non encore
      signalé "absence" comptait comme présent, ce qui gonflait
      artificiellement le taux de présence du jour même / de la période en
      cours.
    - `jours_conge` (jours ouvrés couverts par un congé actif, cf.
      `jours_conge_agent`) est retranché du dénominateur : un agent en congé
      n'est pas tenu de pointer, ce jour ne doit donc ni compter contre lui
      ni gonfler artificiellement son taux de présence.

    `nombre_absences` reste calculé à partir de la table `anomalie` (registre
    officiel, éventuellement justifié) et n'est pas utilisé pour ce calcul.
    """
    nombre_retards = compte_anomalies.get((agent.id_agent, TypeAnomalie.RETARD), 0)
    nombre_absences = compte_anomalies.get((agent.id_agent, TypeAnomalie.ABSENCE), 0)
    nombre_departs = compte_anomalies.get((agent.id_agent, TypeAnomalie.DEPART_ANTICIPE), 0)

    jours_conge = min(jours_conge, jours_ouvres)
    jours_ouvres_effectifs = max(jours_ouvres - jours_conge, 0)
    jours_presents = jours_pointes
    taux_presence = (
        round(jours_presents / jours_ouvres_effectifs, 4) if jours_ouvres_effectifs > 0 else None
    )

    return {
        "id_agent": agent.id_agent,
        "matricule": agent.matricule,
        "nom": agent.nom,
        "prenom": agent.prenom,
        # Jours ouvrés nets de congé — dénominateur exact du taux de présence.
        "jours_ouvres": jours_ouvres_effectifs,
        "jours_presents": jours_presents,
        # Jours réellement pointés (table `pointage`) — identique à
        # `jours_presents` ci-dessus, exposé séparément pour compatibilité
        # avec les usages existants (ex. classement de ponctualité).
        "jours_pointes": jours_pointes,
        "jours_conge": jours_conge,
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
                agent,
                jours_ouvres,
                heures_travaillees_agent(db, agent.id_agent, date_debut, date_fin),
                compte_anomalies,
                jours_pointes_agent(db, agent.id_agent, date_debut, date_fin),
                jours_conge_agent(db, agent.id_agent, id_service, date_debut, date_fin),
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
                agent,
                jours_ouvres,
                heures_travaillees_agent(db, agent.id_agent, date_debut, date_fin),
                compte_anomalies,
                jours_pointes_agent(db, agent.id_agent, date_debut, date_fin),
                jours_conge_agent(db, agent.id_agent, service.id_service, date_debut, date_fin),
            )
            for agent in agents
        ]
        tous_agents_indicateurs.extend(indicateurs_agents_service)
        agrege = agreger_indicateurs(indicateurs_agents_service)
        detail_services.append({
            "id_service": service.id_service,
            "nom_service": service.nom_service,
            "nombre_agents": agrege["nombre_agents"],
            "jours_ouvres": agrege["jours_ouvres"],
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

