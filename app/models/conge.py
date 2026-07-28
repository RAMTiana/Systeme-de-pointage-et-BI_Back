from datetime import date as date_
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import StatutConge, TypeConge, pg_enum

if TYPE_CHECKING:
    from app.models.agent import Agent


class Conge(Base):
    """
    Période de congé (annuel, maladie, maternité...) enregistrée pour un
    agent.

    IMPORTANT — articulation avec le système national : la fonction publique
    malgache dispose déjà d'une application dédiée aux demandes de congé et
    d'absence, commune à tous les ministères. Ce module SRB n'a PAS vocation
    à s'y substituer : il ne porte aucune décision, aucune étape
    d'approbation locale. Il se contente d'enregistrer, pour le périmètre du
    SRB Haute Matsiatra, qu'un agent est en congé déjà validé ailleurs.

    Objectif unique : permettre à `anomalie_service.detecter_absences`
    d'exclure de la détection automatique d'absence les agents en congé ce
    jour-là (cf. `conge_service.agents_en_conge`) — un agent en congé ne
    pointe jamais, ce qui le faisait auparavant signaler à tort comme
    absent.

    Workflow (volontairement minimal) : la Secrétaire enregistre le congé
    une fois qu'il est confirmé (`conge_service.creer`), statut ACTIF dès la
    création — il n'y a rien à approuver ici, la décision a déjà été prise
    en amont. Elle peut l'annuler en cas d'erreur de saisie
    (`conge_service.annuler`). L'enregistrement justifie aussi
    rétroactivement les anomalies 'absence' déjà consignées sur la période
    (cf. `conge_service._justifier_absences_couvertes`), pour le cas où le
    congé est saisi après coup (ex. arrêt maladie transmis en retard).
    """
    __tablename__ = "conge"
    __table_args__ = (
        CheckConstraint("date_fin >= date_debut", name="ck_conge_dates_coherentes"),
    )

    id_conge: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, index=True
    )
    type_conge: Mapped[TypeConge] = mapped_column(pg_enum(TypeConge, "type_conge"), nullable=False)
    date_debut: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    date_fin: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    motif: Mapped[Optional[str]] = mapped_column(Text)
    statut: Mapped[StatutConge] = mapped_column(
        pg_enum(StatutConge, "statut_conge"),
        default=StatutConge.ACTIF, server_default=StatutConge.ACTIF.value, nullable=False,
    )

    id_utilisateur_saisie: Mapped[int] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="RESTRICT"), nullable=False
    )

    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="conges")

    def __repr__(self) -> str:
        return (
            f"<Conge id={self.id_conge} agent={self.id_agent} "
            f"{self.date_debut}->{self.date_fin} statut={self.statut}>"
        )
