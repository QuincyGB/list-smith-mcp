"""Server-level tests: both tools are registered with the real
endpoints' input schemas, and tool calls route through the (mocked)
client logic."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from list_smith_mcp.schemas import (
    FIX_INPUT_SCHEMA,
    FIX_OUTPUT_SCHEMA,
    GRADE_INPUT_SCHEMA,
    GRADE_OUTPUT_SCHEMA,
)
from list_smith_mcp.server import mcp, run_fix, run_grade

FIXTURES = Path(__file__).parent / "fixtures"
GRADE = json.loads((FIXTURES / "grade_example.json").read_text())
FIX = json.loads((FIXTURES / "fix_example.json").read_text())
TARGET = "https://token-verdict-production.up.railway.app/v1/verdict"


def test_tools_registered_with_real_input_schemas():
    tools = asyncio.run(mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"list_grade", "list_fix"}

    # FastMCP generates the tool schemas from the Python signatures, so
    # they can't be byte-identical to the services' wire schemas —
    # assert the substantive contracts match instead: same fields, same
    # required sets.
    grade = by_name["list_grade"].inputSchema
    assert set(grade["properties"]) == set(GRADE_INPUT_SCHEMA["properties"])
    assert grade["properties"]["resource_url"]["type"] == "string"
    assert grade["required"] == GRADE_INPUT_SCHEMA["required"]

    fix = by_name["list_fix"].inputSchema
    assert set(fix["properties"]) == set(FIX_INPUT_SCHEMA["properties"])
    assert fix["properties"]["resource_url"]["type"] == "string"
    assert fix["properties"]["name"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert fix["required"] == FIX_INPUT_SCHEMA["required"]


def test_output_schemas_match_service_contracts():
    # The wrapper advertises exactly the service's response shapes.
    assert GRADE_OUTPUT_SCHEMA["required"] == [
        "resource_url",
        "graded_at",
        "reachable",
        "http_status",
        "x402_valid",
        "terms",
        "extension_present",
        "sdk_validation",
        "schema_quality",
        "defects",
        "score",
        "fix_available",
    ]
    for key in GRADE_OUTPUT_SCHEMA["required"]:
        assert key in GRADE

    assert FIX_OUTPUT_SCHEMA["required"] == [
        "resource_url",
        "graded_at",
        "ok",
        "failure",
        "score_before",
        "defects",
        "package",
        "notes",
    ]
    for key in FIX_OUTPUT_SCHEMA["required"]:
        assert key in FIX


def test_tool_calls_via_run_functions_happy_path():
    class FakeClient:
        def get_grade(self, resource_url, sample_output=None):
            assert resource_url == TARGET
            return GRADE

        def get_fix(self, resource_url, sample_output, name=None, category=None):
            assert resource_url == TARGET
            assert sample_output is not None
            return FIX

    grade = run_grade(TARGET, client=FakeClient())
    assert isinstance(grade["score"], int) and 0 <= grade["score"] <= 100

    fix = run_fix(TARGET, {"verdict": "CAUTION"}, client=FakeClient())
    assert fix["ok"] is True
    assert fix["package"] is not None
