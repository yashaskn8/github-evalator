"""Unit and adversarial tests for the T01 webhook ingress handler."""

import base64
import hashlib
import json
import logging
from unittest.mock import Mock

import pytest

from src.webhook.handler import lambda_handler, _recover_body_bytes, log_event


def _make_event(body_dict=None, body_str=None, headers=None, base64_encode=False, request_context=True):
    """Build a synthetic API Gateway HTTP API v2 proxy event."""
    if body_dict is not None:
        raw = json.dumps(body_dict)
    elif body_str is not None:
        raw = body_str
    else:
        raw = ""

    event = {
        "headers": headers if headers is not None else {},
        "isBase64Encoded": base64_encode,
    }
    if request_context:
        event["requestContext"] = {"requestId": "apigw-req-001"}

    if base64_encode:
        event["body"] = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    else:
        event["body"] = raw
    return event, raw


def _issues_opened_body():
    return {
        "action": "opened",
        "repository": {"id": 12345, "full_name": "octocat/hello-world"},
        "issue": {
            "number": 42,
            "title": "Add retry logic",
            "body": "SECRET PRIVATE ISSUE BODY CONTENT — must never appear in logs",
        },
        "sender": {"id": 67890, "login": "alice"},
        "installation": {"id": 111},
    }


def _pr_opened_body():
    return {
        "action": "opened",
        "repository": {"id": 54321, "full_name": "octocat/other-repo"},
        "pull_request": {
            "number": 88,
            "title": "Fix retry",
            "body": "PRIVATE PR DESCRIPTION — must never appear in logs",
        },
        "sender": {"id": 99999, "login": "bob"},
        "installation": {"id": 222},
    }


# ───────────────────────────────────────────────────────────────────
# Test 1: Parse issues event → 202, expected safe metadata extracted
# ───────────────────────────────────────────────────────────────────


class TestIssuesEvent:
    def test_returns_202_with_safe_metadata(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-uuid-001",
                "x-github-event": "issues",
            },
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "accepted"

        # Verify structured log contains expected fields
        log_lines = [r.message for r in caplog.records]
        assert len(log_lines) >= 1
        logged = json.loads(log_lines[-1])
        assert logged["githubDeliveryId"] == "delivery-uuid-001"
        assert logged["eventType"] == "issues"
        assert logged["action"] == "opened"
        assert logged["repositoryId"] == 12345
        assert logged["issueNumber"] == 42
        assert logged["senderId"] == 67890
        assert logged["installationId"] == 111
        assert logged["result"] == "accepted"
        assert "bodyHash" in logged
        assert "latencyMs" in logged


# ───────────────────────────────────────────────────────────────────
# Test 2: Parse pull_request event → 202, common safe metadata
# ───────────────────────────────────────────────────────────────────


class TestPullRequestEvent:
    def test_returns_202_with_common_metadata(self, caplog):
        body = _pr_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-uuid-002",
                "x-github-event": "pull_request",
            },
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"

        logged = json.loads(caplog.records[-1].message)
        assert logged["eventType"] == "pull_request"
        assert logged["action"] == "opened"
        assert logged["repositoryId"] == 54321
        assert logged["senderId"] == 99999
        assert logged["installationId"] == 222
        assert "issueNumber" not in logged or logged.get("issueNumber") is None


# ───────────────────────────────────────────────────────────────────
# Test 3: Headers work regardless of casing
# ───────────────────────────────────────────────────────────────────


