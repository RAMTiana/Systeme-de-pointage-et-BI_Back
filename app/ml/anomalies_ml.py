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

_CHAMPS = ["taux_presence", "nombre_retards", "nombre_absences", "nombre_departs_anticipes", "heures_travaillees"]


def detecter(profils: List[dict]) -> List[dict]:
    """
    `profils` : liste de dicts type `rapport_service.indicateurs_agent`
    (doit contenir au moins les champs de `_CHAMPS`).

    Retourne la liste des profils exploitables (taux_presence non nul),
    enrichis de :
      - `score_anomalie` (float ≥ 0) : plus la valeur est élevée, plus le
        profil est atypique par rapport au reste du périmètre ;
      - `est_atypique` (bool) : signal binaire produit par le modèle.
    Triée par score décroissant. Liste vide si trop peu d'agents.
    """
    utilisables = [p for p in profils if p.get("taux_presence") is not None]
    if len(utilisables) < NB_MIN_AGENTS:
        return []

    X = np.array([[float(p[c]) for c in _CHAMPS] for p in utilisables])
    X_normalise = StandardScaler().fit_transform(X)

    # contamination : proportion attendue d'agents atypiques — bornée pour
    # rester raisonnable quel que soit l'effectif du périmètre.
    contamination = min(0.25, max(0.05, 3 / len(utilisables)))
    modele = IsolationForest(n_estimators=150, contamination=contamination, random_state=0)
    modele.fit(X_normalise)

    scores_bruts = modele.decision_function(X_normalise)  # plus petit = plus atypique
    predictions = modele.predict(X_normalise)  # -1 = atypique, 1 = normal

    resultats = []
    for profil, score, prediction in zip(utilisables, scores_bruts, predictions):
        resultats.append({
            **profil,
            "score_anomalie": round(float(-score), 4),  # inversé : plus haut = plus atypique
            "est_atypique": bool(prediction == -1),
        })

    resultats.sort(key=lambda p: -p["score_anomalie"])
    return resultats
