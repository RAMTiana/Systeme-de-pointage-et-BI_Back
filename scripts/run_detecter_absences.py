"""Script d'appoint : exécute `anomalie_service.detecter_absences`.

Usage (depuis la racine du projet) :
    source .venv/bin/activate
    python -m scripts.run_detecter_absences

Ce script ouvre une session DB, appelle la détection pour la veille
par défaut et affiche le nombre d'anomalies créées ainsi que leurs IDs.
"""
from datetime import date, timedelta

from app.db.session import SessionLocal
from app.services import anomalie_service


def main(jour=None):
    db = SessionLocal()
    try:
        jour = jour or (date.today() - timedelta(days=1))
        anomalies = anomalie_service.detecter_absences(db, jour=jour)
        print(f"Détection pour {jour.isoformat()} : {len(anomalies)} anomalie(s) créées.")
        for a in anomalies:
            print(f"- id_anomalie={a.id_anomalie} id_agent={a.id_agent} date_detection={a.date_detection}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
