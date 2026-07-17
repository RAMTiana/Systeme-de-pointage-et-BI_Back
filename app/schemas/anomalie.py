"""Schémas Pydantic — Module Anomalies (Processus 3 du BPMN)."""
from datetime import date as date_
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import CanalAlerte, StatutAlerte, StatutJustification, TypeAnomalie
from app.schemas.agent import AgentOut


class JustificatifOut(BaseModel):
    id_justificatif: int
    motif: str
    piece_jointe_chemin: Optional[str] = None
    date_depot: datetime

    model_config = ConfigDict(from_attributes=True)


class AlerteOut(BaseModel):
    id_alerte: int
    canal: CanalAlerte
    statut: StatutAlerte
    destinataire: str
    date_envoi: datetime

    model_config = ConfigDict(from_attributes=True)


class AnomalieOut(BaseModel):
    id_anomalie: int
    id_agent: int
    id_pointage: Optional[int] = None
    type_anomalie: TypeAnomalie
    statut_justification: StatutJustification
    date_detection: datetime
    id_utilisateur_traitant: Optional[int] = None
    agent: Optional[AgentOut] = None

    model_config = ConfigDict(from_attributes=True)


class AnomalieDetailOut(AnomalieOut):
    """Fiche complète : justificatif éventuel + historique des alertes envoyées."""
    justificatif: Optional[JustificatifOut] = None
    alertes: List[AlerteOut] = []


class AnomalieExamenRequest(BaseModel):
    """
    Étapes 8-10 du Processus 3 : décision de la secrétaire après examen du
    dossier de l'agent.
    """
    anomalie_justifiee: bool
    motif: Optional[str] = Field(
        default=None,
        description="Obligatoire si anomalie_justifiee=true (étape 10a : enregistrer le justificatif).",
    )
    piece_jointe_chemin: Optional[str] = Field(
        default=None,
        description="Chemin/référence de la pièce jointe déposée (justificatif scanné, certificat...).",
    )

    @model_validator(mode="after")
    def _motif_requis_si_justifiee(self) -> "AnomalieExamenRequest":
        if self.anomalie_justifiee and not self.motif:
            raise ValueError("Le motif est obligatoire pour justifier une anomalie.")
        return self


class DetectionAbsencesRequest(BaseModel):
    jour: Optional[date_] = Field(
        default=None,
        description="Jour à contrôler (par défaut : hier). Format AAAA-MM-JJ.",
    )


class DetectionAbsencesResultat(BaseModel):
    jour_controle: date_
    absences_detectees: int
    anomalies: List[AnomalieOut]
