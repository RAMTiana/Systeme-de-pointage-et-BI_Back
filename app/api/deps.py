"""
Dépendances FastAPI communes :
  - `get_current_user` / `get_current_active_user` : résolvent l'utilisateur
    à partir du jeton JWT envoyé en en-tête `Authorization: Bearer ...`,
    en vérifiant également que le `jti` n'a pas été révoqué (logout).
  - `require_permission(...)` : fabrique de dépendance pour protéger un
    endpoint selon le RBAC (table role_permission).
  - `verify_device_key` / `verify_job_key` : clés partagées pour les
    endpoints machine-à-machine (poste de pointage, cron).
"""
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.core.token_store import is_access_revoked
from app.db.session import get_db  # noqa: F401 — ré-exporté pour les routeurs
from app.models.utilisateur import Utilisateur
from app.services import utilisateur_service

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Impossible de valider les identifiants.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Utilisateur:
    try:
        payload = decode_token(token)
    except JWTError:
        raise _CREDENTIALS_EXCEPTION

    if payload.get("type") != "access":
        raise _CREDENTIALS_EXCEPTION

    jti = payload.get("jti")
    if jti and is_access_revoked(jti):
        raise _CREDENTIALS_EXCEPTION

    id_utilisateur = payload.get("sub")
    if id_utilisateur is None:
        raise _CREDENTIALS_EXCEPTION

    utilisateur = utilisateur_service.get_by_id(db, int(id_utilisateur))
    if utilisateur is None:
        raise _CREDENTIALS_EXCEPTION

    # On expose le payload JWT sur la requête pour permettre au endpoint
    # /auth/logout de blacklister l'access token courant sans redécoder.
    request.state.jwt_payload = payload
    return utilisateur


def get_current_active_user(
    utilisateur: Utilisateur = Depends(get_current_user),
) -> Utilisateur:
    if not utilisateur.actif:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Compte désactivé.")
    return utilisateur


def require_permission(nom_permission: str) -> Callable[..., Utilisateur]:
    def _verifier(utilisateur: Utilisateur = Depends(get_current_active_user)) -> Utilisateur:
        permissions_du_role = {p.nom_permission for p in utilisateur.role.permissions}
        if nom_permission not in permissions_du_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission requise : {nom_permission}",
            )
        return utilisateur

    return _verifier


def verify_device_key(x_device_key: str | None = Header(default=None)) -> None:
    if not x_device_key or x_device_key != settings.DEVICE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé de dispositif de pointage invalide ou absente (en-tête X-Device-Key).",
        )


def verify_job_key(x_job_key: str | None = Header(default=None)) -> None:
    if not x_job_key or x_job_key != settings.JOB_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé de job planifié invalide ou absente (en-tête X-Job-Key).",
        )