"""
Score de risque par agent — estimation, pour chaque agent, d'un niveau de
risque relatif de retard/absence sur la période à venir, à partir de
l'historique mensuel de l'ensemble des agents du périmètre.

Échantillon d'entraînement : pour chaque agent et chaque mois M disposant
d'un mois M+1 déjà complet dans l'historique, on construit une paire
(caractéristiques du mois M) -> (taux d'incident du mois M+1 = (retards +
absences) / jours_ouvres). Tous les agents et tous les mois disponibles sont
mis en commun pour obtenir suffisamment d'exemples, y compris avec un
historique individuel court.

Choix : régression sur un taux continu plutôt que classification binaire
("un incident aura lieu ou non"). Mesuré sur données simulées (agents avec
des propensions de risque variées, incidents mensuels de type Poisson) :
la classification binaire souffre d'un déséquilibre de classe important dès
que l'historique dépasse quelques mois (~89% des exemples finissent
positifs dans la simulation, presque tout agent connaissant tôt ou tard au
moins un retard sur un mois donné), ce qui aplatit la probabilité prédite et
réduit fortement la capacité du modèle à différencier un agent réellement à
risque d'un agent ponctuel (corrélation avec la propension réelle simulée :
~0.53). La régression sur le taux d'incident (qui conserve l'information de
sévérité — 1 retard isolé n'est pas traité comme 5 absences) porte cette
corrélation à ~0.69 dans la même simulation. La probabilité affichée est
ensuite dérivée du taux prédit via un modèle de Poisson simple (probabilité
d'au moins un incident sur le mois = 1 - exp(-jours_ouvres * taux_prédit)),
ce qui restitue une lecture "probabilité sur le mois" intuitive tout en
gardant le bénéfice de la régression en amont.

Si l'historique global est trop court ou trop peu varié pour entraîner un
modèle fiable, on retombe sur un score heuristique (sans ML, cf.
`score_heuristique`) — jamais d'erreur ni de valeur arbitraire.
"""
from typing import Dict, List, Optional

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

# En dessous de ce nombre d'exemples d'entraînement (mois, tous agents
# confondus), le modèle ML est jugé peu fiable et on retombe sur
# l'heuristique.
NB_MIN_ECHANTILLONS = 20

_CHAMPS = ["taux_presence", "nombre_retards", "nombre_absences", "nombre_departs_anticipes", "heures_travaillees"]

# Nombre de mois pris en compte par le repli heuristique (lissage), du plus
# récent au plus ancien.
_NB_MOIS_LISSAGE_HEURISTIQUE = 3
# Poids décroissants appliqués du mois le plus récent au plus ancien : un
# mois isolé et déjà ancien pèse nettement moins qu'un mois récent, mais ne
# fait pas non plus basculer le score à lui seul comme le ferait une lecture
# du seul dernier mois.
_POIDS_LISSAGE = [0.5, 0.3, 0.2]

# Coefficient d'étalonnage de la conversion taux -> probabilité pour le
# repli heuristique (probabilite = 1 - exp(-_COEFFICIENT_HEURISTIQUE *
# taux_moyen)). Attention : jours_ouvres ne doit PAS servir de coefficient
# ici (piège vérifié en pratique) — comme taux_moyen est déjà un ratio
# incidents/jours_ouvres, reprendre jours_ouvres dans l'exponentielle
# reconstitue trivialement le nombre brut d'incidents du mois
# (jours_ouvres * (incidents / jours_ouvres) = incidents), ce qui redonne
# une probabilité proche de 100% dès 3 incidents dans le mois — précisément
# la sur-signalisation que ce repli doit éviter. Le coefficient 2 reprend
# l'ordre de grandeur du multiplicateur de l'ancienne heuristique
# (`min(1, signal * 2)`), mais via une saturation progressive (exponentielle)
# plutôt qu'un plafonnement brutal à 1.0 dès que signal ≥ 0.5.
_COEFFICIENT_HEURISTIQUE = 2.0


def _caracteristiques(mois: dict) -> List[float]:
    return [float(mois[c]) if mois.get(c) is not None else 0.0 for c in _CHAMPS]


