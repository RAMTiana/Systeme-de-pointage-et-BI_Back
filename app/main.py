"""
Point d'entrée de l'application FastAPI — SRB Haute Matsiatra.

Lancement en local :
    uvicorn app.main:app --reload
"""
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.scheduler import arreter_scheduler, demarrer_scheduler
from app.core.security_headers import SecurityHeadersMiddleware

logger = logging.getLogger(__name__)

# En production, Swagger/Redoc/openapi.json sont désactivés (surface
# d'attaque inutile + fuite de la structure interne de l'API). C'était déjà
# documenté dans PRODUCTION.md mais jamais réellement appliqué ici.
_docs_actifs = settings.APP_ENV != "production"

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description=(
        "API du système de pointage électronique et d'aide à la décision (BI) "
        "du Service Régional du Budget (SRB) — Haute Matsiatra."
    ),
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json" if _docs_actifs else None,
    docs_url=f"{settings.API_V1_PREFIX}/docs" if _docs_actifs else None,
    redoc_url=f"{settings.API_V1_PREFIX}/redoc" if _docs_actifs else None,
)

# --- En-têtes de sécurité (HSTS, CSP, X-Frame-Options...) ---
# Le middleware existait déjà dans app/core/security_headers.py mais n'était
# jamais enregistré : aucun en-tête n'était donc réellement envoyé.
app.add_middleware(SecurityHeadersMiddleware)

# --- CORS : autoriser le frontend Angular à consommer l'API ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Gestionnaire d'erreurs global ---
# Sans ceci, toute exception non gérée (ex. Redis/Postgres injoignable) est
# renvoyée par le middleware d'erreur par défaut de Starlette AVANT que
# CORSMiddleware ait pu ajouter ses en-têtes à la réponse. Le navigateur
# masque alors la vraie erreur 500 derrière un message "bloqué par CORS",
# ce qui rend le diagnostic très difficile côté frontend.
# En interceptant l'exception ici, la réponse redescend normalement à
# travers CORSMiddleware (qui ajoute bien Access-Control-Allow-Origin),
# et le message reste exploitable sans fuiter de détails internes.
@app.exception_handler(Exception)
async def gestionnaire_erreurs_non_gerees(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
    # Ce gestionnaire s'exécute EN DEHORS de CORSMiddleware (ServerErrorMiddleware
    # est la couche la plus externe) : on ajoute donc les en-têtes CORS à la main,
    # sinon le navigateur masque le vrai 500 derrière une erreur CORS.
    origine = request.headers.get("origin")
    entetes = {}
    if origine and origine in settings.CORS_ORIGINS_LIST:
        entetes = {
            "Access-Control-Allow-Origin": origine,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur. Veuillez réessayer plus tard."},
        headers=entetes,
    )


# --- Routeurs métier (branchés au fur et à mesure des modules) ---
app.include_router(api_router, prefix=settings.API_V1_PREFIX)
# Tous les modules du cahier des charges sont livrés. Après `alembic upgrade
# head`, penser à `python -m scripts.seed_reference_data` (cf. README §15)
# avant toute création de compte utilisateur.


@app.on_event("startup")
def verifier_redis_au_demarrage() -> None:
    """
    Vérifie que Redis est joignable au démarrage.

    Redis est requis pour la révocation des tokens JWT, le verrouillage de
    compte et le rate limiting (cf. app/core/redis_client.py). Sans cette
    vérification, une indisponibilité de Redis ne se manifeste qu'au premier
    appel authentifié, sous la forme d'une erreur 500 qui ressemble à tort à
    un problème de CORS côté frontend (cf. app/main.py, gestionnaire
    d'erreurs global). On préfère donc échouer bruyamment, tout de suite,
    avec un message explicite.
    """
    try:
        get_redis().ping()
    except Exception as exc:  # noqa: BLE001 — on veut juste logguer, pas planter le boot
        logger.warning(
            "Redis injoignable (%s) — REDIS_URL=%s. "
            "L'authentification, le rate limiting et le verrouillage de "
            "compte échoueront tant que Redis n'est pas démarré "
            "(ex. `redis-server` ou `docker compose up -d redis`).",
            exc,
            settings.REDIS_URL,
        )
    else:
        logger.info("Connexion Redis OK (%s).", settings.REDIS_URL)


@app.on_event("startup")
def demarrer_taches_planifiees() -> None:
    """
    Démarre le scheduler in-process qui déclenche automatiquement
    `detecter_absences` chaque jour (cf. app/core/scheduler.py). Sans cela,
    aucune anomalie de type `absence` n'est jamais créée : la route
    `POST /anomalies/detecter-absences` existe, mais rien ne l'appelait
    jusqu'ici (elle attendait un cron externe jamais configuré).
    """
    demarrer_scheduler()


@app.on_event("shutdown")
def arreter_taches_planifiees() -> None:
    arreter_scheduler()


@app.get("/", tags=["Santé"])
def root() -> dict:
    return {"message": "API SRB Haute Matsiatra opérationnelle."}


@app.get(f"{settings.API_V1_PREFIX}/health", tags=["Santé"])
def health_check() -> dict:
    """Endpoint de supervision (utilisable par un load-balancer / monitoring)."""
    return {"status": "ok", "env": settings.APP_ENV}