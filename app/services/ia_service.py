"""
Service IA — SRB Haute Matsiatra.

Fournit 4 fonctionnalités d'IA appliquée au système de pointage :
  1. Analyse intelligente des anomalies récentes (détection de patterns,
     recommandations RH ciblées).
  2. Prévisions commentées à partir des tendances BI.
  3. Génération automatique de rapports RH en langage naturel.
  4. Assistant RH conversationnel spécialisé (Q&A sur les données de
     pointage — pas un chatbot généraliste).

Le client HTTP cible n'importe quel fournisseur compatible OpenAI :
OpenAI, Lovable AI Gateway, Groq, Mistral, Together, Ollama, etc.
Configuration via `settings.IA_BASE_URL`, `IA_API_KEY`, `IA_MODEL`.
"""
from __future__ import annotations

import json
import logging
from datetime import date as date_
from datetime import timedelta
from typing import Any, Optional

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import anomalie_service, bi_service

logger = logging.getLogger(__name__)


class IAIndisponibleError(RuntimeError):
    """L'IA n'est pas configurée ou le fournisseur est injoignable."""


# --------------------------------------------------------------------------
# Client LLM (OpenAI-compatible)
# --------------------------------------------------------------------------
def _appeler_llm(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1200,
    response_json: bool = False,
) -> str:
    if not settings.IA_API_KEY:
        raise IAIndisponibleError(
            "IA_API_KEY absent : configurez la clé du fournisseur IA "
            "(OpenAI / Lovable AI Gateway / Groq...) dans le fichier .env."
        )
    url = f"{settings.IA_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.IA_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        reponse = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.IA_API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=settings.IA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.exception("Appel IA échoué : %s", exc)
        raise IAIndisponibleError("Le service IA est injoignable.") from exc

    if reponse.status_code == 402:
        raise IAIndisponibleError("Crédits IA épuisés — rechargez le fournisseur.")
    if reponse.status_code == 429:
        raise IAIndisponibleError("Trop de requêtes vers l'IA — réessayez plus tard.")
    if reponse.status_code >= 400:
        logger.error("IA erreur %s : %s", reponse.status_code, reponse.text[:400])
        raise IAIndisponibleError(
            f"Erreur du fournisseur IA (HTTP {reponse.status_code})."
        )

    data = reponse.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise IAIndisponibleError("Réponse IA malformée.") from exc


_SYSTEM_BASE = (
    "Tu es l'assistant IA du Service Régional du Budget (SRB) — Haute Matsiatra, "
    "spécialisé UNIQUEMENT dans l'analyse des données de pointage et de présence "
    "des agents publics. Tu réponds en français, de manière factuelle, concise "
    "et professionnelle. Tu ne réponds pas aux questions hors du périmètre RH / "
    "pointage. Tu ne divulgues jamais d'informations personnelles au-delà des "
    "données déjà fournies dans le contexte. Tu cites toujours les chiffres "
    "précis quand ils sont disponibles."
)


