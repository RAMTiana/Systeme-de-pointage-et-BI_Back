"""
Prévisions, détection d'anomalies et score de risque (BI prédictif).
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

def _regression_lineaire(points: List[Tuple[int, float]]) -> Optional[Tuple[float, float]]:
    """Régression linéaire simple (moindres carrés) — sans dépendance externe (numpy)."""
    n = len(points)
    if n < 2:
        return None
    somme_x = sum(x for x, _ in points)
    somme_y = sum(y for _, y in points)
    somme_xy = sum(x * y for x, y in points)
    somme_x2 = sum(x * x for x, _ in points)

    denominateur = n * somme_x2 - somme_x ** 2
    if denominateur == 0:
        return None

    pente = (n * somme_xy - somme_x * somme_y) / denominateur
    ordonnee = (somme_y - pente * somme_x) / n
    return pente, ordonnee


def prevision(
    db: Session,
    granularite: TypePeriode,
    id_service: Optional[int] = None,
    nombre_periodes_historique: int = 6,
    horizon: int = 3,
    date_reference: Optional[date_] = None,
) -> dict:
    """
    "Estimer les tendances futures de ponctualité et d'assiduité à partir de
    l'historique des données, à l'aide de méthodes statistiques simples" :
    régression linéaire sur le taux de présence des `nombre_periodes_historique`
    dernières périodes complètes, projetée sur `horizon` périodes futures.

    Estimation indicative — pas un modèle prédictif avancé — conformément à
    la formulation du cahier des charges ("méthodes statistiques simples").
    """
    date_reference = date_reference or (date_.today() - timedelta(days=1))
    buckets_historique, historique, points_regression = _historique_ml_adaptatif(
        db, granularite, id_service, nombre_periodes_historique, date_reference
    )
    if not buckets_historique:
        return {
            "granularite": granularite,
            "id_service": id_service,
            "methode": "aucune",
            "historique": [],
            "prevision": [],
            "avertissement": "Aucune donnée de présence exploitable sur l'historique disponible.",
        }

    coefficients = _regression_lineaire(points_regression)

    projections: List[dict] = []
    avertissement = None
    if coefficients is None:
        avertissement = "Historique insuffisant (au moins 2 périodes avec des données sont nécessaires) pour estimer une tendance."
    else:
        pente, ordonnee = coefficients
        dernier_debut, dernier_fin = buckets_historique[-1]
        debut_projete, fin_projetee = rapport_service.bornes_periode(granularite, dernier_fin + timedelta(days=1))
        for i in range(horizon):
            index_projete = len(buckets_historique) + i
            taux_estime = pente * index_projete + ordonnee
            taux_estime = max(0.0, min(1.0, taux_estime))
            projections.append({
                "periode_debut": debut_projete,
                "periode_fin": fin_projetee,
                "taux_presence_estime": round(taux_estime, 4),
            })
            debut_projete, fin_projetee = rapport_service.bornes_periode(granularite, fin_projetee + timedelta(days=1))

    return {
        "granularite": granularite,
        "id_service": id_service,
        "methode": "regression_lineaire_simple",
        "historique": historique,
        "prevision": projections,
        "avertissement": avertissement or (
            "Estimation indicative basée sur une régression linéaire simple sur l'historique récent "
            "(méthode statistique simple, cf. cahier des charges) — à interpréter avec prudence, "
            "sans valeur d'engagement."
        ),
    }


def _serie_historique(
    db: Session,
    granularite: TypePeriode,
    id_service: Optional[int],
    buckets: List[Tuple[date_, date_]],
) -> Tuple[List[dict], List[Tuple[int, float]]]:
    """Indicateurs période par période + série (indice, taux_presence) exploitable par un modèle."""
    historique: List[dict] = []
    points: List[Tuple[int, float]] = []
    for index, (debut, _fin) in enumerate(buckets):
        indicateurs = rapport_service.calculer_indicateurs(db, granularite, id_service, date_reference=debut)
        historique.append({
            "periode_debut": indicateurs["periode_debut"],
            "periode_fin": indicateurs["periode_fin"],
            "globaux": indicateurs["globaux"],
        })
        taux = indicateurs["globaux"]["taux_presence"]
        if taux is not None:
            points.append((index, taux))
    return historique, points


def _historique_ml_adaptatif(
    db: Session,
    granularite: TypePeriode,
    id_service: Optional[int],
    nombre_periodes_demande: int,
    date_reference: date_,
) -> Tuple[List[Tuple[date_, date_]], List[dict], List[Tuple[int, float]]]:
    """
    Calibre automatiquement la profondeur d'historique fournie au modèle ML.

    Le nombre de périodes demandé par l'appelant n'est qu'un point de départ :
    selon l'ancienneté des pointages, ces périodes peuvent être en grande
    partie vides (taux de présence indisponible), auquel cas le modèle ML
    n'avait pas assez de matière et retombait toujours sur la régression
    linéaire. On élargit donc la fenêtre par paliers tant que le nombre de
    points exploitables reste sous `_ML_MIN_POINTS`, jusqu'à
    `_ML_MAX_PERIODES`. À l'inverse, on ne conserve que les
    `_ML_MAX_PERIODES` périodes les plus récentes : au-delà, l'historique
    ancien dilue le signal récent sans améliorer la prévision.
    """
    nombre = max(2, min(nombre_periodes_demande, _ML_MAX_PERIODES))
    buckets = _buckets_recents(granularite, date_reference, nombre)
    historique, points = _serie_historique(db, granularite, id_service, buckets)

    while len(points) < _ML_MIN_POINTS and nombre < _ML_MAX_PERIODES:
        nombre = min(_ML_MAX_PERIODES, nombre + max(4, _ML_MIN_POINTS - len(points)))
        buckets = _buckets_recents(granularite, date_reference, nombre)
        historique, points = _serie_historique(db, granularite, id_service, buckets)

    # Trop de périodes vides en tête de série : on tronque avant le premier
    # point exploitable pour ne pas nourrir le modèle de trous.
    if points and points[0][0] > 0:
        decalage = points[0][0]
        buckets = buckets[decalage:]
        historique = historique[decalage:]
        points = [(i - decalage, v) for i, v in points]

    return buckets, historique, points


def prevision_ml(
    db: Session,
    granularite: TypePeriode,
    id_service: Optional[int] = None,
    nombre_periodes_historique: int = 6,
    horizon: int = 3,
    date_reference: Optional[date_] = None,
) -> dict:
    """
    Variante ML de `prevision()` : un modèle de gradient boosting
    (scikit-learn), entraîné à la demande sur l'historique récent, remplace
    la régression linéaire simple pour capter des dynamiques non linéaires.

    Repli automatique sur la régression linéaire existante si l'historique
    est trop court pour entraîner un modèle ML fiable (cf.
    `prevision_ml.NB_MIN_POINTS_ML`) : le champ `methode` de la réponse
    indique toujours ce qui a réellement été utilisé.
    """
    date_reference = date_reference or (date_.today() - timedelta(days=1))
    buckets_historique, historique, points_regression = _historique_ml_adaptatif(
        db, granularite, id_service, nombre_periodes_historique, date_reference
    )
    if not buckets_historique:
        return {
            "granularite": granularite,
            "id_service": id_service,
            "methode": "aucune",
            "historique": [],
            "prevision": [],
            "avertissement": "Aucune donnée de présence exploitable sur l'historique disponible.",
        }

    methode = "gradient_boosting_ml"
    valeurs_predites = prevision_ml_module_predire(points_regression, horizon)

    if valeurs_predites is None:
        coefficients = _regression_lineaire(points_regression)
        if coefficients is None:
            return {
                "granularite": granularite,
                "id_service": id_service,
                "methode": "aucune",
                "historique": historique,
                "prevision": [],
                "avertissement": (
                    "Historique insuffisant (au moins 2 périodes avec des données) pour "
                    "estimer une tendance, même avec la méthode de repli."
                ),
            }
        methode = "regression_lineaire_simple (repli, historique insuffisant pour le ML)"
        pente, ordonnee = coefficients
        valeurs_predites = [
            max(0.0, min(1.0, pente * (len(buckets_historique) + i) + ordonnee)) for i in range(horizon)
        ]

    projections: List[dict] = []
    _debut, dernier_fin = buckets_historique[-1]
    debut_projete, fin_projetee = rapport_service.bornes_periode(granularite, dernier_fin + timedelta(days=1))
    for valeur in valeurs_predites:
        projections.append({
            "periode_debut": debut_projete,
            "periode_fin": fin_projetee,
            "taux_presence_estime": round(valeur, 4),
        })
        debut_projete, fin_projetee = rapport_service.bornes_periode(granularite, fin_projetee + timedelta(days=1))

    return {
        "granularite": granularite,
        "id_service": id_service,
        "methode": methode,
        "historique": historique,
        "prevision": projections,
        "avertissement": (
            "Estimation indicative produite par un modèle de gradient boosting entraîné sur "
            "l'historique récent (prévision récursive multi-étapes) — à interpréter avec "
            "prudence, sans valeur d'engagement."
            if methode == "gradient_boosting_ml"
            else "Estimation indicative (méthode de repli) — à interpréter avec prudence, sans valeur d'engagement."
        ),
    }


def prevision_ml_module_predire(points_regression: List[Tuple[int, float]], horizon: int) -> Optional[List[float]]:
    """Petit indirect pour garder un nom explicite au niveau de l'appel ci-dessus."""
    return prevision_ml_module.entrainer_et_predire(points_regression, horizon)


