"""
Service générique pour les codes à usage unique de la table
`code_verification` (réinitialisation de mot de passe, et à terme
vérification d'email — même table, même mécanique, cf. `type_code_enum`).

Le code envoyé par email n'est jamais stocké en clair : seul son hash
(réutilisation de `hash_password`/`verify_password`, bcrypt) est conservé,
comme demandé par le commentaire du schéma SQL d'origine.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.code_verification import CodeVerification
from app.models.enums import TypeCode
from app.models.utilisateur import Utilisateur
from app.services import parametre_service

_CODE_INVALIDE = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="Code invalide ou expiré.",
)


def _generer_code_numerique(longueur: int = 6) -> str:
    """Génère un code numérique à `longueur` chiffres, tiré aléatoirement (secrets, pas random)."""
    return "".join(secrets.choice("0123456789") for _ in range(longueur))


def _invalider_codes_actifs(db: Session, utilisateur: Utilisateur, type_code: TypeCode) -> None:
    """Un nouveau code annule tous les codes non utilisés du même type pour cet utilisateur :
    un seul code valide à la fois, pour éviter d'empiler des codes actifs."""
    stmt = select(CodeVerification).where(
        CodeVerification.id_utilisateur == utilisateur.id_utilisateur,
        CodeVerification.type_code == type_code,
        CodeVerification.utilise.is_(False),
    )
    for code in db.execute(stmt).scalars().all():
        code.utilise = True
    db.commit()


def generer_code(db: Session, utilisateur: Utilisateur, type_code: TypeCode) -> str:
    """Génère un nouveau code, le persiste (haché) et retourne le code en clair (à envoyer par email)."""
    _invalider_codes_actifs(db, utilisateur, type_code)

    code_clair = _generer_code_numerique()
    expiration_minutes = parametre_service.get_int(db, "code_verification_expiration_minutes", default=15)

    code_verification = CodeVerification(
        id_utilisateur=utilisateur.id_utilisateur,
        type_code=type_code,
        code_hash=hash_password(code_clair),
        date_expiration=datetime.now() + timedelta(minutes=expiration_minutes),
    )
    db.add(code_verification)
    db.commit()
    return code_clair


def valider_code(db: Session, utilisateur: Utilisateur, code_clair: str, type_code: TypeCode) -> CodeVerification:
    """
    Vérifie le code le plus récent, non utilisé, du type demandé pour cet
    utilisateur. Lève une 400 générique dans tous les cas d'échec (code
    inexistant, expiré, déjà utilisé, ou tentatives épuisées) — le message ne
    distingue jamais ces cas, pour ne donner aucune information exploitable
    à un attaquant qui essaierait de deviner le code par force brute.
    """
    tentatives_max = parametre_service.get_int(db, "code_verification_tentatives_max", default=5)

    stmt = (
        select(CodeVerification)
        .where(
            CodeVerification.id_utilisateur == utilisateur.id_utilisateur,
            CodeVerification.type_code == type_code,
            CodeVerification.utilise.is_(False),
        )
        .order_by(CodeVerification.date_creation.desc())
    )
    code_verification: Optional[CodeVerification] = db.execute(stmt).scalars().first()

    if code_verification is None:
        raise _CODE_INVALIDE

    if code_verification.date_expiration < datetime.now():
        code_verification.utilise = True
        db.commit()
        raise _CODE_INVALIDE

    if code_verification.tentatives >= tentatives_max:
        code_verification.utilise = True
        db.commit()
        raise _CODE_INVALIDE

    if not verify_password(code_clair, code_verification.code_hash):
        code_verification.tentatives += 1
        if code_verification.tentatives >= tentatives_max:
            code_verification.utilise = True
        db.commit()
        raise _CODE_INVALIDE

    code_verification.utilise = True
    db.commit()
    db.refresh(code_verification)
    return code_verification
