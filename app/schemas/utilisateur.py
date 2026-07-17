"""Schémas Pydantic pour les utilisateurs, rôles et permissions (RBAC)."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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
    role: RoleLight

    model_config = ConfigDict(from_attributes=True)


class RoleChangeRequest(BaseModel):
    id_role: int


class MotDePasseAdminUpdate(BaseModel):
    """Réinitialisation d'un mot de passe par un administrateur (compte local uniquement)."""
    nouveau_mot_de_passe: str = Field(min_length=8, max_length=72)