def detection_anomalies_ml(
    db: Session,
    type_periode: TypePeriode,
    id_service: Optional[int] = None,
    date_reference: Optional[date_] = None,
) -> List[dict]:
    """
    "Repérer des comportements inhabituels" au sens large : compare le profil
    de chaque agent du périmètre (taux de présence, retards, absences,
    départs anticipés, heures travaillées) à celui du reste du groupe sur la
    période, via un Isolation Forest. Complète les règles à seuils fixes du
    module Anomalies, qui ne regardent qu'un indicateur à la fois.

    id_service=None -> comparaison entre tous les agents actifs (tous
    services confondus) ; sinon, comparaison restreinte aux agents du
    service.
    """
    indicateurs = rapport_service.calculer_indicateurs(db, type_periode, id_service, date_reference=date_reference)

    if id_service is not None:
        profils = indicateurs["detail_agents"]
    else:
        agents = _agents_du_perimetre(db, None)
        ids_agents = [a.id_agent for a in agents]
        compte_anomalies = rapport_service.compter_anomalies_par_type(
            db, ids_agents, indicateurs["periode_debut"], indicateurs["periode_fin"]
        )
        profils = []
        jours_ouvres_par_service: Dict[Optional[int], int] = {}
        for agent in agents:
            if agent.id_service not in jours_ouvres_par_service:
                jours_ouvres_par_service[agent.id_service] = rapport_service.jours_ouvres_service(
                    db, agent.id_service, indicateurs["periode_debut"], indicateurs["periode_fin"]
                )
            jours_ouvres = jours_ouvres_par_service[agent.id_service]
            heures = rapport_service.heures_travaillees_agent(
                db, agent.id_agent, indicateurs["periode_debut"], indicateurs["periode_fin"]
            )
            jours_pointes = rapport_service.jours_pointes_agent(
                db, agent.id_agent, indicateurs["periode_debut"], indicateurs["periode_fin"]
            )
            jours_conge = rapport_service.jours_conge_agent(
                db, agent.id_agent, agent.id_service, indicateurs["periode_debut"], indicateurs["periode_fin"]
            )
            profils.append(
                rapport_service.indicateurs_agent(
                    agent, jours_ouvres, heures, compte_anomalies, jours_pointes, jours_conge
                )
            )

    return anomalies_ml.detecter(profils)


