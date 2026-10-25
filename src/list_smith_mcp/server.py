"""MCP server (stdio) exposing ListSmith as two tools.

Run it with an MCP host (Claude Desktop, Cursor, any MCP client):

    list-smith-mcp            # after pip install
    python -m list_smith_mcp  # from a checkout

Configuration is entirely from the environment of whoever runs it:

    AGENT_PRIVATE_KEY   EVM private key (0x...) of YOUR wallet holding
                        USDC on Base — used to pay the per-call x402
                        charges automatically. Never shared with the
                        tool author; the key stays on your machine.
    AGENT_KEY_FILE      Alternative: path to a file containing the key.
    LIST_SMITH_URL      Override the service base URL
                        (default: production).
    LIST_SMITH_GRADE_MAX_AMOUNT_UNITS
                        Spend guard for /v1/grade, in USDC micro-units
                        (default 100000 = $0.10, the known price).
    LIST_SMITH_FIX_MAX_AMOUNT_UNITS
                        Spend guard for /v1/fix, in USDC micro-units
                        (default 500000 = $0.50, the known price).

With no signer configured the tools never pay: they return the parsed
402 challenge as a structured error so the calling agent can see the
price, asset, network and payTo and decide what to do.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import (
    DEFAULT_BASE_URL,
    DEFAULT_FIX_MAX_AMOUNT_UNITS,
    DEFAULT_GRADE_MAX_AMOUNT_UNITS,
    ListSmithClient,
    ListSmithError,
    PaymentRequiredError,
    load_account_from_env,
)

mcp = FastMCP("list-smith")


def build_client(env: dict | None = None) -> ListSmithClient:
    env = os.environ if env is None else env
    grade_raw = (env.get("LIST_SMITH_GRADE_MAX_AMOUNT_UNITS") or "").strip()
    fix_raw = (env.get("LIST_SMITH_FIX_MAX_AMOUNT_UNITS") or "").strip()
    return ListSmithClient(
        base_url=(env.get("LIST_SMITH_URL") or DEFAULT_BASE_URL),
        account=load_account_from_env(env),
        grade_max_amount_units=int(grade_raw) if grade_raw else DEFAULT_GRADE_MAX_AMOUNT_UNITS,
        fix_max_amount_units=int(fix_raw) if fix_raw else DEFAULT_FIX_MAX_AMOUNT_UNITS,
    )


def _guarded(call) -> dict[str, Any]:
    try:
        return call()
    except PaymentRequiredError as exc:
        return exc.structured_error()
    except ListSmithError as exc:
        return {
            "error": "upstream_error",
            "status": exc.status,
            "detail": exc.detail,
        }


def run_grade(
    resource_url: str,
    sample_output: Any = None,
    client: ListSmithClient | None = None,
) -> dict[str, Any]:
    """The list_grade logic, kept transport-free so tests can drive it
    with a mock HTTP client."""
    client = client or build_client()
    return _guarded(lambda: client.get_grade(resource_url, sample_output))


def run_fix(
    resource_url: str,
    sample_output: Any,
    name: str | None = None,
    category: str | None = None,
    client: ListSmithClient | None = None,
) -> dict[str, Any]:
    """The list_fix logic, kept transport-free so tests can drive it
    with a mock HTTP client."""
    client = client or build_client()
    return _guarded(lambda: client.get_fix(resource_url, sample_output, name, category))


@mcp.tool()
def list_grade(resource_url: str, sample_output: Any = None) -> dict[str, Any]:
    """Grade one x402 route's Bazaar discovery metadata.

    Calls ListSmith POST /v1/grade ($0.10 USDC on Base via x402).
    Returns SDK validation verdicts, a schema-quality score, itemized
    defects with severities, and a 0-100 grade. Diagnosis only — no
    repaired content. Use list_fix when you want the repair.

    Args:
        resource_url: Full URL of the x402 resource to grade.
        sample_output: Optional real response body from the endpoint;
            when supplied, it is also checked against the endpoint's
            published output schema.
    """
    return run_grade(resource_url, sample_output)


@mcp.tool()
def list_fix(
    resource_url: str,
    sample_output: Any,
    name: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Repair one x402 route's Bazaar discovery metadata.

    Calls ListSmith POST /v1/fix ($0.50 USDC on Base via x402).
    Returns a complete repaired discovery package — a full output
    schema inferred from your sample, a sub-500-char description with
    a use-when sentence, OpenAPI and well-known snippets, and paste-in
    instructions. The package ships only if it passes both x402 SDK
    validators.

    Args:
        resource_url: Full URL of the x402 resource to repair.
        sample_output: One real response body from the endpoint
            (required). The repaired output schema is inferred from
            this sample only — nothing is invented.
        name: Optional display name for the generated description;
            derived from the URL when omitted.
        category: Optional category (e.g. token-risk, search, data,
            media, monitoring) used to phrase the description's
            use-when sentence.
    """
    return run_fix(resource_url, sample_output, name, category)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
