"""
Enums Python miroir des types PostgreSQL définis dans le schéma SQL
(section 1 — TYPES ÉNUMÉRÉS). Les noms des types PG (name=...) sont
conservés à l'identique pour qu'Alembic génère des CREATE TYPE cohérents
avec le schéma d'origine.
"""
import enum

from sqlalchemy import Enum as SAEnum


def pg_enum(enum_cls: type, name: str) -> SAEnum:
    """
    Construit un type SQLAlchemy Enum qui :
      - porte le même nom PostgreSQL que le schéma SQL d'origine (`name`)
      - stocke la *valeur* de l'enum Python (ex. "actif"), pas son nom
        Python (ex. "ACTIF") — via values_callable.
    """
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda obj: [member.value for member in obj],
    )


class StatutAgent(str, enum.Enum):
    ACTIF = "actif"
    DESACTIVE = "desactive"


class TypePointage(str, enum.Enum):
    ENTREE = "entree"
    SORTIE = "sortie"


class ModePointage(str, enum.Enum):
    QR = "qr"
    BADGE = "badge"
    FACIAL = "facial"
    WEBAUTHN = "webauthn"


class StatutPointage(str, enum.Enum):
    VALIDE = "valide"
    REJETE = "rejete"
    DOUBLON = "doublon"


class TypeAnomalie(str, enum.Enum):
    RETARD = "retard"
    ABSENCE = "absence"
    DEPART_ANTICIPE = "depart_anticipe"


class StatutJustification(str, enum.Enum):
    EN_ATTENTE = "en_attente"
    JUSTIFIEE = "justifiee"
    NON_JUSTIFIEE = "non_justifiee"


class CanalAlerte(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


class StatutAlerte(str, enum.Enum):
    ENVOYEE = "envoyee"
    ECHEC = "echec"


class TypePeriode(str, enum.Enum):
    JOUR = "jour"
    SEMAINE = "semaine"
    MOIS = "mois"
    ANNEE = "annee"


class FormatRapport(str, enum.Enum):
    PDF = "pdf"
    EXCEL = "excel"


class JourSemaine(str, enum.Enum):
    LUNDI = "lundi"
    MARDI = "mardi"
    MERCREDI = "mercredi"
    JEUDI = "jeudi"
    VENDREDI = "vendredi"
    SAMEDI = "samedi"
    DIMANCHE = "dimanche"


class AuthProvider(str, enum.Enum):
    LOCAL = "local"
    GOOGLE = "google"


class TypeCode(str, enum.Enum):
    RESET_PASSWORD = "reset_password"
    VERIFICATION_EMAIL = "verification_email"
