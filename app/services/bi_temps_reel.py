"""
Tableau de bord opérationnel temps réel du module BI.
Extrait de `bi_service` pour respecter la limite de 500 lignes par fichier.
"""
from datetime import date as date_
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import jours_feries
from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import JourSemaine, StatutAgent, StatutPointage, TypeAnomalie, TypePeriode, TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.models.service import Service
from app.services import conge_service, horaire_service, rapport_service
from app.ml import anomalies_ml, risque_agents
# Alias explicite : le service expose lui aussi une fonction `prevision_ml`,
# qui masquerait le module importé sous le même nom (AttributeError -> 500).
from app.ml import prevision_ml as prevision_ml_module

from app.services.bi_commun import (
    _JOURS_PAR_INDEX,
    _MAX_BUCKETS,
    _ML_MAX_CALCULS_RISQUE,
    _ML_MAX_MOIS_RISQUE,
    _ML_MAX_PERIODES,
    _ML_MIN_ECHANTILLONS,
    _ML_MIN_POINTS,
    _agents_du_perimetre,
    _buckets_dans_plage,
    _buckets_recents,
)

def _service_travaille_ce_jour(jours_horaire: Dict[Optional[int], set], id_service: Optional[int], jour_semaine: JourSemaine) -> bool:
    jours = jours_horaire.get(id_service)
    if not jours:
        # Pas d'horaire de référence configuré pour ce service : on applique
        # les jours ouvrés par défaut (lundi-vendredi), cohérent avec
        # horaire_service.horaire_effectif() et la détection d'absences —
        # le week-end n'est jamais un jour attendu tant qu'il n'est pas
        # explicitement configuré comme travaillé.
        return jour_semaine in horaire_service.JOURS_OUVRES_PAR_DEFAUT
    return jour_semaine in jours


def _signal_agent(agent: Agent, nom_service: str) -> dict:
    """Identification minimale d'un agent pour les listes nominatives (absents/retardataires du jour)."""
    return {
        "id_agent": agent.id_agent,
        "matricule": agent.matricule,
        "nom": agent.nom,
        "prenom": agent.prenom,
        "id_service": agent.id_service,
        "nom_service": nom_service,
    }


