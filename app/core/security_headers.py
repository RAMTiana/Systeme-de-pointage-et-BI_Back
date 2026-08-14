"""
Middleware ASGI ajoutant les en-têtes de sécurité HTTP standard.

- HSTS : force HTTPS pour 1 an (uniquement si APP_ENV=production, pour ne
  pas piéger un développeur en HTTP local).
- X-Content-Type-Options : nosniff.
- X-Frame-Options : DENY (pas d'embarquement en iframe).
- Referrer-Policy : strict-origin-when-cross-origin.
- Permissions-Policy : désactive par défaut micro/caméra/géoloc pour l'API.
- Content-Security-Policy : restrictive côté API (pas de contenu HTML servi
  par cette application). Les pages /docs et /redoc (Swagger UI / ReDoc)
  chargent leurs assets depuis le CDN JSDelivr et s'initialisent via un
  script inline généré par FastAPI : elles reçoivent donc une CSP dédiée,
  légèrement assouplie (script-src 'unsafe-inline' + connect-src pour les
  sourcemaps), sans affaiblir la CSP stricte appliquée au reste de l'API.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import settings

_CSP_API = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "frame-ancestors 'none'"
)

# Swagger UI (page /docs) exécute un script inline pour s'initialiser et
# ReDoc s'appuie sur des web workers ; les deux ont besoin d'un peu plus de
# latitude que le reste de l'API, qui ne sert jamais de HTML/JS applicatif.
_CSP_DOCS = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "connect-src 'self' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'"
)

_CHEMINS_DOCS = ("/docs", "/redoc")


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
        # Allow popups to communicate with their opener via postMessage
        # while keeping COOP protections for other contexts. Only enable in
        # production where strict COOP is valuable; in development the
        # header can interfere with Google Identity postMessage flows.
        if settings.APP_ENV == "production":
            h.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        est_page_docs = request.url.path.endswith(_CHEMINS_DOCS)
        h.setdefault("Content-Security-Policy", _CSP_DOCS if est_page_docs else _CSP_API)
        if settings.APP_ENV == "production":
            h.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        return response