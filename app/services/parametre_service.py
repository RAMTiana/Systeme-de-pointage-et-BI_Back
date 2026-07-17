"""
Lecture typée des paramètres configurables par l'administrateur
(table `parametre_systeme` — cf. cahier des charges, "personnalisation des
seuils et règles métier").

Chaque paramètre est lu avec une valeur par défaut applicative : la ligne
de données de référence (section 5 du script SQL d'origine) n'est pas
nécessairement encore insérée dans toutes les installations, et l'API ne
doit jamais échouer faute d'un paramètre non encore configuré.
"""
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.parametre_systeme import ParametreSysteme


def get_valeur(db: Session, nom_parametre: str) -> Optional[str]:
    stmt = select(ParametreSysteme.valeur).where(ParametreSysteme.nom_parametre == nom_parametre)
    return db.execute(stmt).scalar_one_or_none()


def get_int(db: Session, nom_parametre: str, default: int) -> int:
    valeur = get_valeur(db, nom_parametre)
    try:
        return int(valeur) if valeur is not None else default
    except ValueError:
        return default


def get_float(db: Session, nom_parametre: str, default: float) -> float:
    valeur = get_valeur(db, nom_parametre)
    try:
        return float(valeur) if valeur is not None else default
    except ValueError:
        return default


# --------------------------------------------------------------------
# Accès CRUD — module Paramètres système (GET/PUT /parametres, réservé
# en écriture à l'administrateur).
# --------------------------------------------------------------------

def list_all(db: Session) -> List[ParametreSysteme]:
    stmt = select(ParametreSysteme).order_by(ParametreSysteme.nom_parametre)
    return list(db.execute(stmt).scalars().all())


def get_by_nom_or_404(db: Session, nom_parametre: str) -> ParametreSysteme:
    stmt = select(ParametreSysteme).where(ParametreSysteme.nom_parametre == nom_parametre)
    parametre = db.execute(stmt).scalar_one_or_none()
    if parametre is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paramètre introuvable.")
    return parametre


def update_valeur(db: Session, parametre: ParametreSysteme, valeur: str) -> ParametreSysteme:
    parametre.valeur = valeur
    db.commit()
    db.refresh(parametre)
    return parametre
