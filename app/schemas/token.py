"""Schémas Pydantic liés aux jetons JWT."""
from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    """Réponse renvoyée par /auth/login, /auth/google et /auth/refresh."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """Contenu décodé d'un jeton JWT (usage interne)."""
    sub: Optional[str] = None
    type: Optional[str] = None  # "access" | "refresh"
    exp: Optional[int] = None


class RefreshRequest(BaseModel):
    """Corps de requête pour POST /auth/refresh."""
    refresh_token: str
