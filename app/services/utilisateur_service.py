"""
Accès aux données pour l'entité Utilisateur.

Chaque fonction de lecture précharge le rôle et ses permissions
(`joinedload`) : c'est ce qui permet à `require_permission` (voir
app/api/deps.py) de vérifier les droits sans requête SQL supplémentaire.

Cf. cahier des charges — module "Gestion des utilisateurs" : création de
compte par un administrateur, attribution/changement de rôle, activation/
désactivation, réinitialisation de mot de passe. Protégé par la permission
RBAC `valider_roles`.
"""
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.security import hash_password
from app.models.rbac import Role
from app.models.utilisateur import Utilisateur
from app.schemas.utilisateur import UtilisateurCreate, UtilisateurUpdate

_IDENTIFIANT_DEJA_UTILISE = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="Un compte avec ce login ou cet email existe déjà.",
)


def _requete_avec_role() -> Select:
    return select(Utilisateur).options(joinedload(Utilisateur.role).joinedload(Role.permissions))


def get_by_id(db: Session, id_utilisateur: int) -> Optional[Utilisateur]:
    stmt = _requete_avec_role().where(Utilisateur.id_utilisateur == id_utilisateur)
    return db.execute(stmt).unique().scalar_one_or_none()


def get_by_id_or_404(db: Session, id_utilisateur: int) -> Utilisateur:
    utilisateur = get_by_id(db, id_utilisateur)
    if utilisateur is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    return utilisateur


def get_by_login_or_email(db: Session, identifiant: str) -> Optional[Utilisateur]:
    """Le champ `username` du login OAuth2 accepte indifféremment le login ou l'email."""
    stmt = _requete_avec_role().where(
        or_(Utilisateur.login == identifiant, Utilisateur.email == identifiant)
    )
    return db.execute(stmt).unique().scalar_one_or_none()


def get_by_email(db: Session, email: str) -> Optional[Utilisateur]:
    stmt = _requete_avec_role().where(Utilisateur.email == email)
    return db.execute(stmt).unique().scalar_one_or_none()


def get_by_google_id(db: Session, google_id: str) -> Optional[Utilisateur]:
    stmt = _requete_avec_role().where(Utilisateur.google_id == google_id)
    return db.execute(stmt).unique().scalar_one_or_none()


def link_google_account(db: Session, utilisateur: Utilisateur, google_id: str) -> Utilisateur:
    """
    Associe un `google_id` à un compte existant (créé initialement en local
    par un administrateur). L'email étant confirmé par Google, on marque
    aussi le compte comme vérifié.
    """
    utilisateur.google_id = google_id
    utilisateur.email_verifie = True
    db.commit()
    db.refresh(utilisateur)
    return utilisateur


# --------------------------------------------------------------------
# Gestion des comptes (administration — permission `valider_roles`)
# --------------------------------------------------------------------

def get_role_or_404(db: Session, id_role: int) -> Role:
    role = db.get(Role, id_role)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rôle introuvable.")
    return role


def list_roles(db: Session) -> List[Role]:
    """Rôles disponibles (+ permissions), pour peupler un menu déroulant côté Angular."""
    stmt = select(Role).options(joinedload(Role.permissions)).order_by(Role.nom_role)
    return list(db.execute(stmt).unique().scalars().all())


def list_paginated(
    db: Session,
    recherche: Optional[str] = None,
    id_role: Optional[int] = None,
    actif: Optional[bool] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Utilisateur], int]:
    """
    Recherche paginée selon login, email ou nom complet (recherche partielle,
    insensible à la casse) et filtrage par rôle / statut d'activation.
    """
    stmt = _requete_avec_role()
    conditions = []

    if recherche:
        motif = f"%{recherche}%"
        conditions.append(
            or_(
                Utilisateur.login.ilike(motif),
                Utilisateur.email.ilike(motif),
                Utilisateur.nom_complet.ilike(motif),
            )
        )
    if id_role is not None:
        conditions.append(Utilisateur.id_role == id_role)
    if actif is not None:
        conditions.append(Utilisateur.actif == actif)

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(
        stmt.with_only_columns(Utilisateur.id_utilisateur).subquery()
    )
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Utilisateur.nom_complet).offset(skip).limit(limit)
    utilisateurs = list(db.execute(stmt).unique().scalars().all())

    return utilisateurs, total