def score_risque_agents(
    db: Session,
    id_service: Optional[int] = None,
    nombre_mois_historique: int = 7,
    date_reference: Optional[date_] = None,
) -> List[dict]:
    """
    Entraîne une régression sur l'historique mensuel de tous les agents du
    périmètre (mois M -> taux d'incident constaté au mois M+1), puis dérive,
    pour chaque agent, sa probabilité de connaître au moins un incident sur
    le mois à venir à partir de son dernier mois complet (cf. note dans
    `risque_agents` sur le choix de la régression plutôt qu'une
    classification binaire, plus discriminante entre agents à risques réels
    différents).

    Repli heuristique (sans ML, cf. `risque_agents.score_heuristique`) si
    l'historique global est trop court pour entraîner un modèle fiable — le
    champ `methode` du résultat indique toujours lequel des deux a été
    utilisé.
    """
    agents = _agents_du_perimetre(db, id_service)
    if not agents:
        return []

    date_reference = date_reference or (date_.today() - timedelta(days=1))

    # Calibrage automatique de la profondeur d'historique : le classifieur a
    # besoin d'au moins `_ML_MIN_ECHANTILLONS` paires (mois M -> mois M+1),
    # tous agents confondus. Avec peu d'agents, `nombre_mois_historique` seul
    # ne suffisait pas et le score retombait toujours sur l'heuristique ;
    # avec beaucoup d'agents, il faisait au contraire calculer bien plus de
    # mois que nécessaire. On dimensionne donc la fenêtre à partir de
    # l'effectif du périmètre, en la bornant des deux côtés.
    mois_necessaires = -(-_ML_MIN_ECHANTILLONS // max(1, len(agents))) + 1
    mois_retenus = max(nombre_mois_historique, mois_necessaires)
    mois_retenus = min(mois_retenus, _ML_MAX_MOIS_RISQUE, max(2, _ML_MAX_CALCULS_RISQUE // max(1, len(agents))))
    mois_retenus = max(2, mois_retenus)

    buckets = _buckets_recents(TypePeriode.MOIS, date_reference, mois_retenus + 1)
    noms_services = {s.id_service: s.nom_service for s in db.execute(select(Service)).scalars().all()}

    historique_par_agent: Dict[int, List[dict]] = {agent.id_agent: [] for agent in agents}
    for debut, fin in buckets:
        ids_agents = [a.id_agent for a in agents]
        compte_anomalies = rapport_service.compter_anomalies_par_type(db, ids_agents, debut, fin)
        jours_ouvres_par_service: Dict[Optional[int], int] = {}
        for agent in agents:
            if agent.id_service not in jours_ouvres_par_service:
                jours_ouvres_par_service[agent.id_service] = rapport_service.jours_ouvres_service(
                    db, agent.id_service, debut, fin
                )
            jours_ouvres = jours_ouvres_par_service[agent.id_service]
            heures = rapport_service.heures_travaillees_agent(db, agent.id_agent, debut, fin)
            jours_pointes = rapport_service.jours_pointes_agent(db, agent.id_agent, debut, fin)
            jours_conge = rapport_service.jours_conge_agent(db, agent.id_agent, agent.id_service, debut, fin)
            historique_par_agent[agent.id_agent].append(
                rapport_service.indicateurs_agent(
                    agent, jours_ouvres, heures, compte_anomalies, jours_pointes, jours_conge
                )
            )

    modele = risque_agents.entrainer(historique_par_agent)
    methode = "gradient_boosting_ml" if modele is not None else "heuristique (historique insuffisant pour le ML)"

    resultats = []
    for agent in agents:
        historique_agent = historique_par_agent[agent.id_agent]
        dernier_mois = historique_agent[-1]
        if dernier_mois["taux_presence"] is None:
            continue
        if modele is not None:
            score = risque_agents.predire_probabilite(modele, dernier_mois)
        else:
            score = risque_agents.score_heuristique(historique_agent)
        resultats.append({
            "id_agent": agent.id_agent,
            "matricule": agent.matricule,
            "nom": agent.nom,
            "prenom": agent.prenom,
            "id_service": agent.id_service,
            "nom_service": noms_services.get(agent.id_service, "Sans service"),
            "score_risque": score,
            "methode": methode,
        })

    resultats.sort(key=lambda a: -a["score_risque"])
    return resultats
