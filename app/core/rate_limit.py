"""
Rate limiter global basé sur slowapi (backend Redis).

- Clé par défaut : IP distante (X-Forwarded-For si derrière un proxy de
  confiance ; ne PAS activer sans reverse proxy correctement configuré).
- Les limites par route sont déclarées via `@limiter.limit("5/minute")`.
- Le backend Redis permet de partager le compteur entre plusieurs workers
  uvicorn/gunicorn.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
    strategy="fixed-window",
    # Désactivé pour éviter l’incompatibilité avec FastAPI/Starlette quand
    # une route retourne un modèle Pydantic au lieu d’un objet Response.
    headers_enabled=False,
)
