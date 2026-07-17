"""
Logique métier d'authentification durcie pour la production :
- verrouillage temporaire du compte après N tentatives échouées (Redis) ;
- rotation stricte des refresh tokens (chaque refresh est à usage unique) ;
- détection de réutilisation d'un refresh volé → révocation en cascade ;
- rehachage transparent des mots de passe legacy (bcrypt → argon2id) ;
- journal d'audit sur chaque événement d'authentification.

Principe retenu pour Google : aucune création automatique de compte. Les
comptes sont provisionnés par un administrateur ; Google Sign-In ne fait
qu'authentifier un compte déjà existant.
"""
from typing import Optional

from fastapi import HTTPException, status
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from app.core import lockout
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    needs_rehash,
    hash_password,
    verify_password,
)
from app.core.token_store import (
    blacklist_access,
    consume_refresh,
    revoke_all_user_refresh,
    revoke_refresh,
    store_refresh,
)
from app.models.utilisateur import Utilisateur
from app.schemas.token import Token
from app.services import utilisateur_service
from app.services.journal_audit_service import log_action

_IDENTIFIANTS_INVALIDES = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Identifiants incorrects.",
)
_COMPTE_DESACTIVE = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Compte désactivé. Contactez l'administrateur.",
)
_REFRESH_INVALIDE = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Refresh token invalide, expiré ou déjà utilisé.",
)


def _emettre_tokens(utilisateur: Utilisateur) -> Token:
    subject = str(utilisateur.id_utilisateur)
    access, _access_jti, _access_exp = create_access_token(subject)
    refresh, refresh_jti, _refresh_exp = create_refresh_token(subject)
    store_refresh(refresh_jti, subject)
    return Token(access_token=access, refresh_token=refresh)


def login_local(db: Session, identifiant: str, mot_de_passe: str) -> Token:
    """Authentifie un utilisateur par login/email + mot de passe."""
    # Verrouillage AVANT vérification pour éviter à la fois l'énumération
    # et l'épuisement CPU sur argon2/bcrypt.
    lockout.assert_not_locked(identifiant)

    utilisateur = utilisateur_service.get_by_login_or_email(db, identifiant)

    mot_de_passe_valide = (
        utilisateur is not None
        and utilisateur.mot_de_passe_hash is not None
        and verify_password(mot_de_passe, utilisateur.mot_de_passe_hash)
    )

    if not mot_de_passe_valide:
        lockout.register_failure(identifiant)
        log_action(
            db,
            id_utilisateur=utilisateur.id_utilisateur if utilisateur else None,
            action="connexion_echec",
            details=f"Tentative avec l'identifiant : {identifiant}",
        )
        raise _IDENTIFIANTS_INVALIDES

    if not utilisateur.actif:
        raise _COMPTE_DESACTIVE

    # Rehachage transparent des hashes legacy (bcrypt → argon2id) au moment
    # d'une connexion réussie ; profité aussi si les paramètres argon2 changent.
    if needs_rehash(utilisateur.mot_de_passe_hash):
        try:
            utilisateur_service.mettre_a_jour_hash(db, utilisateur, hash_password(mot_de_passe))
        except Exception:
            # Un échec de rehachage ne doit jamais bloquer la connexion.
            pass

    lockout.reset(identifiant)
    log_action(db, id_utilisateur=utilisateur.id_utilisateur, action="connexion_reussie")
    return _emettre_tokens(utilisateur)


def login_google(db: Session, google_token: str) -> Token:
    """Authentifie un utilisateur via un id_token Google Sign-In."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="La connexion Google n'est pas configurée sur ce serveur.",
        )

    try:
        payload = google_id_token.verify_oauth2_token(
            google_token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton Google invalide ou expiré.",
        )
    except google_exceptions.GoogleAuthError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vérification Google momentanément indisponible. Réessayez.",
        )

    google_sub: str = payload["sub"]
    email: Optional[str] = payload.get("email")

    utilisateur = utilisateur_service.get_by_google_id(db, google_sub)

    if utilisateur is None and email:
        utilisateur_existant = utilisateur_service.get_by_email(db, email)
        if utilisateur_existant is not None:
            utilisateur = utilisateur_service.link_google_account(db, utilisateur_existant, google_sub)

    if utilisateur is None:
        log_action(
            db,
            id_utilisateur=None,
            action="connexion_google_echec",
            details=f"Aucun compte SRB pour l'email Google : {email}",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Aucun compte SRB associé à cette adresse Google. Contactez l'administrateur.",
        )

    if not utilisateur.actif:
        raise _COMPTE_DESACTIVE

    log_action(db, id_utilisateur=utilisateur.id_utilisateur, action="connexion_google_reussie")
    return _emettre_tokens(utilisateur)


def rotate_refresh_token(db: Session, refresh_jti: str, id_utilisateur: str) -> Token:
    """
    Consomme atomiquement l'ancien refresh token (via GETDEL Redis) et
    émet une nouvelle paire. Si le jti n'existe plus, c'est soit une
    expiration, soit une réutilisation → dans le doute, on révoque toute
    la famille de refresh de l'utilisateur (sécurité en profondeur).
    """
    owner = consume_refresh(refresh_jti)
    if owner is None:
        # Réutilisation potentielle d'un refresh volé : on révoque tout.
        revoke_all_user_refresh(id_utilisateur)
        log_action(
            db,
            id_utilisateur=int(id_utilisateur),
            action="refresh_reutilisation_detectee",
            details=f"jti={refresh_jti}",
        )
        raise _REFRESH_INVALIDE

    if owner != id_utilisateur:
        # Incohérence sub/owner : session compromise.
        revoke_all_user_refresh(owner)
        raise _REFRESH_INVALIDE

    utilisateur = utilisateur_service.get_by_id(db, int(id_utilisateur))
    if utilisateur is None or not utilisateur.actif:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou désactivé.",
        )

    log_action(db, id_utilisateur=utilisateur.id_utilisateur, action="refresh_token")
    return _emettre_tokens(utilisateur)


def logout(
    db: Session,
    *,
    id_utilisateur: int,
    access_jti: str | None,
    access_exp_epoch: int | None,
    refresh_jti: str | None,
) -> None:
    """
    Déconnexion : révoque immédiatement l'access token courant (blacklist
    jusqu'à son expiration naturelle) et supprime le refresh token associé.
    """
    if access_jti and access_exp_epoch:
        import time
        remaining = access_exp_epoch - int(time.time())
        blacklist_access(access_jti, remaining)
    if refresh_jti:
        revoke_refresh(refresh_jti)
    log_action(db, id_utilisateur=id_utilisateur, action="deconnexion")
