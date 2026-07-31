# Améliorations ML — BI et Assistant IA

3 fichiers modifiés (chemins identiques à ton projet, à copier par-dessus) :
- `app/ml/risque_agents.py`
- `app/services/bi_service.py`
- `app/services/assistant_ia_service.py`

`app/ml/anomalies_ml.py` n'a **pas** été touché : il utilise déjà un z-score
robuste (médiane + MAD), vérifié par simulation comme fiable (précision
~0.95-1.00, rappel ~0.96-1.00 sur des scénarios réalistes).

## 1. Incohérence prévision assistant ↔ dashboard BI (corrigé)

Le dashboard BI utilisait déjà `/bi/prevision-ml` (gradient boosting), mais
l'assistant appelait `bi_service.prevision()` — la régression linéaire
simple. Deux méthodes différentes sur la même donnée = deux résultats
possiblement différents. L'assistant utilise maintenant
`bi_service.prevision_ml()`, identique au dashboard, et affiche la méthode
réellement utilisée (ML ou repli régression) en clair.

## 2. Score de risque agent — deux bugs distincts, vérifiés par simulation

**a) Étiquette d'entraînement trop grossière.** L'ancien modèle classait
"incident au mois suivant : oui/non". Sur données simulées réalistes, ~89%
des exemples finissent positifs (presque tout agent connaît un incident un
mois ou l'autre), ce qui aplatit la capacité du modèle à différencier un
agent réellement à risque d'un agent ponctuel (corrélation avec le risque
réel simulé : 0.53). Remplacé par une **régression sur le taux d'incident**
du mois suivant (conserve l'info de sévérité), puis conversion en
probabilité via un modèle de Poisson simple. Corrélation mesurée après
correctif : 0.65-0.69.

**b) Repli heuristique non lissé + bug de calibration découvert en cours de
route.** L'ancien repli (`score_heuristique`) ne regardait que le dernier
mois brut, sans lissage : un mois isolé (même justifié) faisait bondir le
score. Corrigé par une moyenne pondérée sur les 3 derniers mois (poids
décroissants). En implémentant la conversion en probabilité, j'ai
initialement réutilisé `jours_ouvres` comme coefficient — dans la formule
`1 - exp(-jours_ouvres × taux)`, ça reconstitue exactement le nombre brut
d'incidents du mois, ce qui donnait déjà 63% de "risque" pour un seul
retard dans le mois. Repéré et corrigé avec un coefficient fixe (2.0, dans
l'esprit du multiplicateur de l'ancienne heuristique) et une saturation
progressive au lieu d'un plafonnement brutal à 100%.

Vérifié après correctif : agent régulier → 0%, pic isolé sur fond régulier
→ ~22%, problème récurrent sur 3 mois → ~39% (jamais de saturation
artificielle).

## 3. Assistant IA — plus de contexte dans les réponses

La réponse "agents à risque" indique maintenant le nombre total d'agents
évalués et une description en clair de la méthode utilisée (plutôt que la
chaîne technique brute), pour réduire l'impression de données incomplètes
ou de résultat sorti de nulle part.

## Sur la demande de précision à 99%

Honnêtement : aucun modèle statistique entraîné sur quelques dizaines de
points historiques ne peut *garantir* 99% de précision — ce serait
malhonnête de te le promettre. Ce qui a été fait ici, c'est corriger des
sources d'erreur réelles et mesurables (biais de classe, bug de
calibration, incohérence inter-modules) avec des chiffres vérifiés par
simulation à chaque étape, pas des ajustements à l'aveugle. La qualité
réelle du ML dans ton système va aussi continuer à s'améliorer
naturellement à mesure que le système accumule plus de mois d'historique
réel (les seuils `NB_MIN_ECHANTILLONS` / `NB_MIN_POINTS_ML` existants
gèrent déjà le repli propre en attendant).

## À tester de ton côté

- Relance `/bi/prevision` vs le nouveau comportement de l'assistant pour
  confirmer la cohérence.
- Regarde `/bi/score-risque` sur quelques mois de données réelles : les
  scores devraient être mieux différenciés et plus stables d'un mois à
  l'autre qu'avant.
- Si `detection_anomalies_ml` (agents-ml) te semble encore signaler trop ou
  pas assez d'agents sur tes vraies données, dis-le-moi avec des exemples
  concrets (effectif du service, nombre d'agents signalés attendu) — le
  réglage actuel (déjà robuste en théorie) peut avoir besoin d'un ajustement
  fin spécifique à la distribution réelle de tes données.
