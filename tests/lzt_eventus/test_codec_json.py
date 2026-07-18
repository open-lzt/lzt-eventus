"""`to_jsonable` / `encode_event` codec edge cases — pydantic values + field caching."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from lzt_eventus.codecs.json import _extra_field_names, to_jsonable
from lzt_eventus.events.payment import ItemSold


class _NestedModel(BaseModel):
    amount: Decimal
    label: str


def test_to_jsonable_pydantic_model_keeps_money_as_string() -> None:
    result = to_jsonable(_NestedModel(amount=Decimal("12.50"), label="x"))
    assert result == {"amount": "12.50", "label": "x"}
    assert isinstance(result["amount"], str)  # never a float — codec money invariant


def test_to_jsonable_nested_pydantic_in_collection() -> None:
    result = to_jsonable([_NestedModel(amount=Decimal("1.00"), label="a")])
    assert result == [{"amount": "1.00", "label": "a"}]


def test_extra_field_names_excludes_base_envelope() -> None:
    names = _extra_field_names(ItemSold)
    assert "operation_id" in names
    assert "amount" in names
    assert "event_id" not in names  # base envelope field, not subclass-specific
    assert "payload" not in names
    # cached: same object identity on repeated calls
    assert _extra_field_names(ItemSold) is names
