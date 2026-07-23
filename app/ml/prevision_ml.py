"""
Prévision ML — modèle de gradient boosting (scikit-learn) pour le taux de
présence, en complément de la régression linéaire simple déjà en place dans
`bi_service.prevision` (conservée comme repli).

Approche : prévision récursive multi-étapes sur un historique court (6 à 24
périodes). Le seul signal fiable sur un historique aussi réduit est la série
du taux de présence elle-même ; les caractéristiques (« features ») sont donc
construites à partir de cette série :
  - indice              : position dans la série (tendance)
  - retard_1, retard_2   : valeurs des 1re et 2e périodes précédentes
  - moyenne_mobile_3     : moyenne glissante sur les 3 dernières périodes

Le modèle est ré-entraîné à chaque appel plutôt que persisté sur disque : le
volume de données (quelques dizaines de points au maximum) rend cela
largement suffisant en performance, et évite la complexité d'un pipeline de
ré-entraînement périodique pour un historique aussi réduit.
"""
from typing import List, Optional, Tuple

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

# En dessous de ce nombre de points historiques valides, le modèle ML n'a pas
# assez de matière pour être plus fiable qu'un repli simple : on laisse
# `bi_service.prevision` retomber sur la régression linéaire existante.
NB_MIN_POINTS_ML = 5


def _construire_caracteristiques(serie: List[float], indice: int) -> List[float]:
    retard_1 = serie[indice - 1] if indice - 1 >= 0 else serie[0]
    retard_2 = serie[indice - 2] if indice - 2 >= 0 else retard_1
    fenetre = serie[max(0, indice - 3):indice] or [serie[0]]
    moyenne_mobile = sum(fenetre) / len(fenetre)
    return [float(indice), retard_1, retard_2, moyenne_mobile]


def entrainer_et_predire(points: List[Tuple[int, float]], horizon: int) -> Optional[List[float]]:
    """
    `points` : liste (indice, taux_presence) sur les périodes historiques
    disponibles (indices consécutifs, cf. `bi_service.prevision`).

    Retourne `horizon` valeurs prédites (bornées à [0, 1]), ou `None` si
    l'historique est trop court pour qu'un modèle soit entraîné de façon
    fiable (cf. `NB_MIN_POINTS_ML`).
    """
    if len(points) < NB_MIN_POINTS_ML:
        return None

    serie = [y for _, y in sorted(points, key=lambda p: p[0])]

    X = [_construire_caracteristiques(serie, i) for i in range(len(serie))]
    y = list(serie)

    modele = GradientBoostingRegressor(
        n_estimators=80, max_depth=2, learning_rate=0.1, random_state=0
    )
    modele.fit(np.array(X), np.array(y))

    serie_etendue = list(serie)
    predictions: List[float] = []
    for _ in range(horizon):
        indice = len(serie_etendue)
        caracteristiques = np.array([_construire_caracteristiques(serie_etendue, indice)])
        valeur = float(modele.predict(caracteristiques)[0])
        valeur = max(0.0, min(1.0, valeur))
        predictions.append(round(valeur, 4))
        serie_etendue.append(valeur)  # prédiction récursive : la sortie nourrit l'entrée suivante

    return predictions
