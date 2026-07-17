"""
Verrouillage temporaire d'un compte après N tentatives de connexion échouées.

- Compteur Redis `login:fail:{identifiant_normalisé}` avec TTL sliding.
- Au-delà de LOCKOUT_MAX_ATTEMPTS, une clé `login:locked:{identifiant}` est
  posée pour LOCKOUT_DURATION_SECONDS.
- Le contrôle est fait AVANT la vérification du mot de passe pour éviter
  qu'un attaquant puisse à la fois épuiser les tentatives et consommer
  des cycles bcrypt/argon2.
- Une connexion réussie remet le compteur à zéro.

L'identifiant est normalisé (lower + strip) pour empêcher un contournement
trivial en changeant la casse.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.redis_client import get_redis


def _key_fail(identifiant: str) -> str:
    return f"login:fail:{identifiant.strip().lower()}"


def _key_lock(identifiant: str) -> str:
    return f"login:locked:{identifiant.strip().lower()}"


def assert_not_locked(identifiant: str) -> None:
    ttl = get_redis().ttl(_key_lock(identifiant))
    if ttl and ttl > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Trop de tentatives échouées. Ce compte est temporairement "
                f"verrouillé. Réessayez dans {ttl} secondes."
            ),
            headers={"Retry-After": str(ttl)},
        )


def register_failure(identifiant: str) -> None:
    r = get_redis()
    key = _key_fail(identifiant)
    attempts = r.incr(key)
    if attempts == 1:
        r.expire(key, settings.LOCKOUT_WINDOW_SECONDS)
    if attempts >= settings.LOCKOUT_MAX_ATTEMPTS:
        r.setex(_key_lock(identifiant), settings.LOCKOUT_DURATION_SECONDS, "1")
        r.delete(key)


def reset(identifiant: str) -> None:
    r = get_redis()
    r.delete(_key_fail(identifiant), _key_lock(identifiant))
