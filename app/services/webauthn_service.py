"""
Service d'enrôlement et de vérification WebAuthn/FIDO2 — biométrie de
l'appareil (Touch ID, Windows Hello, empreinte digitale du téléphone),
utilisée comme moyen de pointage biométrique (mode `webauthn`).

Déroulement standard FIDO2, en deux temps distincts :

  Enrôlement (back-office, module Agents — "pour que le pointage fonctionne
  après") :
    1) GET  /agents/{id}/webauthn/options → génère un challenge + les
       paramètres d'inscription (rp, user, algorithmes acceptés...) ;
    2) le navigateur les transmet à `navigator.credentials.create()`
       (via `@simplewebauthn/browser`), qui dialogue avec l'authentificateur
       (biométrie *locale* à l'appareil : jamais transmise au serveur) ;
    3) PUT  /agents/{id}/webauthn → vérifie la réponse signée et enregistre
       l'identifiant de credential + sa clé publique (jamais la biométrie).

  Pointage (poste de pointage, module Pointage) :
    1) GET  /pointage/webauthn/options?matricule=... → challenge d'authentification ;
    2) `navigator.credentials.get()` côté client ;
    3) POST /pointage/webauthn → vérifie l'assertion contre la clé publique
       enregistrée à l'étape d'enrôlement.

Le challenge est conservé côté serveur entre l'étape 1 et l'étape 3 (Redis,
courte durée de vie), car WebAuthn ne peut pas être vérifié sans lui.
"""
import base64
import json
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models.agent import Agent
from app.models.identifiant_webauthn import IdentifiantWebAuthn

_TTL_CHALLENGE_ENROLEMENT_SECONDES = 300
_TTL_CHALLENGE_POINTAGE_SECONDES = 120


def _cle_challenge_enrolement(id_agent: int) -> str:
    return f"webauthn:enrolement:{id_agent}"


def _cle_challenge_pointage(matricule: str) -> str:
    return f"webauthn:pointage:{matricule}"


# --------------------------------------------------------------------
# Enrôlement (back-office, module Agents)
# --------------------------------------------------------------------

def options_enrolement(agent: Agent) -> dict:
    """Génère les options d'inscription à transmettre à navigator.credentials.create()."""
    exclude = None
    if agent.identifiant_webauthn is not None:
        exclude = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(agent.identifiant_webauthn.credential_id))
        ]

    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(agent.id_agent).encode("utf-8"),
        user_name=agent.matricule,
        user_display_name=f"{agent.prenom} {agent.nom}",
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    get_redis().setex(
        _cle_challenge_enrolement(agent.id_agent),
        _TTL_CHALLENGE_ENROLEMENT_SECONDES,
        base64.b64encode(options.challenge).decode("ascii"),
    )
    return json.loads(options_to_json(options))


def enregistrer_credential(
    db: Session, agent: Agent, credential: dict, nom_appareil: Optional[str]
) -> IdentifiantWebAuthn:
    """Vérifie la réponse d'inscription et enregistre (ou remplace) le credential de l'agent."""
    challenge_stocke = get_redis().get(_cle_challenge_enrolement(agent.id_agent))
    if not challenge_stocke:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session d'enrôlement biométrique expirée : redemandez les options et recommencez.",
        )

    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64.b64decode(challenge_stocke),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Échec de la vérification de l'inscription biométrique : {exc}",
        ) from exc
    finally:
        get_redis().delete(_cle_challenge_enrolement(agent.id_agent))

    credential_id_b64 = base64.urlsafe_b64encode(verification.credential_id).decode("ascii").rstrip("=")

    identifiant = agent.identifiant_webauthn
    if identifiant is None:
        identifiant = IdentifiantWebAuthn(id_agent=agent.id_agent)
        db.add(identifiant)

    identifiant.credential_id = credential_id_b64
    identifiant.cle_publique = verification.credential_public_key
    identifiant.compteur_signature = verification.sign_count
    identifiant.nom_appareil = nom_appareil

    db.commit()
    db.refresh(identifiant)
    return identifiant


def supprimer_credential(db: Session, agent: Agent) -> None:
    if agent.identifiant_webauthn is not None:
        db.delete(agent.identifiant_webauthn)
        db.commit()


# --------------------------------------------------------------------
# Pointage (poste de pointage, module Pointage)
# --------------------------------------------------------------------

def options_pointage(agent: Agent) -> dict:
    """Génère les options d'authentification à transmettre à navigator.credentials.get()."""
    if agent.identifiant_webauthn is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Aucun identifiant biométrique enregistré pour cet agent "
            "(à faire depuis la fiche agent, module Agents).",
        )

    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(agent.identifiant_webauthn.credential_id))
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    get_redis().setex(
        _cle_challenge_pointage(agent.matricule),
        _TTL_CHALLENGE_POINTAGE_SECONDES,
        base64.b64encode(options.challenge).decode("ascii"),
    )
    return json.loads(options_to_json(options))


def verifier_assertion(db: Session, agent: Agent, credential: dict) -> None:
    """Vérifie cryptographiquement l'assertion WebAuthn de pointage contre la clé publique enregistrée."""
    if agent.identifiant_webauthn is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Aucun identifiant biométrique enregistré pour cet agent.",
        )

    challenge_stocke = get_redis().get(_cle_challenge_pointage(agent.matricule))
    if not challenge_stocke:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session biométrique expirée : redemandez les options (GET /pointage/webauthn/options) "
            "avant de soumettre le pointage.",
        )

    identifiant = agent.identifiant_webauthn
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64.b64decode(challenge_stocke),
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_ORIGIN,
            credential_public_key=identifiant.cle_publique,
            credential_current_sign_count=identifiant.compteur_signature,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Assertion biométrique invalide : {exc}",
        ) from exc
    finally:
        get_redis().delete(_cle_challenge_pointage(agent.matricule))

    identifiant.compteur_signature = verification.new_sign_count
    db.commit()
