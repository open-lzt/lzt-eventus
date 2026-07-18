"""Shared response envelopes — the only truly cross-cutting DTOs."""

from __future__ import annotations

from collections.abc import Sequence

from lzt_eventus.web.base.schema import BaseSchema


class DataResponse[T](BaseSchema):
    data: T


class Page[T](BaseSchema):
    items: Sequence[T]
    total: int
    limit: int
    offset: int


class Success(BaseSchema):
    ok: bool = True