# --------------------------------------------------------------------------
# 1) Analyse intelligente des anomalies
# --------------------------------------------------------------------------
def analyser_anomalies(
    db: Session,
    *,
    id_service: Optional[int] = None,
    jours: int = 30,
) -> dict[str, Any]:
    date_fin = date_.today()
    date_debut = date_fin - timedelta(days=jours)

    anomalies, total = anomalie_service.lister_anomalies(
        db,
        id_service=id_service,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=0,
        limit=200,
    )

    # Résumé compact envoyé à l'IA (jamais la BDD brute).
    resume: list[dict[str, Any]] = []
    for a in anomalies:
        agent = getattr(a, "agent", None)
        resume.append(
            {
                "date": a.date_detection.date().isoformat(),
                "type": str(a.type_anomalie.value if hasattr(a.type_anomalie, "value") else a.type_anomalie),
                "statut": str(
                    a.statut_justification.value
                    if hasattr(a.statut_justification, "value")
                    else a.statut_justification
                ),
                "agent": f"{agent.prenom} {agent.nom}" if agent else f"agent#{a.id_agent}",
                "matricule": getattr(agent, "matricule", None) if agent else None,
            }
        )

    prompt = (
        f"Voici les {total} anomalies de pointage détectées entre "
        f"{date_debut.isoformat()} et {date_fin.isoformat()} "
        f"(service {'#' + str(id_service) if id_service else 'tous services'}).\n\n"
        f"Données (max 200 dernières) :\n{json.dumps(resume, ensure_ascii=False)}\n\n"
        "Réponds en JSON strict avec la structure suivante :\n"
        "{\n"
        '  "synthese": "1 à 2 phrases",\n'
        '  "tendances": ["...", "..."],  // 2 à 5 tendances observées\n'
        '  "agents_a_surveiller": [{"agent": "Prénom Nom", "raison": "..."}],\n'
        '  "recommandations_rh": ["...", "..."]  // actions concrètes\n'
        "}"
    )
    contenu = _appeler_llm(
        [
            {"role": "system", "content": _SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ],
        response_json=True,
        temperature=0.3,
    )
    try:
        return {
            "periode_debut": date_debut,
            "periode_fin": date_fin,
            "nombre_anomalies_analysees": total,
            "analyse": json.loads(contenu),
        }
    except json.JSONDecodeError:
        return {
            "periode_debut": date_debut,
            "periode_fin": date_fin,
            "nombre_anomalies_analysees": total,
            "analyse": {"synthese": contenu, "tendances": [], "agents_a_surveiller": [], "recommandations_rh": []},
        }


# --------------------------------------------------------------------------
# 2) Prévisions commentées
# --------------------------------------------------------------------------
def commenter_previsions(
    db: Session,
    *,
    id_service: Optional[int] = None,
    horizon: int = 3,
) -> dict[str, Any]:
    from app.models.enums import TypePeriode

    prevision = bi_service.prevision(
        db,
        TypePeriode.MOIS,
        id_service=id_service,
        nombre_periodes_historique=6,
        horizon=horizon,
        date_reference=None,
    )

    # `prevision` est un dict Pydantic-friendly.
    contexte = json.dumps(_json_safe(prevision), ensure_ascii=False)
    prompt = (
        "Voici la prévision numérique (régression linéaire) sur les indicateurs "
        f"de présence pour les {horizon} prochains mois :\n{contexte}\n\n"
        "Réponds en JSON strict :\n"
        "{\n"
        '  "resume_executif": "2 à 3 phrases",\n'
        '  "risques": ["..."],\n'
        '  "opportunites": ["..."],\n'
        '  "actions_preventives": ["..."]\n'
        "}"
    )
    contenu = _appeler_llm(
        [
            {"role": "system", "content": _SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ],
        response_json=True,
    )
    try:
        commentaire = json.loads(contenu)
    except json.JSONDecodeError:
        commentaire = {"resume_executif": contenu, "risques": [], "opportunites": [], "actions_preventives": []}

    return {"prevision": prevision, "commentaire_ia": commentaire}


# --------------------------------------------------------------------------
# 3) Rapport RH auto
# --------------------------------------------------------------------------
def generer_rapport(
    db: Session,
    *,
    id_service: Optional[int] = None,
    periode: str = "hebdomadaire",
) -> dict[str, Any]:
    jours = 7 if periode == "hebdomadaire" else 30
    date_fin = date_.today()
    date_debut = date_fin - timedelta(days=jours - 1)
    from app.models.enums import TypePeriode

    tendances = bi_service.tendances(
        db,
        TypePeriode.JOUR if periode == "hebdomadaire" else TypePeriode.SEMAINE,
        id_service,
        date_debut,
        date_fin,
    )
    classement = bi_service.classement_agents(
        db, date_debut, date_fin, id_service=id_service, critere="ponctualite", limite=5
    )
    retards = bi_service.classement_agents(
        db, date_debut, date_fin, id_service=id_service, critere="retards", limite=5
    )

    contexte = {
        "periode": periode,
        "date_debut": date_debut.isoformat(),
        "date_fin": date_fin.isoformat(),
        "tendances": _json_safe(tendances),
        "top_ponctuels": _json_safe(classement),
        "top_retards": _json_safe(retards),
    }
    prompt = (
        "Rédige un rapport RH clair et structuré en Markdown à partir de ces "
        f"données de pointage :\n{json.dumps(contexte, ensure_ascii=False)}\n\n"
        "Sections attendues :\n"
        "1. Résumé exécutif (3 à 5 lignes)\n"
        "2. Indicateurs clés (chiffres précis, avec évolution)\n"
        "3. Points d'attention (retards, absences récurrentes)\n"
        "4. Reconnaissances (agents exemplaires)\n"
        "5. Recommandations opérationnelles\n"
        "\nSois factuel : n'invente aucun chiffre absent des données."
    )
    contenu = _appeler_llm(
        [
            {"role": "system", "content": _SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=1800,
    )
    return {
        "periode": periode,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "id_service": id_service,
        "rapport_markdown": contenu,
    }


# --------------------------------------------------------------------------
# 4) Assistant RH conversationnel spécialisé
# --------------------------------------------------------------------------
def repondre_question_rh(
    db: Session,
    *,
    question: str,
    id_service: Optional[int] = None,
) -> dict[str, Any]:
    # On fournit à l'IA un instantané temps-réel + les tendances récentes,
    # elle raisonne à partir de ce contexte factuel (elle n'a pas accès à
    # la BDD directement).
    tdb = bi_service.tableau_de_bord_temps_reel(db, id_service=id_service, jour=None)

    from app.models.enums import TypePeriode
    date_fin = date_.today()
    date_debut = date_fin - timedelta(days=30)
    tendances = bi_service.tendances(db, TypePeriode.SEMAINE, id_service, date_debut, date_fin)

    contexte = {
        "aujourdhui": _json_safe(tdb),
        "tendances_30j": _json_safe(tendances),
    }
    prompt = (
        f"Question de l'utilisateur RH : « {question} »\n\n"
        f"Données disponibles :\n{json.dumps(contexte, ensure_ascii=False)}\n\n"
        "Réponds de manière concise et factuelle. Si l'information n'est pas "
        "dans les données ci-dessus, dis-le explicitement et propose l'endroit "
        "de l'application où l'agent RH peut aller la chercher (page Anomalies, "
        "Rapports, Agents, etc.). Ne réponds pas aux questions hors du "
        "périmètre RH / pointage."
    )
    reponse = _appeler_llm(
        [
            {"role": "system", "content": _SYSTEM_BASE},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=800,
    )
    return {"question": question, "reponse": reponse}


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------
def _json_safe(obj: Any) -> Any:
    """Convertit récursivement dates/Decimal/Pydantic en types JSON-sérialisables."""
    if hasattr(obj, "model_dump"):
        return _json_safe(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (date_,)):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)
