"""
Point d'entrée de l'application FastAPI — SRB Haute Matsiatra.

Lancement en local :
    uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.security_headers import SecurityHeadersMiddleware

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

# --- Routeurs métier (branchés au fur et à mesure des modules) ---
app.include_router(api_router, prefix=settings.API_V1_PREFIX)
# Tous les modules du cahier des charges sont livrés. Après `alembic upgrade
# head`, penser à `python -m scripts.seed_reference_data` (cf. README §15)
# avant toute création de compte utilisateur.


@app.get("/", tags=["Santé"])
def root() -> dict:
    return {"message": "API SRB Haute Matsiatra opérationnelle."}


@app.get(f"{settings.API_V1_PREFIX}/health", tags=["Santé"])
def health_check() -> dict:
    """Endpoint de supervision (utilisable par un load-balancer / monitoring)."""
    return {"status": "ok", "env": settings.APP_ENV}