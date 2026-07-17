"""Agrégation des routeurs de l'API v1."""
from fastapi import APIRouter

from app.api.v1 import agents, anomalies, auth, bi, parametres, pointage, rapports, services, utilisateurs

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(services.router)
api_router.include_router(services.router_legacy)
api_router.include_router(agents.router)
api_router.include_router(utilisateurs.router)
api_router.include_router(pointage.router)
api_router.include_router(anomalies.router)
api_router.include_router(rapports.router)
api_router.include_router(parametres.router)
api_router.include_router(bi.router)

__all__ = ["api_router"]