"""ListSmith x402 client — the 402 -> sign -> retry flow.

This mirrors, field for field, the proven client flow used for the
fleet's live cataloging payments (Node: @x402/fetch + @x402/evm 2.28.0
`ExactEvmScheme`, x402 protocol v2, `exact` scheme) — the same flow the
sibling token-verdict-mcp package implements:

1. POST /v1/grade or /v1/fix unpaid -> HTTP 402 with a base64 JSON
   challenge in the `payment-required` response header.
2. Pick the `exact` / `eip155:8453` entry from `accepts`.
3. Build an EIP-3009 `TransferWithAuthorization`:
   {from, to: payTo, value: amount, validAfter: "0",
    validBefore: now + maxTimeoutSeconds, nonce: 32 random bytes}.
4. Sign it as EIP-712 typed data with domain
   {name: accepts.extra.name, version: accepts.extra.version,
    chainId: 8453, verifyingContract: accepts.asset}.
5. Retry the identical POST with the payment payload in the
   `PAYMENT-SIGNATURE` header (base64 JSON). The service's facilitator
   (PayAI) verifies and settles USDC on Base server-side.

Each route has its OWN spend guard: grade is capped at 100000 units
($0.10) and fix at 500000 units ($0.50) by default, so neither route
can ever be signed above its own known price.

The signer is ALWAYS supplied by whoever runs this package, from their
own environment (AGENT_PRIVATE_KEY or AGENT_KEY_FILE). No key material
ships in this package, and without a signer the client never pays — it
raises PaymentRequiredError carrying the parsed challenge instead.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account
from eth_account.signers.local import LocalAccount

DEFAULT_BASE_URL = "https://list-smith-production.up.railway.app"
GRADE_PATH = "/v1/grade"
FIX_PATH = "/v1/fix"
BASE_NETWORK = "eip155:8453"
# Per-route spend guards: grade costs 100000 units ($0.10), fix costs
# 500000 units ($0.50). Refuse to sign for anything above the route's
# configured cap (default: exactly the known price), so a hostile or
# misconfigured endpoint cannot drain more per call.
DEFAULT_GRADE_MAX_AMOUNT_UNITS = 100_000
DEFAULT_FIX_MAX_AMOUNT_UNITS = 500_000

TRANSFER_WITH_AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


class ListSmithError(Exception):
    """Non-payment failure from the service (carries HTTP status + body)."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"list smith request failed: HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class PaymentRequiredError(Exception):
    """The endpoint demands payment and this client could/did not pay.

    Carries the full parsed 402 challenge so callers can inspect or
    settle it themselves. Raised when no signer is configured, when the
    price exceeds the route's spend guard, or when a paid retry still
    gets a 402.
    """

    def __init__(self, challenge: dict, reason: str):
        super().__init__(reason)
        self.challenge = challenge
        self.reason = reason

    def structured_error(self) -> dict:
        """The challenge as a JSON-friendly structured error object."""
        return {
            "error": "payment_required",
            "reason": self.reason,
            "x402Version": self.challenge.get("x402Version"),
            "resource": self.challenge.get("resource"),
            "accepts": self.challenge.get("accepts"),
            "extensions": self.challenge.get("extensions"),
            "hint": (
                "ListSmith costs $0.10 USDC per grade and $0.50 USDC per "
                "fix on Base (x402). Configure AGENT_PRIVATE_KEY or "
                "AGENT_KEY_FILE with an EVM wallet holding USDC on Base "
                "and the call will be paid automatically, or settle this "
                "challenge with any x402 v2 client and retry."
            ),
        }


def parse_payment_required(response: httpx.Response) -> dict:
    """Parse the x402 v2 challenge from a 402 response.

    The challenge travels base64-encoded in the `payment-required`
    header; some stacks also put it in the JSON body. Raises ValueError
    when neither carries a usable challenge.
    """
    header = response.headers.get("payment-required")
    if header:
        try:
            decoded = json.loads(base64.b64decode(header).decode("utf-8"))
            if isinstance(decoded, dict) and decoded.get("accepts"):
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("accepts"):
        return body
    raise ValueError("402 response carried no parseable x402 payment challenge")


def select_requirement(
    challenge: dict, network: str = BASE_NETWORK, scheme: str = "exact"
) -> dict:
    """Pick the accepts entry this client can settle (exact EVM on Base)."""
    for entry in challenge.get("accepts") or []:
        if entry.get("scheme") == scheme and entry.get("network") == network:
            return entry
    raise ValueError(
        f"challenge offers no {scheme} requirement on {network}: "
        f"{[ (a.get('scheme'), a.get('network')) for a in challenge.get('accepts') or [] ]}"
    )


def build_authorization(
    from_address: str,
    requirement: dict,
    *,
    now: int | None = None,
    nonce: str | None = None,
) -> dict:
    """EIP-3009 authorization, string fields exactly as @x402/evm builds."""
    now = int(time.time()) if now is None else now
    return {
        "from": from_address,
        "to": requirement["payTo"],
        "value": str(requirement["amount"]),
        "validAfter": "0",
        "validBefore": str(now + int(requirement.get("maxTimeoutSeconds", 300))),
        "nonce": nonce or ("0x" + secrets.token_hex(32)),
    }


