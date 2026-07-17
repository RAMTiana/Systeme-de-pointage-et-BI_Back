"""Schémas Pydantic — procédure libre-service "Mot de passe oublié"."""
from pydantic import BaseModel, Field


class MotDePasseOublieRequest(BaseModel):
    identifiant: str = Field(min_length=1, description="Login ou email du compte.")


class ReinitialiserMotDePasseRequest(BaseModel):
    identifiant: str = Field(min_length=1, description="Login ou email du compte.")
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$", description="Code reçu par email.")
    nouveau_mot_de_passe: str = Field(min_length=8, max_length=72)


class MessageResponse(BaseModel):
    message: str
