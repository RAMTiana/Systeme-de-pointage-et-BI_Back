"""
Connecteurs techniques d'envoi des alertes (Processus 3 — "Envoyer alerte
automatique", email et/ou SMS vers la hiérarchie).

Ce module est volontairement isolé de la logique métier (app/services/
alerte_service.py) : il ne sait qu'envoyer un message, il ne sait pas qui
doit le recevoir ni pourquoi. Conformément à la conception BPMN
("les tâches automatiques recevront leurs connecteurs techniques lors de
la phase d'implémentation"), ce backend fournit un connecteur SMTP réel
pour l'email et un connecteur webhook générique pour le SMS (le
fournisseur SMS n'étant pas figé au cahier des charges).

Principe : ne jamais simuler un succès. Si un canal n'est pas configuré
(pas de SMTP_HOST / pas de SMS_WEBHOOK_URL), l'envoi échoue proprement
(retourne False) plutôt que de faire croire qu'une notification est
partie alors qu'aucune infrastructure n'est branchée.
"""
import logging
import smtplib
from email.message import EmailMessage

import requests

from app.core.config import settings

logger = logging.getLogger("srb.notifications")


def envoyer_email(destinataire: str, sujet: str, corps: str) -> bool:
    """Envoie un email via SMTP. Retourne False (sans lever d'exception) en cas d'échec."""
    if not settings.SMTP_HOST:
        logger.warning(
            "SMTP non configuré (SMTP_HOST manquant) : alerte email à %s non envoyée.",
            destinataire,
        )
        return False

    message = EmailMessage()
    message["Subject"] = sujet
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = destinataire
    message.set_content(corps)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as serveur:
            if settings.SMTP_USE_TLS:
                serveur.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                serveur.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            serveur.send_message(message)
        return True
    except Exception:
        logger.exception("Échec de l'envoi de l'alerte email à %s.", destinataire)
        return False


def envoyer_sms(destinataire: str, message: str) -> bool:
    """Envoie un SMS via un webhook générique. Retourne False (sans lever d'exception) en cas d'échec."""
    if not settings.SMS_WEBHOOK_URL:
        logger.warning(
            "Passerelle SMS non configurée (SMS_WEBHOOK_URL manquant) : alerte SMS à %s non envoyée.",
            destinataire,
        )
        return False

    headers = {}
    if settings.SMS_WEBHOOK_API_KEY:
        headers["Authorization"] = f"Bearer {settings.SMS_WEBHOOK_API_KEY}"

    try:
        reponse = requests.post(
            settings.SMS_WEBHOOK_URL,
            json={"to": destinataire, "message": message},
            headers=headers,
            timeout=10,
        )
        reponse.raise_for_status()
        return True
    except Exception:
        logger.exception("Échec de l'envoi de l'alerte SMS à %s.", destinataire)
        return False