class TestCaseInsensitiveHeaders:
    @pytest.mark.parametrize(
        "delivery_key,event_key",
        [
            ("X-GitHub-Delivery", "X-GitHub-Event"),
            ("x-github-delivery", "x-github-event"),
            ("X-GITHUB-DELIVERY", "X-GITHUB-EVENT"),
        ],
    )
    def test_headers_normalized(self, delivery_key, event_key, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={delivery_key: "delivery-case-test", event_key: "issues"},
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"
        logged = json.loads(caplog.records[-1].message)
        assert logged["githubDeliveryId"] == "delivery-case-test"
        assert logged["eventType"] == "issues"


# ───────────────────────────────────────────────────────────────────
# Test 4: Base64-encoded API Gateway body → correctly recovered
# ───────────────────────────────────────────────────────────────────


class TestBase64Body:
    def test_base64_decoded_and_hash_matches(self, caplog):
        body = _issues_opened_body()
        event, raw_str = _make_event(
            body_dict=body,
            headers={"x-github-event": "issues"},
            base64_encode=True,
        )

        expected_hash = "sha256:" + hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"
        logged = json.loads(caplog.records[-1].message)
        assert logged["bodyHash"] == expected_hash


# ───────────────────────────────────────────────────────────────────
# Test 5: Malformed JSON → 400, body not logged
# ───────────────────────────────────────────────────────────────────


class TestMalformedJson:
    def test_returns_400_without_logging_body(self, caplog):
        event, raw_str = _make_event(
            body_str="THIS IS NOT JSON {{{",
            headers={"x-github-event": "issues"},
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "invalid_payload"

        # Verify error is logged but the malformed body content is not
        log_text = " ".join(r.message for r in caplog.records)
        assert "invalid_json" in log_text
        assert "THIS IS NOT JSON" not in log_text
        # bodyHash should still be present
        logged = json.loads(caplog.records[-1].message)
        assert "bodyHash" in logged


# ───────────────────────────────────────────────────────────────────
# Test 6: Private fields are never present in telemetry
# ───────────────────────────────────────────────────────────────────


class TestNoPrivateData:
    def test_private_content_excluded_from_logs(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-uuid-private",
                "x-github-event": "issues",
                "x-hub-signature-256": "sha256=fakesignature",
                "authorization": "token secret-github-token",
            },
        )

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        log_text = " ".join(r.message for r in caplog.records)

        # Private content that must NEVER appear
        assert "SECRET PRIVATE ISSUE BODY CONTENT" not in log_text
        assert "fakesignature" not in log_text
        assert "secret-github-token" not in log_text
        assert "Add retry logic" not in log_text  # issue title


# ───────────────────────────────────────────────────────────────────
# Test 7: bodyHash corresponds to exact raw body bytes
# ───────────────────────────────────────────────────────────────────


class TestBodyHashIntegrity:
    def test_hash_matches_raw_bytes(self, caplog):
        body = _issues_opened_body()
        event, raw_str = _make_event(
            body_dict=body,
            headers={"x-github-event": "issues"},
        )

        raw_bytes = raw_str.encode("utf-8")
        expected_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        logged = json.loads(caplog.records[-1].message)
        assert logged["bodyHash"] == expected_hash

    def test_hash_does_not_match_reserialized_bytes(self, caplog):
        """Prove the hash is computed from raw bytes, not from re-serialized JSON."""
        # Use a body with specific whitespace that would change on re-serialization
        raw_with_spaces = '{"action":  "opened",  "repository": {"id": 1}}'
        event, _ = _make_event(
            body_str=raw_with_spaces,
            headers={"x-github-event": "issues"},
        )

        raw_hash = "sha256:" + hashlib.sha256(raw_with_spaces.encode("utf-8")).hexdigest()
        reserialized = json.dumps(json.loads(raw_with_spaces))
        reserialized_hash = "sha256:" + hashlib.sha256(reserialized.encode("utf-8")).hexdigest()

        # The two hashes must differ (proving we don't reserialize)
        assert raw_hash != reserialized_hash

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        logged = json.loads(caplog.records[-1].message)
        # Handler must use the raw hash, not the reserialized hash
        assert logged["bodyHash"] == raw_hash
        assert logged["bodyHash"] != reserialized_hash


# ───────────────────────────────────────────────────────────────────
# Test 8: Missing delivery header → no crash, correlationId present
# ───────────────────────────────────────────────────────────────────


class TestMissingDeliveryHeader:
    def test_no_crash_and_correlation_id_present(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issues"},
            # No x-github-delivery header
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"

        logged = json.loads(caplog.records[-1].message)
        # correlationId should fall back to the API Gateway request ID
        assert logged["correlationId"] == "apigw-req-001"
        # githubDeliveryId should not appear since it was absent
        assert "githubDeliveryId" not in logged or logged.get("githubDeliveryId") is None


# ───────────────────────────────────────────────────────────────────
# Test 9: Hostile Red-Team Gates (Valid-JSON Non-Object, Empty, Base64, Headers)
# ───────────────────────────────────────────────────────────────────


class TestHostilePayloadShapes:
    @pytest.mark.parametrize(
        "non_object_json,description",
        [
            ("[]", "JSON array"),
            ('"hello"', "JSON string"),
            ("123", "JSON integer"),
            ("null", "JSON null"),
            ("true", "JSON boolean"),
            ("[1, 2, 3]", "JSON integer array"),
            ('["secret", "data"]', "JSON array with strings"),
        ],
    )
    def test_valid_json_non_object_returns_400_without_crash(self, non_object_json, description, caplog):
        """JSON values that are valid JSON but not objects must never 5xx or call .get()."""
        event, _ = _make_event(
            body_str=non_object_json,
            headers={"x-github-delivery": "delivery-non-obj", "x-github-event": "issues"},
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "invalid_payload"

        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "invalid_payload"
        assert logged["errorClass"] == "invalid_json_shape"
        # Raw input must not be logged
        assert non_object_json not in caplog.records[-1].message or non_object_json in ('null', '123', 'true')
        assert "secret" not in caplog.records[-1].message

    def test_empty_string_body_returns_400(self, caplog):
        """An empty POST body must return 400 empty_body, never 202."""
        event, _ = _make_event(
            body_str="",
            headers={"x-github-delivery": "delivery-empty", "x-github-event": "issues"},
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "invalid_payload"

        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "invalid_payload"
        assert logged["errorClass"] == "empty_body"

    def test_body_is_none_returns_400(self, caplog):
        """Missing or None body must return 400 empty_body."""
        event = {
            "body": None,
            "headers": {"x-github-delivery": "delivery-none", "x-github-event": "issues"},
            "requestContext": {"requestId": "req-none"},
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "invalid_payload"

        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "empty_body"

    def test_invalid_base64_body_returns_400_without_unhandled_exception(self, caplog):
        """Malformed Base64 input must be cleanly caught and return 400 invalid_base64."""
        event = {
            "body": "This-is-NOT-valid-base64!@#$%",
            "isBase64Encoded": True,
            "headers": {"x-github-delivery": "delivery-b64-bad", "x-github-event": "issues"},
            "requestContext": {"requestId": "req-b64-bad"},
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert result["headers"]["Content-Type"] == "application/json"
        assert json.loads(result["body"])["status"] == "invalid_payload"

        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "invalid_payload"
        assert logged["errorClass"] == "invalid_base64"
        # Malformed body string must not be logged
        assert "This-is-NOT" not in caplog.records[-1].message

    def test_headers_none_does_not_crash(self, caplog):
        """headers=None in event must not raise an exception."""
        body = _issues_opened_body()
        event = {
            "headers": None,
            "body": json.dumps(body),
            "isBase64Encoded": False,
            "requestContext": {"requestId": "req-h-none"},
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert result["headers"]["Content-Type"] == "application/json"
        logged = json.loads(caplog.records[-1].message)
        assert logged["correlationId"] == "req-h-none"

    def test_missing_request_context_correlation_fallback(self, caplog):
        """Missing requestContext should fall back to Lambda context.aws_request_id or 'unknown'."""
        body = _issues_opened_body()
        # Event with no delivery header and no requestContext
        event = {
            "body": json.dumps(body),
            "headers": {"x-github-event": "issues"},
        }

        # Case A: context has aws_request_id
        mock_context = Mock()
        mock_context.aws_request_id = "lambda-req-id-777"

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, mock_context)

        assert result["statusCode"] == 202
        logged = json.loads(caplog.records[-1].message)
        assert logged["correlationId"] == "lambda-req-id-777"

        # Case B: context is None -> falls back to 'unknown'
        caplog.clear()
        with caplog.at_level(logging.INFO):
            result_none = lambda_handler(event, None)

        assert result_none["statusCode"] == 202
        logged_none = json.loads(caplog.records[-1].message)
        assert logged_none["correlationId"] == "unknown"

    def test_error_telemetry_never_contains_private_data(self, caplog):
        """Adversarial error cases must never leak untrusted data into telemetry."""
        malicious_input = '{"secret_token": "SUPER_SECRET_VALUE", "payload": [1, 2, 3]}'
        # In this case, invalid_json_shape is triggered because payload is not root, wait:
        # let's test a non-object containing secret token:
        adversarial_json = '["SUPER_SECRET_TOKEN_IN_ARRAY", 999]'
        event, _ = _make_event(
            body_str=adversarial_json,
            headers={"x-github-delivery": "delivery-redteam"},
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        for record in caplog.records:
            assert "SUPER_SECRET_TOKEN_IN_ARRAY" not in record.message
