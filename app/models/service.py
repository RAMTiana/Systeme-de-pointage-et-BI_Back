from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.affectation import Affectation
    from app.models.rapport import Rapport
    from app.models.horaire_reference import HoraireReference


class Service(Base):
    __tablename__ = "service"

    id_service: Mapped[int] = mapped_column(primary_key=True)
    nom_service: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Relations
    agents: Mapped[List["Agent"]] = relationship(back_populates="service")
    # cascade="all, delete-orphan" : cohérent avec la contrainte SQL
    # `affectation.id_service ... ON DELETE CASCADE NOT NULL` — sans cette
    # cascade côté ORM, SQLAlchemy tente par défaut de mettre id_service à
    # NULL sur les affectations lors de la suppression du service, ce qui
    # viole la contrainte NOT NULL.
    affectations: Mapped[List["Affectation"]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )
    rapports: Mapped[List["Rapport"]] = relationship(back_populates="service")
    horaires_reference: Mapped[List["HoraireReference"]] = relationship(back_populates="service")

    def __repr__(self) -> str:
        return f"<Service id={self.id_service} nom={self.nom_service!r}>"
