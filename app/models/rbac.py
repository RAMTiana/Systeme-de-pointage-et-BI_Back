from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Column, ForeignKey, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.utilisateur import Utilisateur

# Table d'association pure (pas de colonnes additionnelles) : role_permission
role_permission = Table(
    "role_permission",
    Base.metadata,
    Column("id_role", ForeignKey("role.id_role", ondelete="CASCADE"), primary_key=True),
    Column("id_permission", ForeignKey("permission.id_permission", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "role"

    id_role: Mapped[int] = mapped_column(primary_key=True)
    nom_role: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    # Relations
    permissions: Mapped[List["Permission"]] = relationship(
        secondary=role_permission, back_populates="roles"
    )
    utilisateurs: Mapped[List["Utilisateur"]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role id={self.id_role} nom={self.nom_role!r}>"


class Permission(Base):
    __tablename__ = "permission"

    id_permission: Mapped[int] = mapped_column(primary_key=True)
    nom_permission: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Relations
    roles: Mapped[List["Role"]] = relationship(secondary=role_permission, back_populates="permissions")

    def __repr__(self) -> str:
        return f"<Permission id={self.id_permission} nom={self.nom_permission!r}>"
