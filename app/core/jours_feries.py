"""
Jours fériés officiels de Madagascar.

Base légale : Code du Travail (Loi n° 2024-014 du 14 août 2024, art. 115) et
décret n° 62-150 du 28 mars 1962 (modalités d'application des jours fériés
chômés et payés). La liste précise et les dates mobiles sont republiées
chaque année par décret gouvernemental (généralement fin décembre).

Ce module distingue trois catégories, dont la fiabilité du calcul diffère :

1. **Jours fixes** (`_JOURS_FIXES`) : même date civile chaque année
   (ex. 1er janvier). Calculables pour toute année, aucune maintenance requise.

2. **Jours mobiles chrétiens** (`_jours_mobiles_chretiens`) : calculés à partir
   de Pâques (algorithme de Meeus/Jones/Butcher, calendrier grégorien),
   valable pour toute année sans maintenance.

3. **Jours mobiles musulmans** (`_JOURS_MOBILES_ISLAM`) : Aïd el-Fitr (Korité)
   et Aïd el-Adha (Tabaski). Dépendent du calendrier hégirien (observation
   lunaire) et NE PEUVENT PAS être calculés par une formule fixe de façon
   fiable. Ce module fournit des dates approximatives pour les années
   couvertes ci-dessous ; À VÉRIFIER ET COMPLÉTER chaque année à partir du
   décret officiel publié par le gouvernement malgache (généralement en fin
   d'année pour l'année suivante). Une année absente de ce dictionnaire est
   silencieusement ignorée pour ces deux fêtes (pas d'erreur, mais pas de
   jour chômé détecté non plus).

Remarque : la Journée internationale de la femme (8 mars) est, selon les
sources consultées, une fête catégorielle (destinée aux femmes) plutôt qu'une
fermeture générale de l'administration ; elle n'est donc volontairement PAS
incluse dans la liste appliquée par défaut (`est_jour_ferie`). Ajustez
`_JOURS_FIXES` si la réalité de votre organisme diffère.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Dict, Optional

# --------------------------------------------------------------------
# 1. Jours fixes (même date civile chaque année)
# --------------------------------------------------------------------
_JOURS_FIXES: Dict[tuple[int, int], str] = {
    (1, 1): "Jour de l'An",
    (3, 29): "Journée des Martyrs (commémoration du 29 mars 1947)",
    (5, 1): "Fête du Travail",
    (6, 26): "Fête de l'Indépendance",
    (8, 15): "Assomption",
    (11, 1): "Toussaint",
    (12, 25): "Noël",
}

# --------------------------------------------------------------------
# 2. Jours mobiles chrétiens (calculés à partir de Pâques)
# --------------------------------------------------------------------

@lru_cache(maxsize=None)
def _paques(annee: int) -> date:
    """Dimanche de Pâques (calendrier grégorien) — algorithme de Meeus/Jones/Butcher."""
    a = annee % 19
    b = annee // 100
    c = annee % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mois = (h + l - 7 * m + 114) // 31
    jour = ((h + l - 7 * m + 114) % 31) + 1
    return date(annee, mois, jour)


def _jours_mobiles_chretiens(annee: int) -> Dict[date, str]:
    paques = _paques(annee)
    return {
        paques + timedelta(days=1): "Lundi de Pâques",
        paques + timedelta(days=39): "Ascension",
        paques + timedelta(days=50): "Lundi de Pentecôte",
    }


# --------------------------------------------------------------------
# 3. Jours mobiles musulmans — calendrier hégirien, À METTRE À JOUR CHAQUE
#    ANNÉE à partir du décret officiel. Dates approximatives (± 1 jour
#    possible selon l'observation locale du croissant lunaire).
# --------------------------------------------------------------------
_JOURS_MOBILES_ISLAM: Dict[int, Dict[date, str]] = {
    2026: {
        date(2026, 3, 20): "Aïd el-Fitr (Korité)",
        date(2026, 5, 27): "Aïd el-Adha (Tabaski)",
    },
    # TODO : ajouter 2027, 2028... dès publication du décret officiel annuel.
}


def jours_feries_annee(annee: int) -> Dict[date, str]:
    """Renvoie {date: libellé} de tous les jours fériés connus pour `annee`."""
    resultat: Dict[date, str] = {}
    for (mois, jour), libelle in _JOURS_FIXES.items():
        resultat[date(annee, mois, jour)] = libelle
    resultat.update(_jours_mobiles_chretiens(annee))
    resultat.update(_JOURS_MOBILES_ISLAM.get(annee, {}))
    return resultat


def libelle_jour_ferie(jour: date) -> Optional[str]:
    """Libellé du jour férié si `jour` en est un, sinon None."""
    return jours_feries_annee(jour.year).get(jour)


def est_jour_ferie(jour: date) -> bool:
    """True si `jour` est un jour férié officiel malgache (indépendamment du week-end)."""
    return jour in jours_feries_annee(jour.year)
