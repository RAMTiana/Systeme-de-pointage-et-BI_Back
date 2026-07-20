"""Routeur — Module IA (analyse intelligente, prévisions commentées,
rapports auto, assistant RH spécialisé)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.utilisateur import Utilisateur
from app.schemas.ia import (
    AnalyseAnomaliesRequest,
    AnalyseAnomaliesResponse,
    PrevisionCommenteeRequest,
    PrevisionCommenteeResponse,
    QuestionRHRequest,
    QuestionRHResponse,
    RapportAutoRequest,
    RapportAutoResponse,
)
from app.services import ia_service, journal_audit_service
from app.services.ia_service import IAIndisponibleError

router = APIRouter(prefix="/ia", tags=["Intelligence Artificielle"])

# Réutilise la permission BI : les fonctionnalités IA sont réservées aux
# profils déjà autorisés à consulter le tableau de bord décisionnel.
_PROTECTION = Depends(require_permission("consulter_bi"))


def _erreur_ia(exc: IAIndisponibleError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.post("/analyser-anomalies", response_model=AnalyseAnomaliesResponse)
def analyser_anomalies(
    payload: AnalyseAnomaliesRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = _PROTECTION,
) -> AnalyseAnomaliesResponse:
    try:
        resultat = ia_service.analyser_anomalies(
            db, id_service=payload.id_service, jours=payload.jours
        )
    except IAIndisponibleError as exc:
        raise _erreur_ia(exc)
    journal_audit_service.log_action(
        db, id_utilisateur=utilisateur.id_utilisateur,
        action="ia_analyse_anomalies",
        details=f"service={payload.id_service} jours={payload.jours}",
    )
    return resultat  # type: ignore[return-value]


@router.post("/prevision-commentee", response_model=PrevisionCommenteeResponse)
def prevision_commentee(
    payload: PrevisionCommenteeRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = _PROTECTION,
) -> PrevisionCommenteeResponse:
    try:
        resultat = ia_service.commenter_previsions(
            db, id_service=payload.id_service, horizon=payload.horizon
        )
    except IAIndisponibleError as exc:
        raise _erreur_ia(exc)
    journal_audit_service.log_action(
        db, id_utilisateur=utilisateur.id_utilisateur,
        action="ia_prevision", details=f"service={payload.id_service} h={payload.horizon}",
    )
    return resultat  # type: ignore[return-value]


@router.post("/rapport-auto", response_model=RapportAutoResponse)
def rapport_auto(
    payload: RapportAutoRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = _PROTECTION,
) -> RapportAutoResponse:
    try:
        resultat = ia_service.generer_rapport(
            db, id_service=payload.id_service, periode=payload.periode
        )
    except IAIndisponibleError as exc:
        raise _erreur_ia(exc)
    journal_audit_service.log_action(
        db, id_utilisateur=utilisateur.id_utilisateur,
        action="ia_rapport_auto", details=f"periode={payload.periode} service={payload.id_service}",
    )
    return resultat  # type: ignore[return-value]


@router.post("/question-rh", response_model=QuestionRHResponse)
def question_rh(
    payload: QuestionRHRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = _PROTECTION,
) -> QuestionRHResponse:
    try:
        resultat = ia_service.repondre_question_rh(
            db, question=payload.question, id_service=payload.id_service
        )
    except IAIndisponibleError as exc:
        raise _erreur_ia(exc)
    journal_audit_service.log_action(
        db, id_utilisateur=utilisateur.id_utilisateur,
        action="ia_question_rh", details=payload.question[:200],
    )
    return resultat  # type: ignore[return-value]
