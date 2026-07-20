"""
Résolution de l'horaire effectif d'un service pour un jour donné.

Centralise une règle utilisée à la fois par `pointage_service`
(`_detecter_anomalie_horaire` — retard / départ anticipé) et par
`anomalie_service` (`detecter_absences`) : un service peut définir un
horaire précis par jour dans `HoraireReference`, mais ce n'est pas
obligatoire. En l'absence de configuration explicite, on applique
l'horaire standard de l'entreprise (8h-17h, personnalisable via les
paramètres système `heure_debut_travail` / `heure_fin_travail`) sur les
jours ouvrés par défaut (lundi-vendredi), afin que le contrôle des
retards/absences — et donc la page Anomalies — fonctionne dès
l'installation, sans configuration préalable obligatoire de chaque
service.

Le week-end reste considéré comme non travaillé tant qu'aucun horaire de
référence n'est explicitement saisi pour le samedi/dimanche : ce module
ne force jamais un contrôle un jour qui n'est ni configuré, ni ouvré par
défaut.
"""
from datetime import time
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import JourSemaine
from app.models.horaire_reference import HoraireReference
from app.services import parametre_service

# Jours ouvrés par défaut (lundi-vendredi) : utilisés uniquement en secours,
# quand aucun `HoraireReference` n'est configuré pour le service.
JOURS_OUVRES_PAR_DEFAUT = {
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
}

# Horaire standard de l'entreprise (8h-17h), valeur de secours quand ni
# `HoraireReference`, ni les paramètres système ne sont configurés.
HEURE_DEBUT_TRAVAIL_DEFAUT = time(hour=8, minute=0)
HEURE_FIN_TRAVAIL_DEFAUT = time(hour=17, minute=0)


def horaire_effectif(db: Session, id_service: int, jour: JourSemaine) -> Optional[Tuple[time, time]]:
    """
    Renvoie (heure_debut, heure_fin) à appliquer pour ce service et ce jour :
      1. l'horaire explicitement configuré dans `HoraireReference`, s'il existe ;
      2. sinon, pour un jour ouvré par défaut (lundi-vendredi), l'horaire
         standard 8h-17h (paramètres `heure_debut_travail` / `heure_fin_travail`) ;
      3. sinon (week-end sans horaire configuré) : None — jour non travaillé,
         pas de contrôle de retard/absence.
    """
    stmt = select(HoraireReference).where(
        HoraireReference.id_service == id_service,
        HoraireReference.jour_semaine == jour,
    )
    horaire = db.execute(stmt).scalar_one_or_none()
    if horaire is not None:
        return horaire.heure_debut, horaire.heure_fin

    if jour not in JOURS_OUVRES_PAR_DEFAUT:
        return None

    heure_debut = parametre_service.get_time(db, "heure_debut_travail", default=HEURE_DEBUT_TRAVAIL_DEFAUT)
    heure_fin = parametre_service.get_time(db, "heure_fin_travail", default=HEURE_FIN_TRAVAIL_DEFAUT)
    return heure_debut, heure_fin


def jours_avec_horaire(db: Session, jour: JourSemaine) -> bool:
    """
    Indique si `jour` est un jour à contrôler pour au moins un service :
    vrai si au moins un `HoraireReference` explicite existe pour ce jour,
    OU si `jour` fait partie des jours ouvrés par défaut (auquel cas tous
    les services sans configuration explicite sont couverts par l'horaire
    standard 8h-17h).
    """
    if jour in JOURS_OUVRES_PAR_DEFAUT:
        return True
    stmt = select(HoraireReference.id_horaire).where(HoraireReference.jour_semaine == jour).limit(1)
    return db.execute(stmt).scalar_one_or_none() is not None
