"""
Client Redis unique pour l'application.

Utilisé pour :
- verrouillage de comptes après N tentatives de login échouées ;
- rotation & révocation des refresh tokens (JTI blacklist) ;
- rate limiting (via slowapi, avec le même backend).

En développement, REDIS_URL peut pointer vers redis://localhost:6379/0.
En production, REDIS_URL doit être une URL avec authentification et TLS
(rediss://user:pass@host:6380/0).
"""
from __future__ import annotations

import redis

from app.core.config import settings

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Retourne un client Redis synchrone partagé (pool interne)."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
        )
    return _redis
