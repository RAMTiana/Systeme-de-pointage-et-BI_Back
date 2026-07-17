"""
Service métier — "Mot de passe oublié" (procédure libre-service, distincte
de la réinitialisation par un administrateur déjà couverte par
`utilisateur_service.reinitialiser_mot_de_passe`).

Principe de sécurité central : ne jamais laisser un appelant non authentifié
déterminer si un identifiant correspond à un compte existant
(énumération de comptes). La demande de réinitialisation répond donc
toujours le même message générique, que le compte existe, soit un compte
Google, soit désactivé, ou n'existe pas — seul le journal d'audit
distingue ces cas côté back-office.
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core import notifications, password_policy

from app.models.enums import AuthProvider, TypeCode
from app.services import code_verification_service, journal_audit_service, parametre_service, utilisateur_service

_ECHEC_REINITIALISATION = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="Identifiant, code ou mot de passe invalide.",
)


def demander_reinitialisation(db: Session, identifiant: str) -> None:
    """
    Génère et envoie un code de réinitialisation par email, si et seulement
    si l'identifiant correspond à un compte local actif. Ne lève jamais
    d'exception : l'endpoint renvoie toujours la même réponse générique.
    """
    utilisateur = utilisateur_service.get_by_login_or_email(db, identifiant)

    if utilisateur is None or utilisateur.auth_provider != AuthProvider.LOCAL or not utilisateur.actif:
        journal_audit_service.log_action(
            db,
            id_utilisateur=utilisateur.id_utilisateur if utilisateur else None,
            action="demande_reinitialisation_mot_de_passe_refusee",
            details=f"identifiant={identifiant} (compte inexistant, Google, ou désactivé)",
        )
        return

    code_clair = code_verification_service.generer_code(db, utilisateur, TypeCode.RESET_PASSWORD)
    expiration_minutes = parametre_service.get_int(db, "code_verification_expiration_minutes", default=15)

    sujet = "SRB Haute Matsiatra — Réinitialisation de votre mot de passe"
    corps = (
        f"Bonjour {utilisateur.nom_complet},\n\n"
        f"Voici votre code de réinitialisation de mot de passe : {code_clair}\n"
        f"Ce code est valable {expiration_minutes} minutes et à usage unique.\n\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email : "
        "votre mot de passe reste inchangé."
    )
    notifications.envoyer_email(utilisateur.email, sujet, corps)

    journal_audit_service.log_action(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        action="demande_reinitialisation_mot_de_passe",
    )


def reinitialiser(db: Session, identifiant: str, code: str, nouveau_mot_de_passe: str) -> None:
    """
    Vérifie le code reçu par email et applique le nouveau mot de passe.
    Lève une 400 générique dans tous les cas d'échec (identifiant inconnu,
    compte Google, ou code invalide/expiré) — même raisonnement anti-
    énumération que `demander_reinitialisation`.
    """
    utilisateur = utilisateur_service.get_by_login_or_email(db, identifiant)
    if utilisateur is None or utilisateur.auth_provider != AuthProvider.LOCAL:
        raise _ECHEC_REINITIALISATION

    try:
        code_verification_service.valider_code(db, utilisateur, code, TypeCode.RESET_PASSWORD)
    except HTTPException:
        raise _ECHEC_REINITIALISATION

    # Politique de mot de passe appliquée après validation du code, pour ne
    # pas donner d'indice avant qu'un code correct ait été présenté.
    password_policy.validate(
        nouveau_mot_de_passe, login=utilisateur.login, email=utilisateur.email
    )

    utilisateur_service.reinitialiser_mot_de_passe(db, utilisateur, nouveau_mot_de_passe)

    journal_audit_service.log_action(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        action="mot_de_passe_reinitialise_libre_service",
    )
