"""
Endpoints d'authentification (durcis pour la production).

Verrouillage compte, rate limiting slowapi, rotation refresh, logout avec
révocation des jetons — cf. `app.services.auth_service` et
`app.core.token_store` pour le détail des mécanismes.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.security import decode_token
from app.models.utilisateur import Utilisateur
from app.schemas.mot_de_passe import MessageResponse, MotDePasseOublieRequest, ReinitialiserMotDePasseRequest
from app.schemas.token import RefreshRequest, Token
from app.schemas.utilisateur import GoogleLoginRequest, ProfilUpdate, UtilisateurOut
from app.services import auth_service, mot_de_passe_service, utilisateur_service
from app.services.journal_audit_service import log_action

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/login", response_model=Token, summary="Connexion locale")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Token:
    """
    Formulaire OAuth2 standard (`username` / `password`). `username` accepte
    login ou email. Rate limit + verrouillage temporaire du compte après
    plusieurs tentatives échouées.
    """
    return auth_service.login_local(db, identifiant=form_data.username, mot_de_passe=form_data.password)


@router.post("/google", response_model=Token, summary="Connexion via Google Sign-In")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login_google(
    request: Request, payload: GoogleLoginRequest, db: Session = Depends(get_db)
) -> Token:
    return auth_service.login_google(db, google_token=payload.id_token)


@router.post("/refresh", response_model=Token, summary="Renouvellement (rotation) de l'access token")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def refresh(
    request: Request, payload: RefreshRequest, db: Session = Depends(get_db)
) -> Token:
    try:
        token_payload = decode_token(payload.refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalide.")

    if token_payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalide.")

    sub = token_payload.get("sub")
    jti = token_payload.get("jti")
    if not sub or not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalide.")

    return auth_service.rotate_refresh_token(db, refresh_jti=jti, id_utilisateur=sub)


@router.post("/logout", response_model=MessageResponse, summary="Déconnexion (révoque le jeton courant)")
def logout(
    request: Request,
    payload: RefreshRequest | None = None,
    utilisateur: Utilisateur = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    Blackliste l'access token courant jusqu'à son expiration naturelle et,
    si fourni, révoque également le refresh token associé.
    """
    access_payload = getattr(request.state, "jwt_payload", {}) or {}
    refresh_jti: str | None = None
    if payload and payload.refresh_token:
        try:
            rp = decode_token(payload.refresh_token)
            if rp.get("type") == "refresh" and rp.get("sub") == str(utilisateur.id_utilisateur):
                refresh_jti = rp.get("jti")
        except JWTError:
            # Un refresh déjà expiré/invalide n'empêche pas le logout.
            pass

    auth_service.logout(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        access_jti=access_payload.get("jti"),
        access_exp_epoch=access_payload.get("exp"),
        refresh_jti=refresh_jti,
    )
    return MessageResponse(message="Déconnexion effectuée.")


@router.get("/me", response_model=UtilisateurOut, summary="Profil de l'utilisateur courant")
def me(utilisateur: Utilisateur = Depends(get_current_active_user)) -> Utilisateur:
    return utilisateur


@router.patch(
    "/me",
    response_model=UtilisateurOut,
    summary="Modifier son propre profil (nom affiché, photo)",
)
def modifier_mon_profil(
    payload: ProfilUpdate,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(get_current_active_user),
) -> Utilisateur:
    """
    Auto-édition limitée au nom affiché et à la photo de profil — le login et
    l'email restent réservés à la gestion par un administrateur (cf. PATCH
    /utilisateurs/{id}), pour conserver leur rôle d'identifiant vérifié.
    """
    utilisateur = utilisateur_service.update(db, utilisateur, payload)
    log_action(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        action="modification_profil",
        details="Profil modifié par l'utilisateur lui-même.",
    )
    return utilisateur


_MESSAGE_GENERIQUE_DEMANDE = (
    "Si un compte existe avec cet identifiant, un code de réinitialisation "
    "vient de lui être envoyé par email."
)


@router.post(
    "/mot-de-passe-oublie",
    response_model=MessageResponse,
    summary="Demande de réinitialisation du mot de passe",
)
@limiter.limit(settings.RATE_LIMIT_PASSWORD_RESET)
def mot_de_passe_oublie(
    request: Request, payload: MotDePasseOublieRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    mot_de_passe_service.demander_reinitialisation(db, payload.identifiant)
    return MessageResponse(message=_MESSAGE_GENERIQUE_DEMANDE)


@router.post(
    "/reinitialiser-mot-de-passe",
    response_model=MessageResponse,
    summary="Application du nouveau mot de passe",
)
@limiter.limit(settings.RATE_LIMIT_PASSWORD_RESET)
def reinitialiser_mot_de_passe(
    request: Request,
    payload: ReinitialiserMotDePasseRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    mot_de_passe_service.reinitialiser(
        db,
        identifiant=payload.identifiant,
        code=payload.code,
        nouveau_mot_de_passe=payload.nouveau_mot_de_passe,
    )
    return MessageResponse(message="Mot de passe réinitialisé avec succès. Vous pouvez maintenant vous connecter.")