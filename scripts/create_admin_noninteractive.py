"""
Create an admin user non-interactively for CI/dev use.
Usage (from repo root):
    docker compose -f docker-compose.local.yml exec -T backend python -m scripts.create_admin_noninteractive --login superadmin --email you@example.test --nom "Super Admin" --password MyPassw0rd

This script will exit with code 0 if user already exists.
"""
import argparse
import sys
from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.rbac import Role
from app.models.utilisateur import Utilisateur


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--nom", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    if len(args.password.encode("utf-8")) < 8:
        print("Password must be at least 8 characters", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        role_admin = db.execute(select(Role).where(Role.nom_role == "Administrateur")).scalar_one_or_none()
        if role_admin is None:
            print("Administrateur role not found. Run seed_reference_data.", file=sys.stderr)
            sys.exit(1)

        existant = db.execute(
            select(Utilisateur).where(
                (Utilisateur.login == args.login) | (Utilisateur.email == args.email)
            )
        ).scalar_one_or_none()
        if existant is not None:
            print(f"User already exists: id={existant.id_utilisateur}")
            return

        utilisateur = Utilisateur(
            login=args.login,
            email=args.email,
            nom_complet=args.nom,
            mot_de_passe_hash=hash_password(args.password),
            email_verifie=True,
            actif=True,
            id_role=role_admin.id_role,
        )
        db.add(utilisateur)
        db.commit()
        db.refresh(utilisateur)
        print(f"Created admin: id={utilisateur.id_utilisateur}, login={utilisateur.login}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
