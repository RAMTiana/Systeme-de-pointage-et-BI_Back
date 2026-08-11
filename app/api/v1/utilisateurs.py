"""
Endpoints de gestion des comptes utilisateurs — réservés aux administrateurs
(permission RBAC `valider_roles`) :

  GET    /utilisateurs                        — liste paginée (recherche, filtres rôle/statut)
  GET    /utilisateurs/roles                   — rôles disponibles (+ permissions)
  GET    /utilisateurs/{id}                    — détail d'un compte
  POST   /utilisateurs                         — créer un compte local
  PATCH  /utilisateurs/{id}                    — modifier login/email/nom complet
  PUT    /utilisateurs/{id}/role                — changer le rôle attribué
  POST   /utilisateurs/{id}/desactiver          — désactiver un compte
  POST   /utilisateurs/{id}/reactiver           — réactiver un compte
  PUT    /utilisateurs/{id}/mot-de-passe        — réinitialiser le mot de passe (compte local)
  DELETE /utilisateurs/{id}                     — suppression physique (cas exceptionnel)

Toutes les opérations d'écriture sont journalisées dans `journal_audit`
(traçabilité des actions d'administration, cf. cahier des charges).

Un administrateur ne peut pas se désactiver, changer son propre rôle ou se
supprimer lui-même : cela évite qu'un compte finisse par s'auto-verrouiller
hors de l'application sans autre administrateur pour le rétablir.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.utilisateur import Utilisateur
from app.schemas.common import Page
from app.schemas.utilisateur import (
    MotDePasseAdminUpdate,
    RoleChangeRequest,
    RoleOut,
    UtilisateurAdminOut,
    UtilisateurCreate,
    UtilisateurUpdate,
)
from app.services import utilisateur_service
from app.services.journal_audit_service import log_action

router = APIRouter(prefix="/utilisateurs", tags=["Utilisateurs"])

_permission_admin = require_permission("valider_roles")


def _interdire_action_sur_soi_meme(utilisateur_courant: Utilisateur, id_cible: int, message: str) -> None:
    if utilisateur_courant.id_utilisateur == id_cible:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.get(
    "",
    response_model=Page[UtilisateurAdminOut],
    summary="Lister les comptes utilisateurs",
)
def lister_utilisateurs(
    recherche: Optional[str] = Query(default=None, description="Filtre sur login, email ou nom complet"),
    id_role: Optional[int] = Query(default=None),
    actif: Optional[bool] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Page[UtilisateurAdminOut]:
    utilisateurs, total = utilisateur_service.list_paginated(
        db,
        recherche=recherche,
        id_role=id_role,
        actif=actif,
        skip=skip,
        limit=limit,
        exclure_administrateur=utilisateur_service.est_chef_service(utilisateur_courant),
    )
    return Page(items=utilisateurs, total=total, skip=skip, limit=limit)


@router.get(
    "/roles",
    response_model=List[RoleOut],
    summary="Lister les rôles disponibles (+ permissions)",
)
def lister_roles(
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> List[RoleOut]:
    return utilisateur_service.list_roles(
        db, exclure_administrateur=utilisateur_service.est_chef_service(utilisateur_courant)
    )


@router.get(
    "/{id_utilisateur}",
    response_model=UtilisateurAdminOut,
    summary="Détail d'un compte",
)
def obtenir_utilisateur(
    id_utilisateur: int,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    return utilisateur


@router.post(
    "",
    response_model=UtilisateurAdminOut,
    status_code=201,
    summary="Créer un compte utilisateur",
)
def creer_utilisateur(
    payload: UtilisateurCreate,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    role_cible = utilisateur_service.get_role_or_404(db, payload.id_role)
    utilisateur_service.verifier_role_attribuable_par_chef_service(utilisateur_courant, role_cible)
    utilisateur = utilisateur_service.create(db, payload)
    log_action(
        db,
        id_utilisateur=utilisateur_courant.id_utilisateur,
        action="creation_compte",
        details=f"Compte créé : {utilisateur.login} (rôle {utilisateur.id_role})",
    )
    return utilisateur


@router.patch(
    "/{id_utilisateur}",
    response_model=UtilisateurAdminOut,
    summary="Modifier un compte (login, email, nom complet)",
)
def modifier_utilisateur(
    id_utilisateur: int,
    payload: UtilisateurUpdate,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    utilisateur = utilisateur_service.update(db, utilisateur, payload)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="modification_compte",
               details=f"Compte modifié : {utilisateur.login}")
    return utilisateur


@router.put(
    "/{id_utilisateur}/role",
    response_model=UtilisateurAdminOut,
    summary="Changer le rôle attribué à un compte",
)
def changer_role(
    id_utilisateur: int,
    payload: RoleChangeRequest,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    _interdire_action_sur_soi_meme(
        utilisateur_courant, id_utilisateur, "Vous ne pouvez pas modifier votre propre rôle."
    )
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    role_cible = utilisateur_service.get_role_or_404(db, payload.id_role)
    utilisateur_service.verifier_role_attribuable_par_chef_service(utilisateur_courant, role_cible)
    utilisateur = utilisateur_service.changer_role(db, utilisateur, payload.id_role)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="changement_role",
               details=f"Nouveau rôle pour {utilisateur.login} : {payload.id_role}")
    return utilisateur


@router.post(
    "/{id_utilisateur}/desactiver",
    response_model=UtilisateurAdminOut,
    summary="Désactiver un compte",
)
def desactiver_utilisateur(
    id_utilisateur: int,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    _interdire_action_sur_soi_meme(
        utilisateur_courant, id_utilisateur, "Vous ne pouvez pas désactiver votre propre compte."
    )
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    utilisateur = utilisateur_service.changer_statut(db, utilisateur, actif=False)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="desactivation_compte",
               details=f"Compte désactivé : {utilisateur.login}")
    return utilisateur


@router.post(
    "/{id_utilisateur}/reactiver",
    response_model=UtilisateurAdminOut,
    summary="Réactiver un compte",
)
def reactiver_utilisateur(
    id_utilisateur: int,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    utilisateur = utilisateur_service.changer_statut(db, utilisateur, actif=True)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="reactivation_compte",
               details=f"Compte réactivé : {utilisateur.login}")
    return utilisateur


@router.put(
    "/{id_utilisateur}/mot-de-passe",
    response_model=UtilisateurAdminOut,
    summary="Réinitialiser le mot de passe d'un compte local",
)
def reinitialiser_mot_de_passe(
    id_utilisateur: int,
    payload: MotDePasseAdminUpdate,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> Utilisateur:
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    utilisateur = utilisateur_service.reinitialiser_mot_de_passe(db, utilisateur, payload.nouveau_mot_de_passe)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="reinitialisation_mot_de_passe",
               details=f"Mot de passe réinitialisé par un administrateur : {utilisateur.login}")
    return utilisateur


@router.delete(
    "/{id_utilisateur}",
    status_code=204,
    summary="Supprimer un compte (suppression physique, cas exceptionnel)",
)
def supprimer_utilisateur(
    id_utilisateur: int,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> None:
    _interdire_action_sur_soi_meme(
        utilisateur_courant, id_utilisateur, "Vous ne pouvez pas supprimer votre propre compte."
    )
    utilisateur = utilisateur_service.get_by_id_or_404(db, id_utilisateur)
    utilisateur_service.verifier_cible_autorisee_pour_chef_service(utilisateur_courant, utilisateur)
    login = utilisateur.login
    utilisateur_service.delete(db, utilisateur)
    log_action(db, id_utilisateur=utilisateur_courant.id_utilisateur, action="suppression_compte",
               details=f"Compte supprimé : {login}")