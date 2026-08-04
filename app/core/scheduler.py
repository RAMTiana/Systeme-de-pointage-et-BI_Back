"""
Scheduler in-process (APScheduler) pour la détection automatique des
absences (Processus 3/4 du BPMN, pattern Timer Start Event).

Avant ce module, `anomalie_service.detecter_absences` n'était atteignable
que via la route `POST /anomalies/detecter-absences` (protégée par
`X-Job-Key`), elle-même censée être appelée par un ordonnanceur externe
(cron système, tâche planifiée du serveur...). En pratique, si personne ne
configure ce cron, le job ne tourne jamais et aucune anomalie de type
`absence` n'est créée — la table `anomalie` ne contient alors que des
`retard` / `depart_anticipe` (créés, eux, en synchrone à chaque pointage).

Ce module fait tourner le même job directement dans le process de l'API,
sans dépendance externe : un `BackgroundScheduler` (thread dédié, adapté à
notre accès DB synchrone/SQLAlchemy) déclenche `detecter_absences` chaque
jour à l'heure configurée (`ABSENCE_JOB_HOUR`/`ABSENCE_JOB_MINUTE`).

La route HTTP `POST /anomalies/detecter-absences` reste disponible en
parallèle (déclenchement manuel, rattrapage, tests, ou pour un déploiement
qui préférerait malgré tout un cron externe — il suffit alors de mettre
`ABSENCE_JOB_ENABLED=false` pour désactiver ce scheduler interne et éviter
un double déclenchement).
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.session import SessionLocal
from app.services import anomalie_service

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

JOB_ID_DETECTION_ABSENCES = "detection_absences_quotidienne"


def _executer_detection_absences() -> None:
    """
    Wrapper appelé par APScheduler : ouvre sa propre session DB (le job ne
    passe pas par la dépendance FastAPI `get_db`, il n'y a pas de requête
    HTTP en cours) et la ferme systématiquement, y compris en cas d'erreur,
    pour ne jamais laisser une connexion ouverte dans le pool.

    `detecter_absences` sans argument `jour` contrôle la veille par défaut
    (cf. anomalie_service.detecter_absences), ce qui est le comportement
    voulu pour une exécution quotidienne tôt le matin.
    """
    db = SessionLocal()
    try:
        anomalies = anomalie_service.detecter_absences(db)
        logger.info(
            "Détection automatique des absences terminée : %d anomalie(s) créée(s).",
            len(anomalies),
        )
    except Exception:  # noqa: BLE001 — on ne veut jamais planter le scheduler
        logger.exception("Échec de la détection automatique des absences.")
    finally:
        db.close()


def demarrer_scheduler() -> None:
    """À appeler au démarrage de l'application (cf. app/main.py)."""
    global _scheduler

    if not settings.ABSENCE_JOB_ENABLED:
        logger.info(
            "Détection automatique des absences désactivée (ABSENCE_JOB_ENABLED=false) — "
            "utiliser POST /anomalies/detecter-absences via un déclencheur externe si besoin."
        )
        return

    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(timezone="Indian/Antananarivo")
    _scheduler.add_job(
        _executer_detection_absences,
        trigger=CronTrigger(hour=settings.ABSENCE_JOB_HOUR, minute=settings.ABSENCE_JOB_MINUTE),
        id=JOB_ID_DETECTION_ABSENCES,
        replace_existing=True,
        misfire_grace_time=3600,  # tolère jusqu'à 1h de retard (redémarrage serveur, charge...)
    )
    _scheduler.start()
    logger.info(
        "Scheduler démarré : détection des absences tous les jours à %02d:%02d (Indian/Antananarivo).",
        settings.ABSENCE_JOB_HOUR,
        settings.ABSENCE_JOB_MINUTE,
    )


def arreter_scheduler() -> None:
    """À appeler à l'arrêt de l'application pour libérer proprement le thread du scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