def sign_authorization(
    account: LocalAccount, requirement: dict, authorization: dict
) -> str:
    """EIP-712 sign the TransferWithAuthorization against the USDC domain
    the challenge advertises in accepts.extra (name/version)."""
    extra = requirement.get("extra") or {}
    if not extra.get("name") or not extra.get("version"):
        raise ValueError(
            "payment requirement is missing EIP-712 domain parameters "
            f"(extra.name/extra.version) for asset {requirement.get('asset')}"
        )
    network = requirement.get("network", BASE_NETWORK)
    if not network.startswith("eip155:"):
        raise ValueError(f"unsupported network for EVM signing: {network}")
    domain = {
        "name": extra["name"],
        "version": extra["version"],
        "chainId": int(network.split(":", 1)[1]),
        "verifyingContract": requirement["asset"],
    }
    message = {
        "from": authorization["from"],
        "to": authorization["to"],
        "value": int(authorization["value"]),
        "validAfter": int(authorization["validAfter"]),
        "validBefore": int(authorization["validBefore"]),
        "nonce": bytes.fromhex(authorization["nonce"].removeprefix("0x")),
    }
    signed = account.sign_typed_data(
        domain_data=domain,
        message_types=TRANSFER_WITH_AUTHORIZATION_TYPES,
        message_data=message,
    )
    return signed.signature.hex() if signed.signature.hex().startswith("0x") else "0x" + signed.signature.hex()


def build_payment_payload(
    challenge: dict, requirement: dict, authorization: dict, signature: str
) -> dict:
    """The x402 v2 PaymentPayload the retry carries."""
    return {
        "x402Version": challenge.get("x402Version", 2),
        "resource": challenge.get("resource"),
        "accepted": requirement,
        "payload": {"authorization": authorization, "signature": signature},
    }


def encode_payment_header(payload: dict) -> str:
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def decode_receipt(response: httpx.Response) -> dict | None:
    """Decode the base64 `payment-response` settlement receipt, if any."""
    raw = response.headers.get("payment-response")
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def load_account_from_env(env: dict | None = None) -> LocalAccount | None:
    """Build the signer from the operator's OWN environment.

    AGENT_PRIVATE_KEY: a 0x-prefixed EVM private key.
    AGENT_KEY_FILE:    path to a file whose contents are that key.
    Returns None when neither is set — callers then never pay.
    """
    env = os.environ if env is None else env
    key = (env.get("AGENT_PRIVATE_KEY") or "").strip()
    if not key:
        key_file = (env.get("AGENT_KEY_FILE") or "").strip()
        if key_file:
            key = Path(key_file).read_text(encoding="utf-8").strip()
    if not key:
        return None
    if not key.startswith("0x"):
        key = "0x" + key
    return Account.from_key(key)


class ListSmithClient:
    """Synchronous ListSmith client with automatic x402 payment."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        account: LocalAccount | None = None,
        *,
        http: httpx.Client | None = None,
        grade_max_amount_units: int = DEFAULT_GRADE_MAX_AMOUNT_UNITS,
        fix_max_amount_units: int = DEFAULT_FIX_MAX_AMOUNT_UNITS,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.account = account
        self.grade_max_amount_units = grade_max_amount_units
        self.fix_max_amount_units = fix_max_amount_units
        self._http = http or httpx.Client(timeout=timeout)
        self.last_receipt: dict | None = None

    def get_grade(self, resource_url: str, sample_output: Any = None) -> dict:
        """POST /v1/grade ($0.10), paying the 402 challenge when a signer
        is configured. Returns the grade JSON (see schemas.py)."""
        body: dict[str, Any] = {"resource_url": resource_url}
        if sample_output is not None:
            body["sample_output"] = sample_output
        return self._call_paid(GRADE_PATH, body, self.grade_max_amount_units)

    def get_fix(
        self,
        resource_url: str,
        sample_output: Any,
        name: str | None = None,
        category: str | None = None,
    ) -> dict:
        """POST /v1/fix ($0.50), paying the 402 challenge when a signer
        is configured. Returns the repair JSON (see schemas.py)."""
        body: dict[str, Any] = {
            "resource_url": resource_url,
            "sample_output": sample_output,
        }
        if name is not None:
            body["name"] = name
        if category is not None:
            body["category"] = category
        return self._call_paid(FIX_PATH, body, self.fix_max_amount_units)

    def _call_paid(self, path: str, body: dict, max_amount_units: int) -> dict:
        """The shared 402 -> sign -> retry flow for both routes."""
        url = self.base_url + path
        response = self._http.post(url, json=body)
        if response.status_code == 200:
            self.last_receipt = decode_receipt(response)
            return response.json()
        if response.status_code != 402:
            raise ListSmithError(response.status_code, _safe_json(response))

        challenge = parse_payment_required(response)
        if self.account is None:
            raise PaymentRequiredError(
                challenge,
                "payment required and no signer configured "
                "(set AGENT_PRIVATE_KEY or AGENT_KEY_FILE)",
            )
        requirement = select_requirement(challenge)
        amount = int(requirement["amount"])
        if amount > max_amount_units:
            raise PaymentRequiredError(
                challenge,
                f"price {amount} units exceeds spend guard "
                f"{max_amount_units} units; refusing to sign",
            )
        authorization = build_authorization(self.account.address, requirement)
        signature = sign_authorization(self.account, requirement, authorization)
        payload = build_payment_payload(challenge, requirement, authorization, signature)
        paid = self._http.post(
            url,
            json=body,
            headers={"PAYMENT-SIGNATURE": encode_payment_header(payload)},
        )
        if paid.status_code == 200:
            self.last_receipt = decode_receipt(paid)
            return paid.json()
        if paid.status_code == 402:
            raise PaymentRequiredError(
                parse_payment_required(paid),
                "paid retry still returned 402 (settlement rejected)",
            )
        raise ListSmithError(paid.status_code, _safe_json(paid))


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]