def create(db: Session, payload: UtilisateurCreate) -> Utilisateur:
    """Crée un compte local. Le rôle doit exister (404 sinon) ; login/email uniques (409 sinon)."""
    get_role_or_404(db, payload.id_role)

    utilisateur = Utilisateur(
        login=payload.login,
        email=payload.email,
        nom_complet=payload.nom_complet,
        mot_de_passe_hash=hash_password(payload.mot_de_passe),
        id_role=payload.id_role,
    )
    db.add(utilisateur)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _IDENTIFIANT_DEJA_UTILISE
    db.refresh(utilisateur)
    return get_by_id_or_404(db, utilisateur.id_utilisateur)


def update(db: Session, utilisateur: Utilisateur, payload: UtilisateurUpdate) -> Utilisateur:
    donnees = payload.model_dump(exclude_unset=True)
    for champ, valeur in donnees.items():
        setattr(utilisateur, champ, valeur)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _IDENTIFIANT_DEJA_UTILISE
    db.refresh(utilisateur)
    return get_by_id_or_404(db, utilisateur.id_utilisateur)


def changer_role(db: Session, utilisateur: Utilisateur, id_role: int) -> Utilisateur:
    get_role_or_404(db, id_role)
    utilisateur.id_role = id_role
    db.commit()
    db.refresh(utilisateur)
    return get_by_id_or_404(db, utilisateur.id_utilisateur)


def changer_statut(db: Session, utilisateur: Utilisateur, actif: bool) -> Utilisateur:
    """Active ou désactive un compte (jamais de suppression physique en cas normal)."""
    utilisateur.actif = actif
    db.commit()
    db.refresh(utilisateur)
    return utilisateur


def reinitialiser_mot_de_passe(db: Session, utilisateur: Utilisateur, nouveau_mot_de_passe: str) -> Utilisateur:
    """
    Réinitialisation d'un mot de passe par un administrateur — réservée aux comptes
    `auth_provider='local'` (un compte Google n'a pas de mot de passe SRB, cf. contrainte
    `chk_auth_provider` du schéma). La procédure libre-service (email + code à usage
    unique via `code_verification`) fait l'objet d'un module dédié à venir.
    """
    if utilisateur.auth_provider != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce compte s'authentifie via Google ; aucun mot de passe SRB à réinitialiser.",
        )
    utilisateur.mot_de_passe_hash = hash_password(nouveau_mot_de_passe)
    db.commit()
    db.refresh(utilisateur)
    return utilisateur


def mettre_a_jour_hash(db: Session, utilisateur: Utilisateur, nouveau_hash: str) -> None:
    """
    Met à jour uniquement le champ `mot_de_passe_hash`. Utilisé pour le
    rehachage transparent (bcrypt → argon2id) au moment d'une connexion
    réussie — pas de validation métier, pas de commit d'autres champs.
    """
    utilisateur.mot_de_passe_hash = nouveau_hash
    db.commit()





def delete(db: Session, utilisateur: Utilisateur) -> None:
    """
    Suppression physique d'un compte — supprime en cascade ses codes de vérification
    (ON DELETE CASCADE, cf. schéma SQL) ; les rapports générés et anomalies traitées
    par ce compte sont conservés (id_utilisateur passe à NULL). À réserver aux erreurs
    de saisie : dans le cas général, préférer `changer_statut(..., actif=False)` pour
    conserver la traçabilité (journal_audit référence toujours le compte historique).
    """
    db.delete(utilisateur)
    db.commit()
