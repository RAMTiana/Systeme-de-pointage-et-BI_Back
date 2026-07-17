"""
Service de gestion de l'empreinte biométrique faciale d'un agent
(table `empreinte_biometrique`).

Stocke uniquement un **vecteur de caractéristiques** (encodage facial,
ex. 128 dimensions pour la bibliothèque `face-recognition`) déjà calculé
côté client/dispositif de capture — jamais l'image brute du visage,
conformément au cahier des charges (chapitre Sécurité : "stockage
sécurisé et chiffré des données biométriques faciales, accès strictement
limité aux fonctions d'identification").

Le vecteur est sérialisé en JSON puis stocké tel quel dans la colonne
`BYTEA` `encodage_facial` : choix volontairement simple (pas de dépendance
supplémentaire de type numpy/struct), suffisant tant que la comparaison
reste une distance euclidienne sur un vecteur de taille fixe.
"""
import json
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.biometrie import EmpreinteBiometrique


def encoder_vecteur(vecteur: List[float]) -> bytes:
    return json.dumps(vecteur).encode("utf-8")


def decoder_vecteur(donnees: bytes) -> List[float]:
    return json.loads(donnees.decode("utf-8"))


def enregistrer(db: Session, agent: Agent, vecteur: List[float]) -> EmpreinteBiometrique:
    """
    Crée ou remplace l'empreinte faciale de référence de l'agent.
    Nécessite le consentement explicite préalable (cf. cahier des charges,
    Processus 2 BPMN — étape "Informer l'agent et recueillir le consentement").
    """
    if not agent.consentement_facial:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le consentement explicite de l'agent à la reconnaissance faciale "
            "est requis avant d'enregistrer une empreinte (PUT /agents/{id}/consentement-facial).",
        )

    empreinte = agent.empreinte_biometrique
    if empreinte is None:
        empreinte = EmpreinteBiometrique(id_agent=agent.id_agent, encodage_facial=encoder_vecteur(vecteur))
        db.add(empreinte)
    else:
        empreinte.encodage_facial = encoder_vecteur(vecteur)
    db.commit()
    db.refresh(empreinte)
    return empreinte


def supprimer(db: Session, agent: Agent) -> None:
    if agent.empreinte_biometrique is not None:
        db.delete(agent.empreinte_biometrique)
        db.commit()
