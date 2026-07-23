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

# Jeu d'entraînement lisible : chaque ligne est une formulation plausible
# d'un utilisateur, associée à l'intention attendue.
#
# Historique : la version initiale (37 exemples, 4 à 9 par classe) était en
# net sur-apprentissage — mesuré en validation croisée Leave-One-Out :
# 100% d'accuracy sur l'entraînement mais seulement ~49% en généralisation
# (à comparer à ~17% pour un tirage aléatoire entre les 6 classes). Le jeu a
# donc été élargi (37 -> 71 exemples, 8 à 15 par classe) avec des
# formulations plus variées (registres différents, tournures indirectes),
# ce qui porte la CV LOO à ~65% avec les réglages d'origine.
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
    ("fais-moi un pdf des présences", "rapport"),
    ("exporte-moi ça en excel stp", "rapport"),
    ("je voudrais un document récapitulatif du mois", "rapport"),
    ("peux-tu me sortir un fichier avec les chiffres", "rapport"),
    ("génère le rapport annuel", "rapport"),
    ("j'aimerais télécharger les statistiques de la semaine", "rapport"),
    ("crée-moi un export du trimestre", "rapport"),
    ("donne-moi un rapport imprimable", "rapport"),
    # prevision
    ("quelle est la tendance de présence pour les prochains mois", "prevision"),
    ("peux-tu prédire l'assiduité future", "prevision"),
    ("quelle évolution attendre du taux de présence", "prevision"),
    ("fais-moi une prévision sur les 3 prochains mois", "prevision"),
    ("est-ce que la présence va s'améliorer ou se dégrader", "prevision"),
    ("comment va évoluer la ponctualité dans les mois qui viennent", "prevision"),
    ("à quoi ressemblera la présence le trimestre prochain", "prevision"),
    ("dis-moi ce que tu anticipes pour l'assiduité", "prevision"),
    ("as-tu une projection pour les mois à venir", "prevision"),
    ("quel est le pronostic pour la présence future", "prevision"),
    ("où en sera le taux de présence dans deux mois", "prevision"),
    ("peux-tu anticiper les prochains chiffres de présence", "prevision"),
    # anomalies
    ("résume-moi les anomalies récentes", "anomalies"),
    ("y a-t-il des anomalies en attente de traitement", "anomalies"),
    ("combien d'absences ont été détectées ce mois-ci", "anomalies"),
    ("montre-moi les incidents de pointage récents", "anomalies"),
    ("des retards ont-ils été signalés récemment", "anomalies"),
    ("quel est l'état des anomalies non traitées", "anomalies"),
    ("il y a eu des soucis de pointage cette semaine ?", "anomalies"),
    ("des événements bizarres dans les pointages ?", "anomalies"),
    ("où en est-on sur les anomalies en cours", "anomalies"),
    ("des retards ou absences non justifiés récemment", "anomalies"),
    ("fais le point sur les incidents récents", "anomalies"),
    ("combien d'anomalies restent à traiter", "anomalies"),
    # risque
    ("quels agents risquent d'être absents prochainement", "risque"),
    ("qui a le plus de risque de retard le mois prochain", "risque"),
    ("identifie les agents à surveiller", "risque"),
    ("quel est le score de risque des agents", "risque"),
    ("quels profils sont à risque d'absentéisme", "risque"),
    ("quels agents devraient être surveillés de près", "risque"),
    ("y a-t-il des agents avec un profil inquiétant", "risque"),
    ("dis-moi qui a des chances d'être en retard bientôt", "risque"),
    ("quels sont les agents les plus exposés au risque d'absence", "risque"),
    ("qui présente un risque élevé ce mois-ci", "risque"),
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
    ("combien de salariés sommes-nous", "question_rh"),
    ("qui est le plus ponctuel de l'équipe", "question_rh"),
    ("quel est le classement des agents les plus en retard", "question_rh"),
    ("quel service se démarque niveau présence", "question_rh"),
    ("on est combien au total", "question_rh"),
    # aide
    ("que peux-tu faire", "aide"),
    ("aide-moi", "aide"),
    ("quelles sont tes fonctionnalités", "aide"),
    ("comment je peux t'utiliser", "aide"),
    ("qu'est-ce que tu sais faire", "aide"),
    ("montre-moi tes capacités", "aide"),
    ("j'ai besoin d'aide pour commencer", "aide"),
    ("comment ça marche", "aide"),
]


def _construire_pipeline() -> Pipeline:
    textes = [t for t, _ in _EXEMPLES]
    labels = [l for _, l in _EXEMPLES]
    pipeline = Pipeline([
        # N-grammes de CARACTÈRES (3 à 5) plutôt que de mots : avec un
        # vocabulaire aussi restreint, des n-grammes de mots ne se
        # recoupent presque jamais d'une paraphrase à l'autre (chaque
        # formulation devient quasi unique aux yeux du modèle -> sur-
        # apprentissage). Les n-grammes de caractères, eux, partagent des
        # sous-chaînes (« présen », « retar », « anomal »...) entre des
        # formulations différentes et entre variantes orthographiques
        # (accents, féminin/pluriel), donc généralisent nettement mieux.
        # Mesuré en validation croisée Leave-One-Out sur le jeu d'exemples
        # actuel : ~65% avec des n-grammes de mots (1,2) contre ~83% avec
        # ces n-grammes de caractères (3,5), à jeu de données identique.
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)),
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