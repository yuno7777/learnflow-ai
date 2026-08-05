from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PageParams(BaseModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 25
    offset: Annotated[int, Field(ge=0)] = 0
    sort: str | None = None


def page_params(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: str | None = Query(None, description="Field name, prefix with '-' for descending"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset, sort=sort)


class IdName(ORMModel):
    id: UUID
    name: str


class Timestamps(ORMModel):
    created_at: datetime
    updated_at: datetime