def tableau_de_bord_temps_reel(db: Session, id_service: Optional[int] = None, jour: Optional[date_] = None) -> dict:
    """
    Étapes 2-8 du Processus 5, cas "vue opérationnelle" : statut du jour pour
    chaque agent du périmètre, déduit de son dernier pointage valide du jour
    (encore présent, déjà sorti, ou absent si son service travaille
    aujourd'hui et qu'aucun pointage n'est encore enregistré).
    """
    jour = jour or date_.today()
    jour_semaine = _JOURS_PAR_INDEX[jour.weekday()]
    # Un jour férié suspend l'attente de pointage pour tout le monde, quel
    # que soit l'horaire de référence du service (même logique que la
    # détection d'absences — cf. anomalie_service.detecter_absences).
    est_ferie = jours_feries.est_jour_ferie(jour)

    nom_service = "Tous services"
    if id_service is not None:
        service = db.get(Service, id_service)
        if service is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service introuvable.")
        nom_service = service.nom_service

    agents = _agents_du_perimetre(db, id_service)
    ids_agents = [a.id_agent for a in agents]

    # Horaires de référence de tous les services concernés (une seule requête)
    ids_services_concernes = {a.id_service for a in agents}
    jours_horaire: Dict[Optional[int], set] = {}
    for id_s in ids_services_concernes:
        stmt = select(HoraireReference.jour_semaine).where(HoraireReference.id_service == id_s).distinct()
        jours_horaire[id_s] = set(db.execute(stmt).scalars().all())

    # Dernier pointage valide du jour, par agent
    dernier_pointage: Dict[int, TypePointage] = {}
    if ids_agents:
        stmt = select(Pointage).where(
            Pointage.id_agent.in_(ids_agents),
            Pointage.statut == StatutPointage.VALIDE,
            Pointage.date_heure >= datetime.combine(jour, datetime.min.time()),
            Pointage.date_heure <= datetime.combine(jour, datetime.max.time()),
        ).order_by(Pointage.date_heure)
        for p in db.execute(stmt).scalars().all():
            dernier_pointage[p.id_agent] = p.type_pointage  # écrase avec le plus récent au fil de l'itération

    # Retardataires du jour (anomalie 'retard' détectée aujourd'hui)
    ids_retardataires: set = set()
    if ids_agents:
        stmt = select(Anomalie.id_agent).where(
            Anomalie.id_agent.in_(ids_agents),
            Anomalie.type_anomalie == TypeAnomalie.RETARD,
            Anomalie.date_detection >= datetime.combine(jour, datetime.min.time()),
            Anomalie.date_detection <= datetime.combine(jour, datetime.max.time()),
        ).distinct()
        ids_retardataires = set(db.execute(stmt).scalars().all())

    # Agents en congé ACTIF couvrant ce jour : ils ne sont pas tenus de
    # pointer (même exclusion que `anomalie_service.detecter_absences`), ils
    # ne doivent donc pas être comptés comme "attendus" ni remonter dans la
    # liste des absents du jour.
    ids_en_conge = conge_service.agents_en_conge(db, jour, ids_agents=ids_agents) if ids_agents else set()

    par_service: Dict[Optional[int], dict] = {}

    def _compteur_service(id_s: Optional[int], nom_s: str) -> dict:
        return par_service.setdefault(id_s, {
            "id_service": id_s, "nom_service": nom_s,
            "nombre_agents_attendus": 0, "nombre_presents": 0, "nombre_sortis": 0,
            "nombre_absents": 0, "nombre_retardataires": 0,
        })

    services_par_id = {s.id_service: s.nom_service for s in db.execute(select(Service)).scalars().all()}

    nombre_presents = nombre_sortis = nombre_absents = nombre_retardataires = nombre_attendus = 0
    agents_absents: List[dict] = []
    agents_retardataires: List[dict] = []

    for agent in agents:
        attendu = (
            not est_ferie
            and _service_travaille_ce_jour(jours_horaire, agent.id_service, jour_semaine)
            and agent.id_agent not in ids_en_conge
        )
        statut_jour = dernier_pointage.get(agent.id_agent)
        est_retardataire = agent.id_agent in ids_retardataires
        nom_s = services_par_id.get(agent.id_service, "Sans service")

        if attendu:
            nombre_attendus += 1
        if statut_jour == TypePointage.ENTREE:
            nombre_presents += 1
        elif statut_jour == TypePointage.SORTIE:
            nombre_sortis += 1
        elif attendu:
            nombre_absents += 1
            agents_absents.append(_signal_agent(agent, nom_s))
        if est_retardataire:
            nombre_retardataires += 1
            agents_retardataires.append(_signal_agent(agent, nom_s))

        compteur = _compteur_service(agent.id_service, nom_s)
        if attendu:
            compteur["nombre_agents_attendus"] += 1
        if statut_jour == TypePointage.ENTREE:
            compteur["nombre_presents"] += 1
        elif statut_jour == TypePointage.SORTIE:
            compteur["nombre_sortis"] += 1
        elif attendu:
            compteur["nombre_absents"] += 1
        if est_retardataire:
            compteur["nombre_retardataires"] += 1

    agents_absents.sort(key=lambda a: (a["nom"], a["prenom"]))
    agents_retardataires.sort(key=lambda a: (a["nom"], a["prenom"]))

    def _avec_taux(c: dict) -> dict:
        presents_ou_sortis = c["nombre_presents"] + c["nombre_sortis"]
        c["taux_presence"] = round(presents_ou_sortis / c["nombre_agents_attendus"], 4) if c["nombre_agents_attendus"] > 0 else None
        return c

    taux_presence_global = (
        round((nombre_presents + nombre_sortis) / nombre_attendus, 4) if nombre_attendus > 0 else None
    )

    return {
        "jour": jour,
        "id_service": id_service,
        "nom_service": nom_service,
        "nombre_agents_attendus": nombre_attendus,
        "nombre_presents": nombre_presents,
        "nombre_sortis": nombre_sortis,
        "nombre_absents": nombre_absents,
        "nombre_retardataires": nombre_retardataires,
        "taux_presence": taux_presence_global,
        "detail_services": [_avec_taux(c) for c in par_service.values()] if id_service is None else [],
        "agents_absents": agents_absents,
        "agents_retardataires": agents_retardataires,
    }
