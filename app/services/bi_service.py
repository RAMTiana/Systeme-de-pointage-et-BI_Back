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
from app.services import rapport_service

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

    par_service: Dict[Optional[int], dict] = {}

    def _compteur_service(id_s: Optional[int], nom_s: str) -> dict:
        return par_service.setdefault(id_s, {
            "id_service": id_s, "nom_service": nom_s,
            "nombre_agents_attendus": 0, "nombre_presents": 0, "nombre_sortis": 0,
            "nombre_absents": 0, "nombre_retardataires": 0,
        })

    services_par_id = {s.id_service: s.nom_service for s in db.execute(select(Service)).scalars().all()}

    nombre_presents = nombre_sortis = nombre_absents = nombre_retardataires = nombre_attendus = 0

    for agent in agents:
        attendu = _service_travaille_ce_jour(jours_horaire, agent.id_service, jour_semaine)
        statut_jour = dernier_pointage.get(agent.id_agent)
        est_retardataire = agent.id_agent in ids_retardataires

        if attendu:
            nombre_attendus += 1
        if statut_jour == TypePointage.ENTREE:
            nombre_presents += 1
        elif statut_jour == TypePointage.SORTIE:
            nombre_sortis += 1
        elif attendu:
            nombre_absents += 1
        if est_retardataire:
            nombre_retardataires += 1

        nom_s = services_par_id.get(agent.id_service, "Sans service")
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
    critere: Literal["ponctualite", "retards"] = "ponctualite",
    limite: int = 10,
) -> List[dict]:
    """
    "Identifier les agents les plus ponctuels ou les plus souvent en retard" :
    classement global (id_service=None) ou restreint à un service, sur la
    période demandée.
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
        indicateurs = rapport_service.indicateurs_agent(agent, jours_ouvres, heures, compte_anomalies)
        resultats.append({
            **indicateurs,
            "id_service": agent.id_service,
            "nom_service": noms_services.get(agent.id_service, "Sans service"),
        })

    if critere == "ponctualite":
        resultats.sort(key=lambda a: (-(a["taux_presence"] or 0), a["nombre_retards"]))
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
    buckets_historique = _buckets_recents(granularite, date_reference, nombre_periodes_historique)

    historique = []
    points_regression: List[Tuple[int, float]] = []
    for index, (debut, _fin) in enumerate(buckets_historique):
        indicateurs = rapport_service.calculer_indicateurs(db, granularite, id_service, date_reference=debut)
        taux = indicateurs["globaux"]["taux_presence"]
        historique.append({
            "periode_debut": indicateurs["periode_debut"],
            "periode_fin": indicateurs["periode_fin"],
            "globaux": indicateurs["globaux"],
        })
        if taux is not None:
            points_regression.append((index, taux))

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
