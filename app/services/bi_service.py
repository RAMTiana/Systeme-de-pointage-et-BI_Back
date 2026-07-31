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

from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import JourSemaine, StatutAgent, StatutPointage, TypeAnomalie, TypePeriode, TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.models.service import Service
from app.services import conge_service, rapport_service
from app.ml import anomalies_ml, risque_agents
# Alias explicite : le service expose lui aussi une fonction `prevision_ml`,
# qui masquerait le module importé sous le même nom (AttributeError -> 500).
from app.ml import prevision_ml as prevision_ml_module

# Dupliqué depuis anomalie_service/rapport_service (même convention) plutôt
# qu'une dépendance croisée sur une constante privée d'un autre module.
_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]

_MAX_BUCKETS = 60  # garde-fou contre une plage de dates trop large en granularité fine

# Bornes de l'historique fourni aux modèles ML par le module BI.
# Trop peu de points -> le modèle retombe systématiquement sur le repli
# statistique ; trop de points -> l'historique ancien (organisation, effectifs
# différents) dilue le signal récent et alourdit inutilement chaque appel.
_ML_MIN_POINTS = 8       # objectif minimal de périodes exploitables pour la prévision
_ML_MAX_PERIODES = 24    # plafond absolu de périodes explorées pour la prévision
_ML_MIN_ECHANTILLONS = 30   # objectif d'exemples (agent x mois) pour le score de risque
_ML_MAX_MOIS_RISQUE = 18    # plafond de mois d'historique pour le score de risque
_ML_MAX_CALCULS_RISQUE = 600  # plafond agents x mois, pour garder un temps de réponse raisonnable


# --------------------------------------------------------------------
# Tableau de bord opérationnel (temps réel)
# --------------------------------------------------------------------

def _agents_du_perimetre(db: Session, id_service: Optional[int]) -> List[Agent]:
    stmt = select(Agent).where(Agent.statut == StatutAgent.ACTIF)
    if id_service is not None:
        stmt = stmt.where(Agent.id_service == id_service)
    return list(db.execute(stmt).scalars().all())


def _service_travaille_ce_jour(jours_horaire: Dict[Optional[int], set], id_service: Optional[int], jour_semaine: JourSemaine) -> bool:
    jours = jours_horaire.get(id_service)
    if not jours:
        return True  # pas d'horaire de référence -> pas de base pour exclure ce jour
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
        attendu = _service_travaille_ce_jour(jours_horaire, agent.id_service, jour_semaine) and (
            agent.id_agent not in ids_en_conge
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


# --------------------------------------------------------------------
# Tendances (évolution des indicateurs sur une période définie)
# --------------------------------------------------------------------

def _buckets_dans_plage(granularite: TypePeriode, date_debut: date_, date_fin: date_) -> List[Tuple[date_, date_]]:
    debut, fin = rapport_service.bornes_periode(granularite, date_debut)
    buckets: List[Tuple[date_, date_]] = []
    while debut <= date_fin:
        buckets.append((debut, fin))
        if len(buckets) > _MAX_BUCKETS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Plage trop large pour cette granularité (plus de {_MAX_BUCKETS} périodes). Réduisez la plage ou choisissez une granularité plus large.",
            )
        debut, fin = rapport_service.bornes_periode(granularite, fin + timedelta(days=1))
    return buckets


def _buckets_recents(granularite: TypePeriode, date_reference: date_, nombre: int) -> List[Tuple[date_, date_]]:
    """Les `nombre` dernières périodes complètes se terminant dans la période contenant `date_reference`."""
    _, fin = rapport_service.bornes_periode(granularite, date_reference)
    buckets: List[Tuple[date_, date_]] = []
    for _ in range(nombre):
        debut, f = rapport_service.bornes_periode(granularite, fin)
        buckets.append((debut, f))
        fin = debut - timedelta(days=1)
    buckets.reverse()
    return buckets


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