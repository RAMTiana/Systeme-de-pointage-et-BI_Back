"""
Politique de mot de passe appliquée à la création et à la réinitialisation.

Règles (alignées ANSSI / NIST 800-63B) :
- longueur minimale configurable (défaut 12) ;
- au moins une minuscule, une majuscule, un chiffre, un caractère spécial ;
- pas d'espaces en début/fin ;
- pas identique au login ou à l'email.

L'appel `validate()` lève une HTTPException 422 avec la liste des motifs
d'échec en français, exploitable directement côté frontend.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, status

from app.core.config import settings

_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"\d")
_SPECIAL = re.compile(r"[^A-Za-z0-9]")


def validate(mot_de_passe: str, *, login: Optional[str] = None, email: Optional[str] = None) -> None:
    erreurs: list[str] = []

    if len(mot_de_passe) < settings.PASSWORD_MIN_LENGTH:
        erreurs.append(f"au moins {settings.PASSWORD_MIN_LENGTH} caractères")
    if len(mot_de_passe.encode("utf-8")) > 72:
        # bcrypt tronque à 72 octets — on l'interdit explicitement pour
        # rester agnostique à l'algorithme de hachage utilisé.
        erreurs.append("au plus 72 octets")
    if not _LOWER.search(mot_de_passe):
        erreurs.append("une lettre minuscule")
    if not _UPPER.search(mot_de_passe):
        erreurs.append("une lettre majuscule")
    if not _DIGIT.search(mot_de_passe):
        erreurs.append("un chiffre")
    if not _SPECIAL.search(mot_de_passe):
        erreurs.append("un caractère spécial")
    if mot_de_passe != mot_de_passe.strip():
        erreurs.append("aucun espace en début ou fin")
    if login and mot_de_passe.lower() == login.lower():
        erreurs.append("différent du login")
    if email and mot_de_passe.lower() == email.lower():
        erreurs.append("différent de l'email")

    if erreurs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Le mot de passe doit contenir : " + ", ".join(erreurs) + ".",
        )
