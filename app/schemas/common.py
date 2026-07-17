"""Schémas Pydantic génériques réutilisables par plusieurs modules."""
from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Résultat paginé : `items` pour la page courante, `total` pour le nombre global de résultats."""
    items: List[T]
    total: int
    skip: int
    limit: int
