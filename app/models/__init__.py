"""
Point d'entrée unique des modèles ORM.

Ce fichier importe explicitement chaque modèle afin que
`Base.metadata` soit complète au moment où Alembic (ou tout
autre outil) inspecte les tables déclarées. Sans cet import
centralisé, un modèle "oublié" n'apparaîtrait jamais dans les
migrations autogénérées.
"""
from app.db.base_class import Base  # noqa: F401

from app.models.service import Service  # noqa: F401
from app.models.agent import Agent  # noqa: F401
from app.models.affectation import Affectation  # noqa: F401
from app.models.biometrie import EmpreinteBiometrique  # noqa: F401
from app.models.identifiant_webauthn import IdentifiantWebAuthn  # noqa: F401
from app.models.rbac import Role, Permission, role_permission  # noqa: F401
from app.models.utilisateur import Utilisateur  # noqa: F401
from app.models.code_verification import CodeVerification  # noqa: F401
from app.models.pointage import Pointage  # noqa: F401
from app.models.anomalie import Anomalie  # noqa: F401
from app.models.justificatif import Justificatif  # noqa: F401
from app.models.alerte import Alerte  # noqa: F401
from app.models.rapport import Rapport  # noqa: F401
from app.models.journal_audit import JournalAudit  # noqa: F401
from app.models.horaire_reference import HoraireReference  # noqa: F401
from app.models.parametre_systeme import ParametreSysteme  # noqa: F401

__all__ = [
    "Base",
    "Service",
    "Agent",
    "Affectation",
    "EmpreinteBiometrique",
    "IdentifiantWebAuthn",
    "Role",
    "Permission",
    "role_permission",
    "Utilisateur",
    "CodeVerification",
    "Pointage",
    "Anomalie",
    "Justificatif",
    "Alerte",
    "Rapport",
    "JournalAudit",
    "HoraireReference",
    "ParametreSysteme",
]