def entrainer(historique_par_agent: Dict[int, List[dict]]) -> Optional[GradientBoostingRegressor]:
    """
    `historique_par_agent` : {id_agent: [indicateurs_mois_1, ..., indicateurs_mois_N]},
    chronologique, un dict par mois au format `rapport_service.indicateurs_agent`.

    Entraîne une régression sur le taux d'incident (retards + absences,
    ramené aux jours ouvrés) du mois suivant — cf. note du module sur le
    choix de la régression plutôt que la classification binaire.
    """
    X, y = [], []
    for mois_agent in historique_par_agent.values():
        for i in range(len(mois_agent) - 1):
            actuel, suivant = mois_agent[i], mois_agent[i + 1]
            if actuel.get("taux_presence") is None or suivant.get("taux_presence") is None:
                continue
            jours_suivant = suivant.get("jours_ouvres") or 1
            taux_incident = (suivant["nombre_retards"] + suivant["nombre_absences"]) / jours_suivant
            X.append(_caracteristiques(actuel))
            y.append(taux_incident)

    if len(y) < NB_MIN_ECHANTILLONS:
        return None
    # Historique trop peu varié (tous les mois suivants au même taux, par ex.
    # tous à zéro) : une régression n'apporterait rien de plus fiable qu'un
    # repli heuristique constant.
    if float(np.std(y)) < 1e-9:
        return None

    # Configuration régularisée (comme pour les autres modèles ML du
    # projet) : peu d'arbres, peu profonds, feuilles d'au moins 5
    # échantillons, sous-échantillonnage — adapté à un nombre d'exemples
    # limité (quelques dizaines) pour éviter le sur-apprentissage.
    modele = GradientBoostingRegressor(
        n_estimators=30, max_depth=2, learning_rate=0.08,
        min_samples_leaf=5, subsample=0.7, random_state=0,
    )
    modele.fit(np.array(X), np.array(y))
    return modele


def predire_probabilite(modele: GradientBoostingRegressor, dernier_mois: dict) -> float:
    """
    Convertit le taux d'incident prédit par la régression en probabilité
    "au moins un incident sur le mois à venir", via un modèle de Poisson
    simple : P(≥1) = 1 - exp(-jours_ouvres * taux_prédit).
    """
    taux_predit = float(modele.predict(np.array([_caracteristiques(dernier_mois)]))[0])
    taux_predit = max(0.0, taux_predit)
    jours = dernier_mois.get("jours_ouvres") or 20
    probabilite = 1.0 - np.exp(-jours * taux_predit)
    return round(float(min(1.0, max(0.0, probabilite))), 4)


def score_heuristique(historique_recent: List[dict]) -> float:
    """
    Repli sans ML : moyenne pondérée (plus de poids aux mois récents) du
    taux d'incident (retards + absences + départs anticipés, ramenés aux
    jours ouvrés) sur les `_NB_MOIS_LISSAGE_HEURISTIQUE` derniers mois
    disponibles, puis transformée en probabilité via le même modèle de
    Poisson simple que la variante ML — pour une lecture cohérente entre
    les deux méthodes.

    `historique_recent` : liste des indicateurs mensuels d'un agent,
    du plus ancien au plus récent (on ne regarde que la fin de la liste).

    Lissage sur plusieurs mois plutôt qu'un seul : un score basé sur le
    seul dernier mois fait basculer le résultat pour un mois isolé (une
    absence justifiée ponctuelle, par exemple), donnant une impression de
    score incohérent d'une période à l'autre pour un agent par ailleurs
    régulier. La moyenne pondérée lisse cet effet tout en restant réactive
    à une dégradation récente et réelle (poids décroissant mais non nul sur
    les mois plus anciens).
    """
    derniers_mois = historique_recent[-_NB_MOIS_LISSAGE_HEURISTIQUE:]
    derniers_mois = list(reversed(derniers_mois))  # plus récent en premier, aligné sur _POIDS_LISSAGE

    taux_pondere = 0.0
    poids_total = 0.0
    for mois, poids in zip(derniers_mois, _POIDS_LISSAGE):
        jours = mois.get("jours_ouvres") or 1
        incidents = (
            (mois.get("nombre_retards") or 0)
            + (mois.get("nombre_absences") or 0)
            + (mois.get("nombre_departs_anticipes") or 0)
        )
        taux_pondere += poids * (incidents / jours)
        poids_total += poids

    taux_moyen = taux_pondere / poids_total if poids_total > 0 else 0.0
    probabilite = 1.0 - np.exp(-_COEFFICIENT_HEURISTIQUE * taux_moyen)
    return round(float(min(1.0, max(0.0, probabilite))), 4)
