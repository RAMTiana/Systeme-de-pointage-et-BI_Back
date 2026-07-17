"""
Service de journalisation — alimente `journal_audit` (traçabilité complète
des actions sensibles : connexions, modifications, corrections d'anomalies...
cf. cahier des charges, chapitre "Traçabilité et sauvegarde").
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.journal_audit import JournalAudit


def log_action(
    db: Session,
    id_utilisateur: Optional[int],
    action: str,
    details: Optional[str] = None,
) -> None:
    """
    Enregistre une entrée d'audit.

    Volontairement tolérant aux erreurs : un souci de journalisation ne doit
    jamais faire échouer l'action métier qu'il accompagne (ex. : une connexion
    réussie ne doit pas être bloquée par un problème d'écriture du log).
    """
    try:
        entree = JournalAudit(id_utilisateur=id_utilisateur, action=action, details=details)
        db.add(entree)
        db.commit()
    except Exception:
        db.rollback()
