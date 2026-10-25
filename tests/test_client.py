"""Client payment-flow tests. ALL payments are mocked — the HTTP layer is
an httpx MockTransport and the signer is a throwaway account generated
at test time. No key material lives in this repo, and no test touches
the network or spends anything.

The 402 fixtures (fixtures/payment_required_grade.json and
payment_required_fix.json) are real: captured from unpaid probes of
production on 2026-10-23. The grade/fix fixtures are minimal instances
of the service's own published output schemas.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from list_smith_mcp.client import (
    TRANSFER_WITH_AUTHORIZATION_TYPES,
    ListSmithClient,
    ListSmithError,
    PaymentRequiredError,
    build_authorization,
    load_account_from_env,
    parse_payment_required,
    select_requirement,
)
from list_smith_mcp.server import run_fix, run_grade

FIXTURES = Path(__file__).parent / "fixtures"
GRADE_CHALLENGE = json.loads((FIXTURES / "payment_required_grade.json").read_text())
FIX_CHALLENGE = json.loads((FIXTURES / "payment_required_fix.json").read_text())
GRADE = json.loads((FIXTURES / "grade_example.json").read_text())
FIX = json.loads((FIXTURES / "fix_example.json").read_text())
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PAY_TO = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
TARGET = "https://token-verdict-production.up.railway.app/v1/verdict"
SAMPLE = {"verdict": "CAUTION", "score": 35.0}


def challenge_response(challenge: dict, path: str) -> httpx.Response:
    header = base64.b64encode(json.dumps(challenge).encode()).decode()
    return httpx.Response(
        402, headers={"payment-required": header}, json={}, request=_req(path)
    )


def _req(path: str) -> httpx.Request:
    return httpx.Request("POST", f"https://example.test{path}")


class Recorder:
    """MockTransport handler: replays queued responses, records requests."""

    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def mock_client(recorder: Recorder, **kwargs) -> ListSmithClient:
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    return ListSmithClient(base_url="https://example.test", http=http, **kwargs)


def assert_signature_recovers(challenge: dict, retry: httpx.Request, account) -> dict:
    """Decode the retry's PAYMENT-SIGNATURE and prove the EIP-712
    signature recovers to the signer — the core of the paid flow."""
    header = retry.headers.get("PAYMENT-SIGNATURE")
    assert header, "paid retry must carry the PAYMENT-SIGNATURE header"
    payload = json.loads(base64.b64decode(header))
    req = select_requirement(challenge)
    domain = {
        "name": req["extra"]["name"],
        "version": req["extra"]["version"],
        "chainId": 8453,
        "verifyingContract": req["asset"],
    }
    auth = payload["payload"]["authorization"]
    message = {
        "from": auth["from"],
        "to": auth["to"],
        "value": int(auth["value"]),
        "validAfter": int(auth["validAfter"]),
        "validBefore": int(auth["validBefore"]),
        "nonce": bytes.fromhex(auth["nonce"].removeprefix("0x")),
    }
    encoded = encode_typed_data(domain, TRANSFER_WITH_AUTHORIZATION_TYPES, message)
    recovered = Account.recover_message(encoded, signature=payload["payload"]["signature"])
    assert recovered == account.address
    return payload


def receipt_response(body: dict, account, path: str) -> httpx.Response:
    receipt = base64.b64encode(
        json.dumps(
            {
                "success": True,
                "transaction": "0x" + "ab" * 32,
                "network": "eip155:8453",
                "payer": account.address,
            }
        ).encode()
    ).decode()
    return httpx.Response(
        200, headers={"payment-response": receipt}, json=body, request=_req(path)
    )


# --- challenge parsing -------------------------------------------------------


def test_parse_real_production_challenges():
    for challenge, amount in ((GRADE_CHALLENGE, "100000"), (FIX_CHALLENGE, "500000")):
        parsed = parse_payment_required(
            challenge_response(challenge, "/v1/grade")
        )
        assert parsed["x402Version"] == 2
        req = select_requirement(parsed)
        assert req["scheme"] == "exact"
        assert req["network"] == "eip155:8453"
        assert req["asset"] == USDC_ADDRESS
        assert req["amount"] == amount
        assert req["payTo"] == PAY_TO
        assert req["extra"] == {"name": "USD Coin", "version": "2"}


def test_select_requirement_rejects_foreign_network():
    challenge = {"accepts": [{"scheme": "exact", "network": "eip155:1"}]}
    with pytest.raises(ValueError):
        select_requirement(challenge)


# --- no signer: structured error, never a payment ----------------------------


def test_no_signer_grade_returns_structured_error_and_never_retries():
    recorder = Recorder([challenge_response(GRADE_CHALLENGE, "/v1/grade")])
    client = mock_client(recorder, account=None)
    result = run_grade(TARGET, client=client)
    assert result["error"] == "payment_required"
    assert result["accepts"][0]["amount"] == "100000"
    assert result["accepts"][0]["payTo"] == PAY_TO
    assert len(recorder.requests) == 1  # no paid retry was attempted


def test_no_signer_fix_returns_structured_error_and_never_retries():
    recorder = Recorder([challenge_response(FIX_CHALLENGE, "/v1/fix")])
    client = mock_client(recorder, account=None)
    result = run_fix(TARGET, SAMPLE, client=client)
    assert result["error"] == "payment_required"
    assert result["accepts"][0]["amount"] == "500000"
    assert len(recorder.requests) == 1


def test_no_signer_client_raises_with_challenge_attached():
    recorder = Recorder([challenge_response(GRADE_CHALLENGE, "/v1/grade")])
    client = mock_client(recorder, account=None)
    with pytest.raises(PaymentRequiredError) as excinfo:
        client.get_grade(TARGET)
    assert excinfo.value.challenge["accepts"][0]["asset"] == USDC_ADDRESS


# --- signer flow: sign EIP-3009, retry with PAYMENT-SIGNATURE ---------------


def test_paid_grade_flow_signs_eip3009_and_retries():
    account = Account.create()  # throwaway, generated at test time
    ok = receipt_response(GRADE, account, "/v1/grade")
    recorder = Recorder([challenge_response(GRADE_CHALLENGE, "/v1/grade"), ok])
    client = mock_client(recorder, account=account)

    result = client.get_grade(TARGET)
    assert result["score"] == 88
    assert client.last_receipt["transaction"] == "0x" + "ab" * 32
    assert len(recorder.requests) == 2

    # Unpaid request body carries only resource_url (no sample given).
    first_body = json.loads(recorder.requests[0].content)
    assert first_body == {"resource_url": TARGET}

    payload = assert_signature_recovers(GRADE_CHALLENGE, recorder.requests[1], account)
    assert payload["x402Version"] == 2
    assert payload["accepted"]["amount"] == "100000"
    assert payload["accepted"]["asset"] == USDC_ADDRESS
    auth = payload["payload"]["authorization"]
    assert auth["from"] == account.address
    assert auth["to"] == PAY_TO
    assert auth["value"] == "100000"
    assert auth["validAfter"] == "0"
    assert int(auth["validBefore"]) > int(auth["validAfter"])


def test_paid_fix_flow_signs_eip3009_and_retries():
    account = Account.create()
    ok = receipt_response(FIX, account, "/v1/fix")
    recorder = Recorder([challenge_response(FIX_CHALLENGE, "/v1/fix"), ok])
    client = mock_client(recorder, account=account)

    result = client.get_fix(TARGET, SAMPLE, name="Token Verdict", category="token-risk")
    assert result["ok"] is True
    assert result["score_before"] == 42
    assert len(recorder.requests) == 2

    # The request body carries the full FixRequest contract.
    first_body = json.loads(recorder.requests[0].content)
    assert first_body == {
        "resource_url": TARGET,
        "sample_output": SAMPLE,
        "name": "Token Verdict",
        "category": "token-risk",
    }

    payload = assert_signature_recovers(FIX_CHALLENGE, recorder.requests[1], account)
    assert payload["accepted"]["amount"] == "500000"
    auth = payload["payload"]["authorization"]
    assert auth["value"] == "500000"
    assert auth["to"] == PAY_TO


def test_grade_spend_guard_refuses_above_grade_cap():
    expensive = json.loads(json.dumps(GRADE_CHALLENGE))
    expensive["accepts"][0]["amount"] = "500000"  # fix price > grade guard
    recorder = Recorder([challenge_response(expensive, "/v1/grade")])
    client = mock_client(recorder, account=Account.create())
    with pytest.raises(PaymentRequiredError) as excinfo:
        client.get_grade(TARGET)
    assert "spend guard" in excinfo.value.reason
    assert len(recorder.requests) == 1  # refused before any retry


def test_fix_spend_guard_refuses_above_fix_cap():
    expensive = json.loads(json.dumps(FIX_CHALLENGE))
    expensive["accepts"][0]["amount"] = "600000"  # $0.60 > $0.50 guard
    recorder = Recorder([challenge_response(expensive, "/v1/fix")])
    client = mock_client(recorder, account=Account.create())
    with pytest.raises(PaymentRequiredError) as excinfo:
        client.get_fix(TARGET, SAMPLE)
    assert "spend guard" in excinfo.value.reason
    assert len(recorder.requests) == 1


def test_paid_retry_still_402_raises():
    recorder = Recorder(
        [
            challenge_response(GRADE_CHALLENGE, "/v1/grade"),
            challenge_response(GRADE_CHALLENGE, "/v1/grade"),
        ]
    )
    client = mock_client(recorder, account=Account.create())
    with pytest.raises(PaymentRequiredError) as excinfo:
        client.get_grade(TARGET)
    assert "still returned 402" in excinfo.value.reason
    assert len(recorder.requests) == 2


# --- non-payment failures -----------------------------------------------------


def test_upstream_error_maps_to_structured_error():
    not_found = httpx.Response(
        404, json={"error": "not found"}, request=_req("/v1/grade")
    )
    recorder = Recorder([not_found])
    client = mock_client(recorder, account=None)
    result = run_grade(TARGET, client=client)
    assert result == {
        "error": "upstream_error",
        "status": 404,
        "detail": {"error": "not found"},
    }
    with pytest.raises(ListSmithError):
        mock_client(
            Recorder([httpx.Response(404, json={}, request=_req("/v1/fix"))])
        ).get_fix(TARGET, SAMPLE)


# --- signer loading ------------------------------------------------------------


def test_load_account_from_env_variants(tmp_path):
    assert load_account_from_env({}) is None
    account = Account.create()
    key = account.key.hex()
    loaded = load_account_from_env({"AGENT_PRIVATE_KEY": key})
    assert loaded is not None and loaded.address == account.address
    key_file = tmp_path / "agent.key"
    key_file.write_text(key + "\n")
    loaded = load_account_from_env({"AGENT_KEY_FILE": str(key_file)})
    assert loaded is not None and loaded.address == account.address


def test_build_authorization_window():
    req = select_requirement(FIX_CHALLENGE)
    auth = build_authorization("0x" + "11" * 20, req, now=1_000_000)
    assert auth["validAfter"] == "0"
    assert auth["validBefore"] == str(1_000_000 + 300)  # maxTimeoutSeconds
    assert auth["value"] == "500000"
    assert auth["to"] == PAY_TO
    assert auth["nonce"].startswith("0x") and len(auth["nonce"]) == 66
