<!-- mcp-name: io.github.QuincyGB/list-smith-mcp -->
# list-smith-mcp

[![M8ven Score](https://m8ven.ai/badge/mcp/quincygb/list-smith-mcp)](https://m8ven.ai/mcp/quincygb/list-smith-mcp?s=readme)

> **Standalone home:** This server was split out of `QuincyGB/token-verdict-tools` so each MCP server has its own GitHub repository for directory ingestion and maintenance. The PyPI package name (`list-smith-mcp`), version, and install command are unchanged.

An [MCP](https://modelcontextprotocol.io) server (stdio transport) that
exposes **ListSmith** as native tools for any MCP host — Claude
Desktop, Cursor, or your own agent runtime.

ListSmith grades and repairs the Bazaar discovery metadata of x402
services. **`/v1/grade`** ($0.10) itemizes what is broken in a route's
discovery metadata — hollow schemas, failed SDK validation,
over-500-char descriptions, wrong asset addresses. **`/v1/fix`**
($0.50) returns a repaired, validator-passed discovery package inferred
from one real sample output. Both are paid in USDC on Base via the
[x402 protocol](https://x402.org) — no accounts, no API keys; the
payment is the authentication.

## The tools

**`list_grade`** — diagnosis only, no repaired content:

| Input | Type | Notes |
|---|---|---|
| `resource_url` | string (required) | Full URL of the x402 resource to grade |
| `sample_output` | any (optional) | One real response body from the endpoint; when supplied, it is also checked against the endpoint's published output schema |

Output: `resource_url`, `graded_at`, `reachable`, `http_status`,
`x402_valid`, `terms`, `extension_present`, `sdk_validation`,
`schema_quality`, `defects` (with severities), `score` (0–100), and
`fix_available`.

**`list_fix`** — the repair:

| Input | Type | Notes |
|---|---|---|
| `resource_url` | string (required) | Full URL of the x402 resource to repair |
| `sample_output` | any (required) | One real response body from the endpoint. The repaired output schema is inferred from this sample only — nothing is invented |
| `name` | string (optional) | Display name for the generated description; derived from the URL when omitted |
| `category` | string (optional) | Category (e.g. token-risk, search, data, media, monitoring) used to phrase the description's use-when sentence |

Output: `resource_url`, `graded_at`, `ok`, `failure`, `score_before`,
`defects`, `package` (description, repaired Bazaar extension, OpenAPI
and well-known snippets, instructions, validation results — shipped
only if it passes both x402 SDK validators), and `notes`.

## Install

```bash
pip install list-smith-mcp

# Or from a checkout of this repository:
pip install .
```

Requires Python ≥ 3.10. The server pins the MCP v1 SDK API
(`mcp>=1.2,<2`).

## Configuration

All configuration is the **operator's own environment** — whoever runs
this server supplies their own wallet. No key material ships in this
package, and the package author never sees your key.

| Env var | Required | Purpose |
|---|---|---|
| `AGENT_PRIVATE_KEY` | for paid calls | EVM private key (`0x...`) of a wallet holding USDC on Base. Used locally to sign the x402 EIP-3009 authorization. |
| `AGENT_KEY_FILE` | alternative | Path to a file containing that key. |
| `LIST_SMITH_URL` | no | Service base URL override (default: production). |
| `LIST_SMITH_GRADE_MAX_AMOUNT_UNITS` | no | Spend guard for `/v1/grade` in USDC micro-units (default `100000` = $0.10, the known price). |
| `LIST_SMITH_FIX_MAX_AMOUNT_UNITS` | no | Spend guard for `/v1/fix` in USDC micro-units (default `500000` = $0.50, the known price). |

Each route is capped at its own price — the client refuses to sign
anything above the route's guard. **Without a signer configured the
tools never pay.** On a 402 they return the parsed challenge as a
structured error — `error: "payment_required"` with the full `accepts`
array (scheme, network, asset, amount, payTo) — so the calling agent
can see the price and decide what to do, or settle the challenge with
its own x402 client.

## MCP host configuration

Claude Desktop (`claude_desktop_config.json`) — same shape for other
stdio MCP hosts:

```json
{
  "mcpServers": {
    "list-smith": {
      "command": "list-smith-mcp",
      "env": {
        "AGENT_PRIVATE_KEY": "0xYOUR_OWN_WALLET_KEY"
      }
    }
  }
}
```

From a checkout instead of an install:

```json
{
  "mcpServers": {
    "list-smith": {
      "command": "python",
      "args": ["-m", "list_smith_mcp"],
      "env": {
        "PYTHONPATH": "/path/to/list-smith-mcp/src",
        "AGENT_KEY_FILE": "/path/to/your/agent.key"
      }
    }
  }
}
```

Prefer `AGENT_KEY_FILE` over inlining a key in a config file when your
host setup allows it.

## How payment works

The client mirrors the standard x402 v2 flow (the same flow used for
the fleet's own live cataloging payments):

1. `POST /v1/grade` or `/v1/fix` unpaid → HTTP 402, challenge
   base64-encoded in the `payment-required` header.
2. Pick the `exact` / `eip155:8453` entry from `accepts`.
3. Build an EIP-3009 `TransferWithAuthorization` (`validAfter: 0`,
   `validBefore: now + maxTimeoutSeconds`, random 32-byte nonce) and
   sign it as EIP-712 typed data against the USDC domain the challenge
   advertises (`extra.name` / `extra.version`).
4. Retry the identical POST with the payload in the `PAYMENT-SIGNATURE`
   header. The service verifies and settles through its facilitator
   (PayAI) server-side; the settlement receipt comes back in the
   `payment-response` header.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

All payments mocked (httpx `MockTransport`, throwaway signer generated
at test time). The 402 fixtures are the real challenges captured from
production; the paid-flow tests verify the EIP-712 signatures actually
recover to the signer address, for both routes.

## Security notes

- Your private key is read from your environment at runtime and used
  only to sign payment authorizations locally. It is never transmitted —
  the x402 payload contains a signature, not a key.
- The per-route spend guards cap any single call at the configured
  amounts (defaults: exactly the $0.10 grade and $0.50 fix prices).
- Run this with a dedicated agent wallet holding only working funds,
  the same way you would fund any agent.
