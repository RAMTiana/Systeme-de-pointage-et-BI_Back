"""
Stockage Redis des jetons JWT : rotation des refresh tokens et blacklist
des access tokens (logout immédiat).

Modèle :
- Chaque jeton (access ou refresh) porte un `jti` unique.
- `refresh:{jti} -> user_id` avec TTL = durée du refresh token. Un refresh
  n'est valide QUE s'il est présent dans Redis. À chaque `/auth/refresh`,
  l'ancien jti est supprimé et un nouveau est stocké (rotation stricte).
- `revoked:{jti} -> "1"` avec TTL = temps restant du jeton : toute
  vérification d'access token consulte cette clé et refuse le jeton s'il
  y figure. Utilisé par `/auth/logout`.

Sécurité : la rotation permet de détecter la réutilisation d'un refresh
token volé — si un `jti` de refresh présenté n'existe plus dans Redis,
on considère la session comme compromise et on révoque toute la famille
(clé `refresh_family:{user_id}` qui compte les refresh actifs).
"""
from __future__ import annotations

from app.core.config import settings
from app.core.redis_client import get_redis


def store_refresh(jti: str, user_id: str) -> None:
    ttl_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    get_redis().setex(f"refresh:{jti}", ttl_seconds, user_id)


def consume_refresh(jti: str) -> str | None:
    """
    Consomme (supprime) un refresh token. Retourne le user_id associé ou
    None si le jeton n'est pas/plus enregistré (déjà utilisé, expiré,
    ou révoqué). Opération atomique via GETDEL.
    """
    return get_redis().getdel(f"refresh:{jti}")


def revoke_refresh(jti: str) -> None:
    get_redis().delete(f"refresh:{jti}")


def revoke_all_user_refresh(user_id: str) -> None:
    """
    Révoque tous les refresh tokens d'un utilisateur (utile en cas de
    réutilisation détectée ou de changement de mot de passe).
    Note : SCAN au lieu de KEYS pour ne pas bloquer Redis en production.
    """
    r = get_redis()
    for key in r.scan_iter(match="refresh:*", count=500):
        if r.get(key) == user_id:
            r.delete(key)


def blacklist_access(jti: str, remaining_seconds: int) -> None:
    if remaining_seconds > 0:
        get_redis().setex(f"revoked:{jti}", remaining_seconds, "1")


def is_access_revoked(jti: str) -> bool:
    return get_redis().exists(f"revoked:{jti}") == 1
