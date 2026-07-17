"""
Middleware ASGI ajoutant les en-têtes de sécurité HTTP standard.

- HSTS : force HTTPS pour 1 an (uniquement si APP_ENV=production, pour ne
  pas piéger un développeur en HTTP local).
- X-Content-Type-Options : nosniff.
- X-Frame-Options : DENY (pas d'embarquement en iframe).
- Referrer-Policy : strict-origin-when-cross-origin.
- Permissions-Policy : désactive par défaut micro/caméra/géoloc pour l'API.
- Content-Security-Policy : restrictive côté API ; assouplie uniquement sur
  les pages Swagger/ReDoc (cf. _CSP_DOCS), qui ont besoin de script inline
  et du CDN JSDelivr pour s'afficher.

Implémentation en ASGI pur (et non via `BaseHTTPMiddleware`/`dispatch`) :
`BaseHTTPMiddleware` encapsule l'appel suivant dans un `TaskGroup` qui gère
mal la combinaison avec les `@app.exception_handler` — une réponse générée
par un handler d'exception pour une erreur survenue plus bas peut ressortir
en dehors de ce middleware (et donc en dehors du middleware CORS ajouté
après lui), et le navigateur affiche alors à tort une erreur CORS au lieu
du vrai message d'erreur. Une implémentation ASGI pure (wrapper sur `send`)
n'a pas ce problème : les en-têtes sont ajoutés sur n'importe quelle
réponse, y compris celles produites par un exception handler.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

_CSP_API = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "script-src 'self'; "
    "style-src 'self'; "
    "frame-ancestors 'none'"
)

# Swagger UI / ReDoc (servis par FastAPI lui-même sous {API_V1_PREFIX}/docs
# et /redoc) exécutent du JS inline pour s'initialiser et chargent leurs
# assets (JS/CSS/source maps) depuis le CDN JSDelivr : la CSP stricte de
# l'API bloquerait leur exécution (script-src 'self' seul refuse l'inline).
# On applique donc une CSP dédiée, plus permissive, uniquement sur ces
# pages de documentation — jamais sur les endpoints métier JSON, et de
# toute façon désactivées en production (cf. app/main.py, _docs_actifs).
_CSP_DOCS = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self' https://cdn.jsdelivr.net; "
    "frame-ancestors 'none'"
)

_CHEMINS_DOCS = {
    f"{settings.API_V1_PREFIX}/docs",
    f"{settings.API_V1_PREFIX}/redoc",
    f"{settings.API_V1_PREFIX}/openapi.json",
}

_HSTS = "max-age=31536000; includeSubDomains; preload"


class SecurityHeadersMiddleware:
    """Middleware ASGI pur : ajoute les en-têtes de sécurité sur toute réponse HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        csp = _CSP_DOCS if scope.get("path") in _CHEMINS_DOCS else _CSP_API

        async def send_avec_en_tetes(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.setdefault("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append(
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()")
                )
                headers.append((b"content-security-policy", csp.encode()))
                if settings.APP_ENV == "production":
                    headers.append((b"strict-transport-security", _HSTS.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_avec_en_tetes)
