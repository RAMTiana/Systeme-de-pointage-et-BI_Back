"""
Primitives de sécurité : hachage des mots de passe (argon2id, avec
compatibilité rétro-active bcrypt pour les hashes existants) et émission /
validation des jetons JWT (python-jose) avec identifiant unique (jti)
permettant la révocation et la rotation via Redis.

Ce module ne connaît ni la base de données ni FastAPI — il est
volontairement indépendant pour rester facilement testable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import jwt

from app.core.config import settings

# --- Mots de passe --------------------------------------------------------
#
# Nouveau hash : argon2id (paramètres OWASP 2024 : t=3, m=64 Mio, p=4).
# Hashes existants (bcrypt) restent vérifiables ; ils seront rehashés en
# argon2id à la prochaine connexion réussie de l'utilisateur (cf.
# `needs_rehash`).

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 Mio
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_BCRYPT_MAX_BYTES = 72


def hash_password(mot_de_passe: str) -> str:
    """Retourne un hash argon2id du mot de passe en clair."""
    if len(mot_de_passe.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        # Limite conservatrice pour rester compatible avec un éventuel
        # basculement bcrypt et éviter les mots de passe démesurés.
        raise ValueError("Le mot de passe ne doit pas dépasser 72 octets.")
    return _argon2.hash(mot_de_passe)


def verify_password(mot_de_passe: str, mot_de_passe_hash: str) -> bool:
    """
    Compare un mot de passe en clair au hash stocké. Détecte automatiquement
    argon2 vs bcrypt (hashes existants) pour permettre une migration
    progressive sans invalider les comptes.
    """
    if not mot_de_passe_hash:
        return False
    if mot_de_passe_hash.startswith("$argon2"):
        try:
            return _argon2.verify(mot_de_passe_hash, mot_de_passe)
        except (VerifyMismatchError, InvalidHashError, Exception):
            return False
    # Legacy bcrypt
    try:
        return bcrypt.checkpw(mot_de_passe.encode("utf-8"), mot_de_passe_hash.encode("utf-8"))
    except ValueError:
        return False


def needs_rehash(mot_de_passe_hash: str) -> bool:
    """Vrai si le hash doit être régénéré (algo legacy ou paramètres obsolètes)."""
    if not mot_de_passe_hash or not mot_de_passe_hash.startswith("$argon2"):
        return True
    try:
        return _argon2.check_needs_rehash(mot_de_passe_hash)
    except InvalidHashError:
        return True


# --- JWT ------------------------------------------------------------------

def _creer_token(
    subject: str, expires_delta: timedelta, token_type: Literal["access", "refresh"]
) -> tuple[str, str, int]:
    """Retourne (token, jti, exp_epoch)."""
    maintenant = datetime.now(timezone.utc)
    exp = maintenant + expires_delta
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": maintenant,
        "exp": exp,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, jti, int(exp.timestamp())


def create_access_token(subject: str) -> tuple[str, str, int]:
    return _creer_token(
        subject,
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "access",
    )


def create_refresh_token(subject: str) -> tuple[str, str, int]:
    return _creer_token(
        subject,
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    """
    Décode et valide un jeton JWT (signature + expiration + iss + aud).
    Lève `jose.JWTError` si le jeton est invalide, malformé ou expiré.
    """
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )
