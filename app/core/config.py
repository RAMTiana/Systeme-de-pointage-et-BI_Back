"""
Configuration centralisée de l'application.

Toutes les variables sont lues depuis l'environnement (fichier .env en local,
variables d'environnement réelles en production). Ne jamais committer le
fichier .env — seul .env.example doit être versionné.

En mode production (APP_ENV=production), le démarrage échoue si une des
valeurs sensibles est laissée à sa valeur par défaut de développement :
c'est un garde-fou contre les déploiements accidentels avec des secrets
publics.
"""
from functools import lru_cache
from typing import List

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_INSECURE_DEFAULTS = {
    "SECRET_KEY": {"change-me", "change-me-with-a-long-random-string"},
    "DEVICE_API_KEY": {"change-me-device-key"},
    "JOB_API_KEY": {"change-me-job-key"},
    "POSTGRES_PASSWORD": {"changeme", "srb_password", "postgres"},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    APP_NAME: str = "SRB Haute Matsiatra - API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # --- Base de données ---
    POSTGRES_USER: str = "srb_user"
    POSTGRES_PASSWORD: str = "changeme"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "srb_haute_matsiatra"
    DATABASE_URL: str | None = None

    # --- Sécurité / JWT ---
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ISSUER: str = "srb-haute-matsiatra"
    JWT_AUDIENCE: str = "srb-frontend"

    # --- Politique mot de passe ---
    PASSWORD_MIN_LENGTH: int = 12

    # --- Verrouillage compte après tentatives échouées ---
    LOCKOUT_MAX_ATTEMPTS: int = 5
    LOCKOUT_WINDOW_SECONDS: int = 900          # fenêtre glissante 15 min
    LOCKOUT_DURATION_SECONDS: int = 900        # blocage 15 min

    # --- Rate limiting (slowapi) ---
    RATE_LIMIT_DEFAULT: str = "120/minute"
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_PASSWORD_RESET: str = "5/minute"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Google Sign-In ---
    GOOGLE_CLIENT_ID: str | None = None

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:4200"

    # --- Poste de pointage & job cron ---
    DEVICE_API_KEY: str = "change-me-device-key"
    JOB_API_KEY: str = "change-me-job-key"

    # --- WebAuthn (biométrie d'appareil : Touch ID / Windows Hello / empreinte) ---
    # RP_ID doit être le nom d'hôte (sans schéma ni port) du front-office ;
    # ORIGIN doit être l'origine complète (schéma + hôte + port) depuis laquelle
    # navigator.credentials.create()/.get() sont appelés.
    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "SRB Haute Matsiatra"
    WEBAUTHN_ORIGIN: str = "http://localhost:4200"

    # --- SMTP (alertes) ---
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    SMTP_FROM_EMAIL: str = "no-reply@srb-hautematsiatra.mg"
    SMTP_FROM_NAME: str = "SRB Haute Matsiatra"

    # --- SMS (webhook) ---
    SMS_WEBHOOK_URL: str | None = None
    SMS_WEBHOOK_API_KEY: str | None = None

    # --- Rapports ---
    REPORTS_DIR: str = "storage/rapports"

    # --- Détection automatique des absences (job planifié) ---
    # Exécuté in-process via APScheduler (cf. app/core/scheduler.py) : plus
    # besoin d'un cron système externe pour appeler
    # POST /anomalies/detecter-absences. ABSENCE_JOB_HOUR/MINUTE sont
    # l'heure locale du serveur à laquelle tourne le job ; par défaut 6h00,
    # pour laisser le temps aux derniers pointages tardifs de la veille
    # d'être enregistrés avant le contrôle.
    ABSENCE_JOB_ENABLED: bool = True
    ABSENCE_JOB_HOUR: int = 6
    ABSENCE_JOB_MINUTE: int = 0

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def CORS_ORIGINS_LIST(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def _refuser_defauts_en_production(self) -> "Settings":
        if self.APP_ENV != "production":
            return self
        problemes: list[str] = []
        for var, insecure in _INSECURE_DEFAULTS.items():
            valeur = getattr(self, var, None)
            if valeur in insecure:
                problemes.append(var)
        if len(self.SECRET_KEY) < 32:
            problemes.append("SECRET_KEY (au moins 32 caractères requis en production)")
        if self.DEBUG:
            problemes.append("DEBUG (doit être false en production)")
        if any(o.startswith("http://") and "localhost" not in o for o in self.CORS_ORIGINS_LIST):
            problemes.append("CORS_ORIGINS (HTTPS obligatoire en production)")
        if problemes:
            raise ValueError(
                "Configuration non sécurisée pour APP_ENV=production : "
                + ", ".join(problemes)
                + ". Corrigez les variables d'environnement avant de démarrer."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
