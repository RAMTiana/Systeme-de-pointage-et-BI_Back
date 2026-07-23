"""
Classifieur d'intention (assistant IA) — TF-IDF + régression logistique,
entraîné en mémoire sur un petit jeu de phrases types en français.

Objectif : reconnaître des formulations qui ne contiennent pas exactement
les mots-clés attendus (paraphrases, tournures différentes, oublis
d'accents) sans dépendre d'une API de langage externe — cohérent avec le
choix « 100% local » déjà fait pour l'assistant et pour le module BI.

Reste volontairement complémentaire au moteur à mots-clés existant
(`assistant_ia_service._detecter_intention_mots_cles`) : en cas de confiance
insuffisante du modèle (`SEUIL_CONFIANCE`), l'appelant retombe sur les
règles à mots-clés plutôt que de forcer une intention incertaine.
"""
from typing import List, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

SEUIL_CONFIANCE = 0.35

# Jeu d'entraînement volontairement petit et lisible : chaque ligne est une
# formulation plausible d'un utilisateur, associée à l'intention attendue.
# À enrichir au fil du temps avec des questions réellement posées (via le
# journal d'audit) si le besoin de reconnaissance s'affine.
_EXEMPLES: List[Tuple[str, str]] = [
    # rapport
    ("génère-moi un rapport mensuel", "rapport"),
    ("je veux exporter les données en pdf", "rapport"),
    ("peux-tu générer le rapport de la semaine", "rapport"),
    ("télécharge le rapport annuel en excel", "rapport"),
    ("j'ai besoin d'un export pour la direction", "rapport"),
    ("sors-moi le rapport du jour", "rapport"),
    ("prépare le rapport hebdomadaire au format tableur", "rapport"),
    # prevision
    ("quelle est la tendance de présence pour les prochains mois", "prevision"),
    ("peux-tu prédire l'assiduité future", "prevision"),
    ("quelle évolution attendre du taux de présence", "prevision"),
    ("fais-moi une prévision sur les 3 prochains mois", "prevision"),
    ("est-ce que la présence va s'améliorer ou se dégrader", "prevision"),
    ("comment va évoluer la ponctualité dans les mois qui viennent", "prevision"),
    # anomalies
    ("résume-moi les anomalies récentes", "anomalies"),
    ("y a-t-il des anomalies en attente de traitement", "anomalies"),
    ("combien d'absences ont été détectées ce mois-ci", "anomalies"),
    ("montre-moi les incidents de pointage récents", "anomalies"),
    ("des retards ont-ils été signalés récemment", "anomalies"),
    ("quel est l'état des anomalies non traitées", "anomalies"),
    # risque
    ("quels agents risquent d'être absents prochainement", "risque"),
    ("qui a le plus de risque de retard le mois prochain", "risque"),
    ("identifie les agents à surveiller", "risque"),
    ("quel est le score de risque des agents", "risque"),
    ("quels profils sont à risque d'absentéisme", "risque"),
    # question_rh
    ("combien d'agents avons-nous", "question_rh"),
    ("quel est le taux de présence aujourd'hui", "question_rh"),
    ("quel est l'effectif total", "question_rh"),
    ("qui sont les agents les plus ponctuels", "question_rh"),
    ("quels agents sont le plus souvent en retard", "question_rh"),
    ("quel service a le meilleur taux de présence", "question_rh"),
    ("compare les services entre eux", "question_rh"),
    ("combien de personnes travaillent ici", "question_rh"),
    ("quel service est le plus assidu", "question_rh"),
    # aide
    ("que peux-tu faire", "aide"),
    ("aide-moi", "aide"),
    ("quelles sont tes fonctionnalités", "aide"),
    ("comment je peux t'utiliser", "aide"),
]


def _construire_pipeline() -> Pipeline:
    textes = [t for t, _ in _EXEMPLES]
    labels = [l for _, l in _EXEMPLES]
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1)),
        # C plus élevé que la valeur par défaut (1.0) : avec un jeu
        # d'entraînement aussi petit, une régularisation plus faible donne
        # des probabilités mieux séparées entre classes proches, donc un
        # seuil de confiance plus discriminant.
        ("clf", LogisticRegression(max_iter=1000, C=10)),
    ])
    pipeline.fit(textes, labels)
    return pipeline


# Entraîné une seule fois au chargement du module (jeu de données fixe,
# volume négligeable) plutôt qu'à chaque appel de l'assistant.
_PIPELINE = _construire_pipeline()


def predire(message: str) -> Tuple[str, float]:
    """Retourne (intention, confiance) où confiance = probabilité de la classe prédite."""
    probabilites = _PIPELINE.predict_proba([message])[0]
    classes = _PIPELINE.classes_
    idx = int(probabilites.argmax())
    return classes[idx], float(probabilites[idx])
