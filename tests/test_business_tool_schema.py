"""Contract-only tests; no browser, model, API keys, or file mutations."""
import re

import pytest

from web_agent.business_input import BusinessInput, FIELD_UNITS
from web_agent.delivery_schema import UNITS, field_for


def _comparison_schema():
    return BusinessInput.model_json_schema()["properties"]["sheets"]["items"]["anyOf"][0]


def test_unit_schema_is_backend_driven_and_model_visible():
    fields = _comparison_schema()["properties"]["fields"]["additionalProperties"]["properties"]
    units = fields["unit"]
    assert units["enum"] == FIELD_UNITS
    assert set(UNITS) <= set(units["enum"])
    assert {"CNY", "USD", "SGD", "EUR", "GBP", "JPY"} <= set(units["enum"])
    assert "核" not in units["enum"]
    assert "count" in units["enum"]
    assert "type" in fields and "required" in fields
    assert field_for("一般计数", {"一般计数": {"type": "number", "unit": "count"}})["unit"] == "count"
    assert field_for("CPU核心数", {"CPU核心数": {"type": "number", "unit": "核"}})["unit"] == "count"
    with pytest.raises(ValueError, match="单位不受支持"):
        field_for("CPU核心数", {"CPU核心数": {"type": "number", "unit": "rpm"}})


def test_price_schema_exposes_real_metadata_constraints():
    cells = _comparison_schema()["properties"]["rows"]["items"]["properties"]["cells"]
    price = cells["additionalProperties"]["properties"]["price"]["properties"]
    assert price["type"]["enum"] == ["launch", "current", "starting", "promotional"]
    assert re.fullmatch(price["currency"]["pattern"], "CNY")
    assert not re.fullmatch(price["currency"]["pattern"], "人民币")
    assert re.fullmatch(price["market"]["pattern"], "CN")
    assert not re.fullmatch(price["market"]["pattern"], "China")
    assert re.fullmatch(price["queried_at"]["pattern"], "2026-10-21")
    assert not re.fullmatch(price["queried_at"]["pattern"], "today")


def test_pydantic_runtime_legacy_adapter_unchanged():
    # Model-facing guidance is NOT used to overwrite the precise runtime errors.
    samples = [
        {"sheets": [{"arbitrary": "legacy payload"}]},
        {"sheets": [{"name": "指标", "mode": "comparison", "columns": ["CPU核心数"],
                     "fields": {"CPU核心数": {"type": "number", "unit": "核"}},
                     "rows": [{"label": "A", "cells": {"CPU核心数": {"value": 6}}}]}]},
    ]
    for sample in samples:
        assert BusinessInput.model_validate(sample).model_dump() == sample


def test_schema_for_table_and_calculation_remains_visible():
    modes = _comparison_schema()
    other = BusinessInput.model_json_schema()["properties"]["sheets"]["items"]["anyOf"][1]
    assert modes["properties"]["mode"]["const"] == "comparison"
    assert other["properties"]["mode"]["enum"] == ["table", "calculation"]


def test_non_comparison_schema_requires_name_and_mode():
    other = BusinessInput.model_json_schema()["properties"]["sheets"]["items"]["anyOf"][1]
    assert set(other.get("required", [])) == {"name", "mode"}
    assert "filename" in other["properties"]
    assert "calculation_id" in other["properties"]
