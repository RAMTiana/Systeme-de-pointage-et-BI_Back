"""
Bootstrap — création du tout premier compte administrateur.

`POST /api/v1/utilisateurs` est protégé par la permission `valider_roles` :
sur une base fraîchement migrée + seedée (rôles/permissions présents, mais
aucun compte `utilisateur`), personne ne peut donc appeler cet endpoint pour
créer le premier compte. Ce script contourne ce point de départ en insérant
directement le compte en base, une seule fois.

Pré-requis : `alembic upgrade head` puis `python -m scripts.seed_reference_data`
déjà exécutés (le rôle "Administrateur" doit exister).

Usage :
    python -m scripts.creer_premier_admin --login admin --email admin@srb-hautematsiatra.mg --nom "Administrateur SRB"
    (le mot de passe est demandé de façon masquée, jamais passé en argument)
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.rbac import Role
from app.models.utilisateur import Utilisateur


def main() -> None:
    parser = argparse.ArgumentParser(description="Crée le premier compte administrateur.")
    parser.add_argument("--login", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--nom", required=True, help="Nom complet")
    args = parser.parse_args()

    mot_de_passe = getpass.getpass("Mot de passe (8 caractères minimum) : ")
    confirmation = getpass.getpass("Confirmation : ")
    if mot_de_passe != confirmation:
        print("Les deux mots de passe ne correspondent pas.", file=sys.stderr)
        sys.exit(1)
    if len(mot_de_passe.encode("utf-8")) < 8:
        print("Le mot de passe doit faire au moins 8 caractères.", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        role_admin = db.execute(select(Role).where(Role.nom_role == "Administrateur")).scalar_one_or_none()
        if role_admin is None:
            print(
                "Rôle 'Administrateur' introuvable — exécutez d'abord "
                "`python -m scripts.seed_reference_data`.",
                file=sys.stderr,
            )
            sys.exit(1)

        existant = db.execute(
            select(Utilisateur).where(
                (Utilisateur.login == args.login) | (Utilisateur.email == args.email)
            )
        ).scalar_one_or_none()
        if existant is not None:
            print(f"Un compte existe déjà avec ce login ou cet e-mail (id={existant.id_utilisateur}).", file=sys.stderr)
            sys.exit(1)

        utilisateur = Utilisateur(
            login=args.login,
            email=args.email,
            nom_complet=args.nom,
            mot_de_passe_hash=hash_password(mot_de_passe),
            email_verifie=True,  # Compte de bootstrap : pas de flux de vérification à rejouer.
            actif=True,
            id_role=role_admin.id_role,
        )
        db.add(utilisateur)
        db.commit()
        db.refresh(utilisateur)
        print(f"Compte administrateur créé : id={utilisateur.id_utilisateur}, login={utilisateur.login}")
    finally:
        db.close()


if __name__ == "__main__":
    main()