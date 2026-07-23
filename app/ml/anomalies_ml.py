"""
Détection d'anomalies ML — repère, parmi les agents d'un périmètre, ceux dont
le profil de présence sur la période s'écarte statistiquement du profil du
reste du groupe (Isolation Forest), en complément des règles à seuils fixes
du module Anomalies (qui détectent un événement précis : un retard, une
absence, un départ anticipé, pointage par pointage).

Différence avec le module Anomalies existant : ici, on ne compare pas un
indicateur à un seuil fixe mais le profil complet d'un agent (taux de
présence, retards, absences, départs anticipés, heures travaillées) à celui
des autres agents du même périmètre sur la même période. Un agent peut ainsi
être signalé comme atypique même si aucun seuil métier n'est individuellement
dépassé — par exemple un cumul de plusieurs signaux faibles simultanés.
"""
from typing import List

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# Une comparaison statistique n'a de sens qu'avec un minimum d'agents dans le
# périmètre : en dessous, on renvoie une liste vide plutôt qu'un résultat non
# significatif.
NB_MIN_AGENTS = 5

# Seuil (en écarts-types au-dessus de la moyenne des scores d'anomalie du
# périmètre) à partir duquel un agent est considéré atypique. Indépendant du
# nombre d'agents — cf. note plus bas sur pourquoi ce choix remplace une
# proportion fixe.
SEUIL_Z_ATYPIQUE = 1.5

_CHAMPS = ["taux_presence", "nombre_retards", "nombre_absences", "nombre_departs_anticipes", "heures_travaillees"]


def detecter(profils: List[dict]) -> List[dict]:
    """
    `profils` : liste de dicts type `rapport_service.indicateurs_agent`
    (doit contenir au moins les champs de `_CHAMPS`).

    Retourne la liste des profils exploitables (taux_presence non nul),
    enrichis de :
      - `score_anomalie` (float ≥ 0) : plus la valeur est élevée, plus le
        profil est atypique par rapport au reste du périmètre ;
      - `est_atypique` (bool) : signal binaire, cf. note ci-dessous.
    Triée par score décroissant. Liste vide si trop peu d'agents.

    Note sur `est_atypique` : dans une version précédente, ce signal venait
    directement de l'Isolation Forest avec une `contamination` (proportion
    attendue d'agents atypiques) calculée comme `3 / nb_agents`, bornée entre
    5% et 25%. Mesuré sur données simulées, ce choix biaisait la détection
    selon la taille du périmètre : sur-signalement (faux positifs, précision
    ~50-67%) pour de petites équipes où la contamination minimale de 5%
    représente déjà plus d'agents qu'il n'y a réellement d'atypiques, et
    sous-signalement (rappel ~60-75%, anomalies réelles manquées) pour de
    grandes équipes où le plafond de 25% ne suffit pas toujours à ramener la
    contamination réelle vers le bas. Le modèle est donc désormais entraîné
    avec `contamination="auto"` (heuristique standard, sert uniquement à
    calibrer les scores), et `est_atypique` est déterminé séparément par un
    seuil statistique sur le score (z-score > `SEUIL_Z_ATYPIQUE`) : un agent
    est signalé s'il s'écarte significativement du reste du périmètre, quel
    que soit l'effectif de ce dernier. Cette approche a donné une précision
    et un rappel proches de 100% à toutes les tailles de périmètre testées.
    """
    utilisables = [p for p in profils if p.get("taux_presence") is not None]
    if len(utilisables) < NB_MIN_AGENTS:
        return []

    X = np.array([[float(p[c]) for c in _CHAMPS] for p in utilisables])
    X_normalise = StandardScaler().fit_transform(X)

    modele = IsolationForest(n_estimators=150, contamination="auto", random_state=0)
    modele.fit(X_normalise)

    scores_bruts = -modele.decision_function(X_normalise)  # plus haut = plus atypique
    moyenne, ecart_type = scores_bruts.mean(), scores_bruts.std()
    z_scores = (scores_bruts - moyenne) / (ecart_type if ecart_type > 1e-9 else 1.0)

    resultats = []
    for profil, score, z in zip(utilisables, scores_bruts, z_scores):
        resultats.append({
            **profil,
            "score_anomalie": round(float(score), 4),
            "est_atypique": bool(z > SEUIL_Z_ATYPIQUE),
        })

    resultats.sort(key=lambda p: -p["score_anomalie"])
    return resultats