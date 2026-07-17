"""
Middleware ASGI ajoutant les en-têtes de sécurité HTTP standard.

- HSTS : force HTTPS pour 1 an (uniquement si APP_ENV=production, pour ne
  pas piéger un développeur en HTTP local).
- X-Content-Type-Options : nosniff.
- X-Frame-Options : DENY (pas d'embarquement en iframe).
- Referrer-Policy : strict-origin-when-cross-origin.
- Permissions-Policy : désactive par défaut micro/caméra/géoloc pour l'API.
- Content-Security-Policy : restrictive côté API (pas de contenu HTML servi
  par cette application ; Swagger reste accessible car servi par FastAPI
  lui-même, la CSP autorise 'self' + CDN JSDelivr utilisé par Swagger UI).
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        h.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "connect-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'",
        )
        if settings.APP_ENV == "production":
            h.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        return response
