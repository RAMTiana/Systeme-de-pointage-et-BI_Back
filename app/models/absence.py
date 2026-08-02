from datetime import date as date_
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import StatutAbsence, pg_enum

if TYPE_CHECKING:
    from app.models.agent import Agent


class Absence(Base):
    """
    Absence ponctuelle enregistrée pour un agent, déjà connue/justifiée par
    l'application nationale de gestion des absences de la fonction publique
    malgache (commune à tous les ministères, pas seulement au SRB Haute
    Matsiatra).

    IMPORTANT — même logique que `Conge` (cf. sa docstring) : ce module
    n'a PAS vocation à se substituer au système central. Il ne porte aucune
    décision, aucune étape d'approbation locale. Il se contente d'enregistrer,
    pour le périmètre du SRB, qu'une absence est déjà connue ailleurs.

    Objectif unique : permettre à `anomalie_service.detecter_absences`
    d'exclure de la détection automatique d'absence les agents couverts par
    un enregistrement ici (cf. `absence_service.agents_en_absence`) — un
    agent absent pour un motif déjà remonté au niveau central ne doit pas
    être signalé une seconde fois comme anomalie locale ni déclencher une
    alerte à la hiérarchie.

    Workflow (volontairement minimal, identique à `Conge`) : la Secrétaire
    enregistre l'absence une fois qu'elle en a connaissance
    (`absence_service.creer`), statut ACTIF dès la création — il n'y a rien
    à approuver ici. Elle peut l'annuler en cas d'erreur de saisie
    (`absence_service.annuler`). L'enregistrement justifie aussi
    rétroactivement les anomalies 'absence' déjà consignées sur la période
    (cf. `absence_service._justifier_absences_couvertes`), pour le cas où
    l'absence est saisie après coup, une fois déjà détectée par le job de la
    veille.
    """
    __tablename__ = "absence"
    __table_args__ = (
        CheckConstraint("date_fin >= date_debut", name="ck_absence_dates_coherentes"),
    )

    id_absence: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, index=True
    )
    date_debut: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    date_fin: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    motif: Mapped[Optional[str]] = mapped_column(Text)
    statut: Mapped[StatutAbsence] = mapped_column(
        pg_enum(StatutAbsence, "statut_absence"),
        default=StatutAbsence.ACTIF, server_default=StatutAbsence.ACTIF.value, nullable=False,
    )

    id_utilisateur_saisie: Mapped[int] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="RESTRICT"), nullable=False
    )

    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="absences")

    def __repr__(self) -> str:
        return (
            f"<Absence id={self.id_absence} agent={self.id_agent} "
            f"{self.date_debut}->{self.date_fin} statut={self.statut}>"
        )
