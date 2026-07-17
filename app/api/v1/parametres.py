"""
Endpoints de gestion des paramètres système (cahier des charges 3.1,
"Personnalisation des seuils et règles métier" — ex. seuil de retard,
seuil de récidive, fenêtre glissante, expiration des codes de vérification) :

  GET /parametres                      — liste, accessible à tout utilisateur connecté
  GET /parametres/{nom_parametre}      — détail d'un paramètre
  PUT /parametres/{nom_parametre}      — modification, réservée à un administrateur
                                          (permission `valider_roles`, comme le module Utilisateurs)

La lecture est ouverte à tout utilisateur actif (ex. : afficher les seuils
en vigueur sur un écran de suivi), tandis que l'écriture est journalisée
dans `journal_audit` au même titre que les autres actions d'administration.
"""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission
from app.models.utilisateur import Utilisateur
from app.schemas.parametre import ParametreOut, ParametreUpdate
from app.services import parametre_service
from app.services.journal_audit_service import log_action

router = APIRouter(prefix="/parametres", tags=["Paramètres système"])

_permission_admin = require_permission("valider_roles")


@router.get("", response_model=List[ParametreOut], summary="Lister les paramètres système")
def lister_parametres(
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> List[ParametreOut]:
    return parametre_service.list_all(db)


@router.get("/{nom_parametre}", response_model=ParametreOut, summary="Détail d'un paramètre")
def obtenir_parametre(
    nom_parametre: str,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> ParametreOut:
    return parametre_service.get_by_nom_or_404(db, nom_parametre)


@router.put(
    "/{nom_parametre}",
    response_model=ParametreOut,
    summary="Modifier la valeur d'un paramètre",
)
def modifier_parametre(
    nom_parametre: str,
    payload: ParametreUpdate,
    db: Session = Depends(get_db),
    utilisateur_courant: Utilisateur = Depends(_permission_admin),
) -> ParametreOut:
    parametre = parametre_service.get_by_nom_or_404(db, nom_parametre)
    ancienne_valeur = parametre.valeur
    parametre = parametre_service.update_valeur(db, parametre, payload.valeur)
    log_action(
        db,
        id_utilisateur=utilisateur_courant.id_utilisateur,
        action="modification_parametre",
        details=f"{nom_parametre} : '{ancienne_valeur}' -> '{payload.valeur}'",
    )
    return parametre