"""Schémas Pydantic pour les utilisateurs, rôles et permissions (RBAC)."""
import base64
import binascii
import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# --------------------------------------------------------------------
# Photo de profil (avatar) — envoyée par le frontend en data URL base64
# (redimensionnée/compressée côté client). Validation commune réutilisée
# par `UtilisateurUpdate` (édition par un administrateur) et `ProfilUpdate`
# (auto-édition via PATCH /auth/me).
# --------------------------------------------------------------------

_FORMATS_PHOTO_AUTORISES = {"image/jpeg", "image/png", "image/webp"}
_TAILLE_MAX_PHOTO_OCTETS = 2 * 1024 * 1024  # 2 Mo une fois décodée
_MOTIF_DATA_URL_PHOTO = re.compile(r"^data:(?P<mime>image/[\w.+-]+);base64,(?P<donnees>.+)$", re.DOTALL)


def _valider_photo_profil(valeur: Optional[str]) -> Optional[str]:
    """Chaîne vide -> suppression de la photo existante (autorisée telle quelle).
    Sinon : data URL base64 obligatoire, format JPEG/PNG/WebP, 2 Mo max décodés."""
    if valeur is None or valeur == "":
        return valeur
    correspondance = _MOTIF_DATA_URL_PHOTO.match(valeur.strip())
    if not correspondance:
        raise ValueError(
            "Photo invalide : attendu une data URL base64, ex. 'data:image/jpeg;base64,...'."
        )
    mime = correspondance.group("mime")
    if mime not in _FORMATS_PHOTO_AUTORISES:
        raise ValueError(f"Format d'image non autorisé ({mime}) : utilisez JPEG, PNG ou WebP.")
    try:
        donnees_brutes = base64.b64decode(correspondance.group("donnees"), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Photo invalide : encodage base64 illisible.")
    if len(donnees_brutes) > _TAILLE_MAX_PHOTO_OCTETS:
        raise ValueError("Photo trop volumineuse : 2 Mo maximum une fois décodée.")
    return valeur


class PermissionOut(BaseModel):
    id_permission: int
    nom_permission: str
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RoleOut(BaseModel):
    id_role: int
    nom_role: str
    permissions: List[PermissionOut] = []

    model_config = ConfigDict(from_attributes=True)


class UtilisateurOut(BaseModel):
    """Profil courant renvoyé par GET /auth/me — jamais de champ sensible (hash, google_id)."""
    id_utilisateur: int
    login: str
    email: EmailStr
    nom_complet: str
    actif: bool
    email_verifie: bool
    auth_provider: str
    photo_profil: Optional[str] = None
    role: RoleOut

    model_config = ConfigDict(from_attributes=True)


class GoogleLoginRequest(BaseModel):
    """Corps de requête pour POST /auth/google — id_token émis par Google Sign-In côté Angular."""
    id_token: str


class RoleLight(BaseModel):
    """Version allégée du rôle, utilisée en imbrication dans la liste des utilisateurs."""
    id_role: int
    nom_role: str

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------
# Gestion des comptes (module Utilisateurs — réservé aux administrateurs)
# --------------------------------------------------------------------

class UtilisateurCreate(BaseModel):
    """
    Création d'un compte par un administrateur. Toujours en `auth_provider='local'` :
    un compte Google se lie automatiquement à un compte local existant lors de sa
    première connexion (cf. auth_service.login_google), il ne se crée jamais seul.
    """
    login: str = Field(min_length=1, max_length=80)
    email: EmailStr
    nom_complet: str = Field(min_length=1, max_length=150)
    mot_de_passe: str = Field(min_length=8, max_length=72)
    id_role: int


class UtilisateurUpdate(BaseModel):
    """PATCH sémantique : seuls les champs fournis sont modifiés. Le rôle et le mot de
    passe se changent via leurs endpoints dédiés (traçabilité distincte dans l'audit)."""
    login: Optional[str] = Field(default=None, min_length=1, max_length=80)
    email: Optional[EmailStr] = None
    nom_complet: Optional[str] = Field(default=None, min_length=1, max_length=150)
    photo_profil: Optional[str] = Field(
        default=None,
        description="Data URL base64 (JPEG/PNG/WebP, 2 Mo max décodé). Chaîne vide pour supprimer la photo actuelle.",
    )

    @field_validator("photo_profil")
    @classmethod
    def _validation_photo(cls, v: Optional[str]) -> Optional[str]:
        return _valider_photo_profil(v)


class ProfilUpdate(BaseModel):
    """Auto-modification du profil par l'utilisateur connecté (PATCH /auth/me) :
    volontairement limitée au nom affiché et à la photo — le login et l'email
    restent modifiables uniquement par un administrateur (cf. UtilisateurUpdate)."""
    nom_complet: Optional[str] = Field(default=None, min_length=1, max_length=150)
    photo_profil: Optional[str] = Field(
        default=None,
        description="Data URL base64 (JPEG/PNG/WebP, 2 Mo max décodé). Chaîne vide pour supprimer la photo actuelle.",
    )

    @field_validator("photo_profil")
    @classmethod
    def _validation_photo(cls, v: Optional[str]) -> Optional[str]:
        return _valider_photo_profil(v)


class UtilisateurAdminOut(BaseModel):
    """Fiche compte telle que vue par un administrateur — toujours sans champ sensible."""
    id_utilisateur: int
    login: str
    email: EmailStr
    nom_complet: str
    actif: bool
    email_verifie: bool
    auth_provider: str
    date_creation: datetime
    photo_profil: Optional[str] = None
    role: RoleLight

    model_config = ConfigDict(from_attributes=True)


class RoleChangeRequest(BaseModel):
    id_role: int


class MotDePasseAdminUpdate(BaseModel):
    """Réinitialisation d'un mot de passe par un administrateur (compte local uniquement)."""
    nouveau_mot_de_passe: str = Field(min_length=8, max_length=72)
