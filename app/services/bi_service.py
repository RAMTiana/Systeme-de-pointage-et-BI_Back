"""
Service métier — Module Tableau de bord décisionnel / BI (Processus 5 du BPMN
"Consultation du tableau de bord décisionnel (BI)").

Ce module ne duplique pas la logique d'agrégation du module Rapports
(Processus 4) : il réutilise `rapport_service.calculer_indicateurs` pour les
indicateurs d'une période complète, et les fonctions unitaires
(`jours_ouvres_service`, `heures_travaillees_agent`, `compter_anomalies_par_type`,
`indicateurs_agent`, `bornes_periode`) pour les calculs propres au tableau de
bord temps réel et au classement des agents — cohérent avec le principe de
conception BPMN "Processus 1 et 3 alimentent les Processus 4 et 5" (les deux
processus de restitution partagent la même source de données agrégées).

Couvre les besoins du cahier des charges :
  - "Tableau de bord opérationnel" (présents/absents/retardataires en temps
    réel, taux de présence global et par service, vue consolidée multi-services)
  - "Système d'aide à la décision (BI)" (tendances, classement des agents,
    comparaison entre services, tableau de bord prédictif)

ainsi que la boucle d'exploration du Processus 5 (étapes 9→10→7 : chaque
endpoint recalcule les indicateurs à la demande selon les filtres reçus,
sans état conservé côté serveur entre deux appels).
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
    _agents_du_perimetre,
    _buckets_dans_plage,
    _buckets_recents,
)
from app.services.bi_previsions import (
    _historique_ml_adaptatif,
    _regression_lineaire,
    _serie_historique,
    detection_anomalies_ml,
    prevision,
    prevision_ml,
    prevision_ml_module_predire,
    score_risque_agents,
)
from app.services.bi_temps_reel import (
    _service_travaille_ce_jour,
    _signal_agent,
    tableau_de_bord_temps_reel,
)


def tendances(
    db: Session,
    granularite: TypePeriode,
    id_service: Optional[int],
    date_debut: date_,
    date_fin: date_,
) -> List[dict]:
    """
    Étapes 7-9-10 du Processus 5 : recalcule les indicateurs sur chaque
    période de la plage (jour/semaine/mois), pour dessiner une courbe
    d'évolution (taux de présence, retards, absences, heures travaillées).
    """
    if date_debut > date_fin:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date_debut doit précéder date_fin.")

    buckets = _buckets_dans_plage(granularite, date_debut, date_fin)
    resultats = []
    for debut, _fin in buckets:
        indicateurs = rapport_service.calculer_indicateurs(db, granularite, id_service, date_reference=debut)
        resultats.append({
            "periode_debut": indicateurs["periode_debut"],
            "periode_fin": indicateurs["periode_fin"],
            "globaux": indicateurs["globaux"],
        })
    return resultats


# --------------------------------------------------------------------
# Classement des agents (ponctualité)
# --------------------------------------------------------------------

def classement_agents(
    db: Session,
    date_debut: date_,
    date_fin: date_,
    id_service: Optional[int] = None,
    critere: Literal["ponctualite", "retards", "absences"] = "ponctualite",
    limite: int = 10,
) -> List[dict]:
    """
    "Identifier les agents les plus ponctuels, les plus souvent en retard ou
    les plus souvent absents" : classement global (id_service=None) ou
    restreint à un service, sur la période demandée.
    """
    agents = _agents_du_perimetre(db, id_service)
    if not agents:
        return []
    ids_agents = [a.id_agent for a in agents]
    compte_anomalies = rapport_service.compter_anomalies_par_type(db, ids_agents, date_debut, date_fin)
    noms_services = {s.id_service: s.nom_service for s in db.execute(select(Service)).scalars().all()}

    jours_ouvres_par_service: Dict[Optional[int], int] = {}
    resultats = []
    for agent in agents:
        if agent.id_service not in jours_ouvres_par_service:
            jours_ouvres_par_service[agent.id_service] = rapport_service.jours_ouvres_service(
                db, agent.id_service, date_debut, date_fin
            )
        jours_ouvres = jours_ouvres_par_service[agent.id_service]
        heures = rapport_service.heures_travaillees_agent(db, agent.id_agent, date_debut, date_fin)
        jours_pointes = rapport_service.jours_pointes_agent(db, agent.id_agent, date_debut, date_fin)
        jours_conge = rapport_service.jours_conge_agent(db, agent.id_agent, agent.id_service, date_debut, date_fin)
        indicateurs = rapport_service.indicateurs_agent(
            agent, jours_ouvres, heures, compte_anomalies, jours_pointes, jours_conge
        )
        resultats.append({
            **indicateurs,
            "id_service": agent.id_service,
            "nom_service": noms_services.get(agent.id_service, "Sans service"),
        })

    if critere == "ponctualite":
        # Deux exclusions pour que ce classement ne contienne que des agents
        # réellement ponctuels (il alimente la fiche "Ponctualité exemplaire"
        # du tableau de bord) :
        #   - `jours_pointes == 0` : un agent qui n'a jamais pointé sur la
        #     période n'a mécaniquement aucun retard, sans être ponctuel ;
        #   - `nombre_retards > 0` : un agent ayant enregistré au moins un
        #     retard sur la période n'a pas sa place dans un classement des
        #     agents les plus ponctuels.
        resultats = [a for a in resultats if a["jours_pointes"] > 0 and a["nombre_retards"] == 0]
        resultats.sort(key=lambda a: (-(a["taux_presence"] or 0), a["nombre_absences"], -a["jours_pointes"]))
    elif critere == "absences":
        resultats.sort(key=lambda a: (-a["nombre_absences"], -a["nombre_retards"]))
    else:
        resultats.sort(key=lambda a: (-a["nombre_retards"], -(a["nombre_absences"])))

    return resultats[:limite]


# --------------------------------------------------------------------
# Comparaison entre services
# --------------------------------------------------------------------

def comparaison_services(db: Session, type_periode: TypePeriode, date_reference: Optional[date_] = None) -> dict:
    """
    "Comparer les performances de ponctualité entre services" : réutilise le
    calcul consolidé du module Rapports (détail par service), en y ajoutant
    un rang par taux de présence décroissant.
    """
    indicateurs = rapport_service.calculer_indicateurs(db, type_periode, id_service=None, date_reference=date_reference)
    services_tries = sorted(
        indicateurs["detail_services"], key=lambda s: (s["taux_presence"] is None, -(s["taux_presence"] or 0))
    )
    for rang, s in enumerate(services_tries, start=1):
        s["rang"] = rang

    return {
        "type_periode": type_periode,
        "periode_debut": indicateurs["periode_debut"],
        "periode_fin": indicateurs["periode_fin"],
        "globaux": indicateurs["globaux"],
        "services": services_tries,
    }


# --------------------------------------------------------------------
# Tableau de bord prédictif (méthode statistique simple)
# --------------------------------------------------------------------

