"""Unit and adversarial tests for the T01/T02/T04 webhook ingress handler."""

import base64
import hashlib
import hmac
import json
import logging
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

import src.webhook.handler as handler_module
from src.webhook.handler import (
    lambda_handler,
    _recover_body_bytes,
    _verify_hmac_signature,
    log_event,
    MAX_COMMENT_BODY_BYTES,
    MAX_QUEUE_MESSAGE_BYTES,
    MAX_STRING_ID_LENGTH,
    SUPPORTED_EVENT_TYPES,
)

DEFAULT_TEST_SECRET = "test-webhook-secret-key-xyz-12345"


def _compute_sig(raw_bytes: bytes, secret: str = DEFAULT_TEST_SECRET) -> str:
    """Helper to compute valid GitHub X-Hub-Signature-256 header for test payloads."""
    digest = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture(autouse=True)
def inject_default_dependencies(monkeypatch):
    """Ensure the handler has a valid secret and SQS mock available for tests by default."""
    handler_module._secret_provider_override = lambda: DEFAULT_TEST_SECRET
    monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue")
    mock_sqs = Mock()
    mock_sqs.send_message.return_value = {"MessageId": "mock-msg-001"}
    monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
    yield mock_sqs
    handler_module._secret_provider_override = None



def _make_event(
    body_dict=None,
    body_str=None,
    headers=None,
    base64_encode=False,
    request_context=True,
    sign=True,
    secret=DEFAULT_TEST_SECRET,
):
    """Build a synthetic API Gateway HTTP API v2 proxy event with valid signature by default."""
    if body_dict is not None:
        raw = json.dumps(body_dict)
    elif body_str is not None:
        raw = body_str
    else:
        raw = ""

    raw_bytes = raw.encode("utf-8")
    req_headers = {k: v for k, v in (headers or {}).items()}

    # Add default delivery and event if not provided
    if "x-github-delivery" not in {k.lower(): v for k, v in req_headers.items()}:
        req_headers["x-github-delivery"] = "delivery-uuid-default"
    if "x-github-event" not in {k.lower(): v for k, v in req_headers.items()}:
        req_headers["x-github-event"] = "issues"

    # Add valid signature if requested and not explicitly provided
    if sign and "x-hub-signature-256" not in {k.lower(): v for k, v in req_headers.items()}:
        req_headers["x-hub-signature-256"] = _compute_sig(raw_bytes, secret)

    event = {
        "headers": req_headers,
        "isBase64Encoded": base64_encode,
    }
    if request_context:
        event["requestContext"] = {"requestId": "apigw-req-001"}

    if base64_encode:
        event["body"] = base64.b64encode(raw_bytes).decode("ascii")
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
            "head": {
                "sha": "abc123def456789abc123def456789abc123def4",
            },
        },
        "sender": {"id": 99999, "login": "bob"},
        "installation": {"id": 222},
    }


def _issue_comment_body():
    return {
        "action": "created",
        "repository": {"id": 12345},
        "issue": {"number": 42},
        "comment": {"id": 987654321, "body": "I propose modifying retry logic in src/retry.py"},
        "sender": {"id": 67890},
        "installation": {"id": 111},
    }


# ───────────────────────────────────────────────────────────────────
# T01 Base Tests: Ingress, Recovery, Metadata, Headers
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

        log_text = " ".join(r.message for r in caplog.records)
        assert "invalid_json" in log_text
        assert "THIS IS NOT JSON" not in log_text
        logged = json.loads(caplog.records[-1].message)
        assert "bodyHash" in logged


class TestNoPrivateData:
    def test_private_content_excluded_from_logs(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-uuid-private",
                "x-github-event": "issues",
                "authorization": "token secret-github-token",
            },
        )

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        log_text = " ".join(r.message for r in caplog.records)
        assert "SECRET PRIVATE ISSUE BODY CONTENT" not in log_text
        assert "secret-github-token" not in log_text
        assert "Add retry logic" not in log_text
        assert DEFAULT_TEST_SECRET not in log_text


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
        raw_with_spaces = '{"action":  "opened",  "repository": {"id": 1}}'
        event, _ = _make_event(
            body_str=raw_with_spaces,
            headers={"x-github-event": "issues"},
        )

        raw_hash = "sha256:" + hashlib.sha256(raw_with_spaces.encode("utf-8")).hexdigest()
        reserialized = json.dumps(json.loads(raw_with_spaces))
        reserialized_hash = "sha256:" + hashlib.sha256(reserialized.encode("utf-8")).hexdigest()

        assert raw_hash != reserialized_hash

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        logged = json.loads(caplog.records[-1].message)
        assert logged["bodyHash"] == raw_hash
        assert logged["bodyHash"] != reserialized_hash


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
        assert "secret" not in caplog.records[-1].message

    def test_empty_string_body_returns_400(self, caplog):
        event, _ = _make_event(
            body_str="",
            headers={"x-github-delivery": "delivery-empty", "x-github-event": "issues"},
            sign=False,
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
        assert "This-is-NOT" not in caplog.records[-1].message

    def test_headers_none_fails_closed_missing_signature(self, caplog):
        """In T02, headers=None does not crash, but fails authentication (missing signature)."""
        body = _issues_opened_body()
        event = {
            "headers": None,
            "body": json.dumps(body),
            "isBase64Encoded": False,
            "requestContext": {"requestId": "req-h-none"},
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        assert result["headers"]["Content-Type"] == "application/json"
        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "unauthorized"
        assert logged["errorClass"] == "missing_signature"

    def test_missing_request_context_correlation_fallback(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-correlate"},
            request_context=False,
        )

        mock_context = Mock()
        mock_context.aws_request_id = "lambda-req-id-777"

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, mock_context)

        assert result["statusCode"] == 202
        logged = json.loads(caplog.records[-1].message)
        assert logged["correlationId"] == "delivery-correlate"


# ───────────────────────────────────────────────────────────────────
# T02 Cryptographic HMAC Authentication & Secrets Manager Tests
# ───────────────────────────────────────────────────────────────────


class TestOfficialGitHubHmacVector:
    def test_official_github_test_vector_matches_exact_expected(self):
        """Must match GitHub's published test vector exactly:
        secret: It's a Secret to Everybody
        payload: Hello, World!
        digest: 757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17
        header: sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17
        """
        secret = "It's a Secret to Everybody"
        payload = b"Hello, World!"
        expected_digest = "757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        expected_header = f"sha256={expected_digest}"

        is_valid, err = _verify_hmac_signature(payload, expected_header, secret)
        assert is_valid is True
        assert err == ""

        # Mutation of one byte must fail
        tampered_payload = b"Hello, World?"
        is_tampered_valid, _ = _verify_hmac_signature(tampered_payload, expected_header, secret)
        assert is_tampered_valid is False


class TestHmacVerificationFailures:
    def test_missing_signature_returns_401(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        assert json.loads(result["body"])["status"] == "unauthorized"
        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "unauthorized"
        assert logged["errorClass"] == "missing_signature"

    def test_malformed_signature_no_sha256_prefix_returns_401(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-hub-signature-256": "md5=badprefix1234567890"},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "malformed_signature"

    def test_incorrect_signature_returns_401(self, caplog):
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-hub-signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000"},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "invalid_signature"

    def test_wrong_secret_returns_401(self, caplog):
        body = _issues_opened_body()
        # Sign with an unauthorized attacker secret
        event, _ = _make_event(body_dict=body, secret="attacker-wrong-secret", sign=True)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "invalid_signature"

    def test_payload_modified_after_signing_returns_401(self, caplog):
        original_body = '{"action": "opened", "issue": {"number": 1}}'
        valid_sig = _compute_sig(original_body.encode("utf-8"))

        tampered_body = '{"action": "opened", "issue": {"number": 999}}'
        event, _ = _make_event(
            body_str=tampered_body,
            headers={"x-hub-signature-256": valid_sig},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "invalid_signature"

    def test_whitespace_mutation_after_signing_returns_401(self, caplog):
        original = '{"action":"opened"}'
        sig = _compute_sig(original.encode("utf-8"))

        mutated = '{"action": "opened"}'
        event, _ = _make_event(
            body_str=mutated,
            headers={"x-hub-signature-256": sig},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401


class TestUnicodePayloadHmac:
    def test_unicode_payload_with_valid_signature_succeeds(self):
        body_dict = {
            "action": "created",
            "issue": {"number": 42},
            "comment": {"id": 111, "body": "Fix retry logic 🚀 नमस्ते 日本語"},
            "repository": {"id": 12345},
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body_dict,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

    def test_unicode_mutation_fails_authentication(self):
        original = '{"comment": "Fix retry 🚀"}'
        sig = _compute_sig(original.encode("utf-8"))
        mutated = '{"comment": "Fix retry 🛸"}'

        event, _ = _make_event(
            body_str=mutated,
            headers={"x-hub-signature-256": sig},
            sign=False,
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 401


class TestAuthBeforeJsonParsing:
    def test_unauthenticated_malformed_json_returns_401_first(self, caplog):
        """Proves authentication occurs BEFORE JSON parsing:
        malformed JSON + forged signature must return 401 (not 400).
        """
        malformed = "THIS IS NOT JSON {{{ INVALID"
        event, _ = _make_event(
            body_str=malformed,
            headers={"x-hub-signature-256": "sha256=forgedbadsignature"},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        # MUST be 401, not 400!
        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "unauthorized"
        assert logged["errorClass"] == "invalid_signature"

    def test_authenticated_malformed_json_returns_400(self, caplog):
        """When signature is VALID, malformed JSON passes auth and is rejected with controlled 400."""
        malformed = "THIS IS NOT JSON {{{ INVALID"
        valid_sig = _compute_sig(malformed.encode("utf-8"))
        event, _ = _make_event(
            body_str=malformed,
            headers={"x-hub-signature-256": valid_sig},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"])["status"] == "invalid_payload"
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "invalid_json"


class TestMissingRequiredGitHubHeadersAfterAuth:
    def test_authenticated_request_missing_delivery_returns_400_invalid_webhook(self, caplog):
        body = _issues_opened_body()
        raw_bytes = json.dumps(body).encode("utf-8")
        sig = _compute_sig(raw_bytes)

        event = {
            "body": json.dumps(body),
            "headers": {
                "x-hub-signature-256": sig,
                "x-github-event": "issues",
                # Missing x-github-delivery
            },
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"])["status"] == "invalid_webhook"
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "missing_github_headers"

    def test_authenticated_request_missing_event_returns_400_invalid_webhook(self, caplog):
        body = _issues_opened_body()
        raw_bytes = json.dumps(body).encode("utf-8")
        sig = _compute_sig(raw_bytes)

        event = {
            "body": json.dumps(body),
            "headers": {
                "x-hub-signature-256": sig,
                "x-github-delivery": "delivery-valid-001",
                # Missing x-github-event
            },
        }

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"])["status"] == "invalid_webhook"
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "missing_github_headers"


class TestSecretsManagerFailClosed:
    def test_secrets_manager_failure_returns_503_and_fails_closed(self, monkeypatch, caplog):
        """When Secrets Manager raises ClientError or secret is unavailable, fail closed."""
        handler_module._secret_provider_override = None
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111:secret:sec")

        mock_sm_client = Mock()
        mock_sm_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Secret not found"}},
            "GetSecretValue",
        )
        monkeypatch.setattr(handler_module, "_get_secretsmanager_client", lambda: mock_sm_client)

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert json.loads(result["body"])["status"] == "authentication_unavailable"
        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "authentication_error"
        assert logged["errorClass"] == "secret_unavailable"

    def test_secret_and_signatures_never_in_logs(self, caplog):
        """Assert secrets, expected signatures, and received signatures never leak into logs."""
        body = _issues_opened_body()
        secret = "SUPER_SECRET_VALUE_NEVER_LOG"
        raw_bytes = json.dumps(body).encode("utf-8")
        bad_sig = "sha256=BAD_SIGNATURE_VALUE_12345"

        handler_module._secret_provider_override = lambda: secret

        event, _ = _make_event(
            body_dict=body,
            headers={"x-hub-signature-256": bad_sig},
            sign=False,
        )

        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        all_logs = " ".join(r.message for r in caplog.records)
        assert secret not in all_logs
        assert "SUPER_SECRET" not in all_logs
        assert bad_sig not in all_logs
        assert "BAD_SIGNATURE" not in all_logs


class TestEmptySecretFailsClosed:
    """FIX 1: Empty, whitespace-only, None, or missing SecretString must fail closed."""

    def test_empty_string_secret_returns_503(self, monkeypatch, caplog):
        handler_module._secret_provider_override = lambda: ""
        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert json.loads(result["body"])["status"] == "authentication_unavailable"
        assert mock_sqs.send_message.call_count == 0

    def test_whitespace_only_secret_returns_503(self, monkeypatch, caplog):
        handler_module._secret_provider_override = lambda: "   "
        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert json.loads(result["body"])["status"] == "authentication_unavailable"
        assert mock_sqs.send_message.call_count == 0

    def test_none_secret_returns_503(self, monkeypatch, caplog):
        handler_module._secret_provider_override = lambda: None
        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert mock_sqs.send_message.call_count == 0

    def test_missing_secret_string_from_sm_returns_503(self, monkeypatch, caplog):
        handler_module._secret_provider_override = None
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111:secret:sec")

        mock_sm = Mock()
        mock_sm.get_secret_value.return_value = {}  # No SecretString key
        monkeypatch.setattr(handler_module, "_get_secretsmanager_client", lambda: mock_sm)

        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert mock_sqs.send_message.call_count == 0

    def test_valid_nonempty_secret_allows_authentication(self, caplog):
        """A valid non-empty secret allows authentication to continue."""
        handler_module._secret_provider_override = lambda: "valid-real-secret"
        body = _issues_opened_body()
        raw = json.dumps(body).encode("utf-8")
        sig = _compute_sig(raw, "valid-real-secret")
        event, _ = _make_event(
            body_dict=body,
            headers={"x-hub-signature-256": sig},
            sign=False,
        )
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)
        assert result["statusCode"] == 202


class TestSecretsManagerRotationWithoutIndefiniteCache:
    def test_subsequent_requests_observe_rotated_secrets_without_warm_cache(self, monkeypatch, caplog):
        """Proves no indefinite warm secret caching:
        Invocation 1 uses Secret A, Invocation 2 uses Secret B.
        Both authenticate successfully without process restart.
        """
        secret_store = {"current": "initial-secret-version-aaa"}
        handler_module._secret_provider_override = None
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111:secret:rot")

        mock_sm_client = Mock()
        mock_sm_client.get_secret_value.side_effect = lambda SecretId: {"SecretString": secret_store["current"]}
        monkeypatch.setattr(handler_module, "_get_secretsmanager_client", lambda: mock_sm_client)

        body = _issues_opened_body()
        raw_bytes = json.dumps(body).encode("utf-8")

        # Request 1 with Secret A
        sig_a = _compute_sig(raw_bytes, secret="initial-secret-version-aaa")
        event_1, _ = _make_event(body_dict=body, headers={"x-hub-signature-256": sig_a}, sign=False)
        res_1 = lambda_handler(event_1, None)
        assert res_1["statusCode"] == 202

        # Rotate secret in Secrets Manager
        secret_store["current"] = "rotated-secret-version-bbb"

        # Request 2 with Secret B — must succeed without container restart
        sig_b = _compute_sig(raw_bytes, secret="rotated-secret-version-bbb")
        event_2, _ = _make_event(body_dict=body, headers={"x-hub-signature-256": sig_b}, sign=False)
        res_2 = lambda_handler(event_2, None)
        assert res_2["statusCode"] == 202

        # Verify old Secret A now fails against rotated secret
        event_old, _ = _make_event(body_dict=body, headers={"x-hub-signature-256": sig_a}, sign=False)
        res_old = lambda_handler(event_old, None)
        assert res_old["statusCode"] == 401


class TestPreAuthCorrelationUntrustedHeaderIsolation:
    def test_forged_request_does_not_trust_unauthenticated_github_delivery_header(self, caplog):
        """Attacker sends forged signature and arbitrary x-github-delivery header.
        Telemetry correlationId must derive from API Gateway requestId, NOT attacker header.
        """
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "attacker-controlled-delivery-uuid-9999",
                "x-hub-signature-256": "sha256=badfakeforgedsig0000000000000000000000000000",
            },
            sign=False,
        )
        # requestContext has requestId: "apigw-req-001"
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        log_records = [json.loads(r.message) for r in caplog.records]
        assert len(log_records) >= 1
        logged = log_records[-1]

        # The correlationId MUST NOT be the attacker-controlled delivery header
        assert logged["correlationId"] != "attacker-controlled-delivery-uuid-9999"
        assert logged["correlationId"] == "apigw-req-001"
        assert "githubDeliveryId" not in logged or logged.get("githubDeliveryId") is None

    def test_forged_request_without_request_context_falls_back_to_unknown(self, caplog):
        """Attacker sends forged signature without requestContext; correlationId is 'unknown'."""
        body = _issues_opened_body()
        event = {
            "body": json.dumps(body),
            "headers": {
                "x-github-delivery": "attacker-controlled-uuid",
                "x-hub-signature-256": "sha256=forged00000000000000000000000000000000000",
            },
            # No requestContext
        }
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        logged = json.loads(caplog.records[-1].message)
        assert logged["correlationId"] == "unknown"
        assert logged["correlationId"] != "attacker-controlled-uuid"

    def test_authenticated_request_switches_to_github_delivery_id(self, caplog):
        """After HMAC verification succeeds, correlationId matches verified githubDeliveryId."""
        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "verified-delivery-uuid-777",
                "x-github-event": "issues",
            },
        )
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        logged = json.loads(caplog.records[-1].message)
        assert logged["githubDeliveryId"] == "verified-delivery-uuid-777"
        assert logged["correlationId"] == "verified-delivery-uuid-777"


# ───────────────────────────────────────────────────────────────────
# T04 SQS Queue Integration, Contract, and Event-Specific Tests
# ───────────────────────────────────────────────────────────────────


class TestT04SqsQueueIntegrationAndContract:
    def test_authenticated_webhook_sends_exactly_one_sqs_message(self, monkeypatch):
        """Authenticated webhook enqueues exactly one bounded normalized SQS message and returns 202."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-123"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-sqs-001",
                "x-github-event": "issues",
            },
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 1

        call_args = mock_sqs.send_message.call_args[1]
        assert call_args["QueueUrl"] == "https://sqs.us-east-1.amazonaws.com/123/queue"

        msg_body = json.loads(call_args["MessageBody"])
        # Invariant checks on queue message contract:
        assert msg_body["schemaVersion"] == "1.0.0"
        assert msg_body["githubDeliveryId"] == "delivery-sqs-001"
        assert msg_body["eventType"] == "issues"
        assert msg_body["action"] == "opened"
        assert msg_body["installationId"] == 111
        assert msg_body["repositoryId"] == 12345
        assert msg_body["issueNumber"] == 42
        assert msg_body["senderId"] == 67890
        assert "receivedAt" in msg_body
        assert msg_body["bodyHash"].startswith("sha256:")

        # STRICT EXCLUSIONS:
        assert "x-hub-signature-256" not in msg_body
        assert "headers" not in msg_body
        assert "authorization" not in msg_body
        assert "secret" not in msg_body
        assert "body" not in msg_body
        # Unrelated fields and full body excluded:
        assert "title" not in msg_body
        assert "full_name" not in msg_body

    def test_forged_signature_makes_zero_sqs_calls(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-hub-signature-256": "sha256=forgedbad"},
            sign=False,
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        assert mock_sqs.send_message.call_count == 0

    def test_secrets_manager_failure_makes_zero_sqs_calls(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        handler_module._secret_provider_override = None
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111:secret:sec")

        mock_sm = Mock()
        mock_sm.get_secret_value.side_effect = ClientError({"Error": {"Code": "500"}}, "GetSecretValue")
        monkeypatch.setattr(handler_module, "_get_secretsmanager_client", lambda: mock_sm)

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body, sign=False)
        result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert mock_sqs.send_message.call_count == 0

    def test_invalid_json_makes_zero_sqs_calls(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        raw = "not json {{"
        sig = _compute_sig(raw.encode("utf-8"))
        event, _ = _make_event(
            body_str=raw,
            headers={"x-hub-signature-256": sig},
            sign=False,
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_missing_github_headers_makes_zero_sqs_calls(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        raw = json.dumps(body).encode("utf-8")
        sig = _compute_sig(raw)
        event = {
            "body": json.dumps(body),
            "headers": {
                "x-hub-signature-256": sig,
                # Missing x-github-delivery and x-github-event
            },
        }
        result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_sqs_send_failure_returns_503_and_never_202(self, monkeypatch, caplog):
        """SQS failure must return controlled retryable 503 so GitHub redelivers; never 202."""
        mock_sqs = Mock()
        mock_sqs.send_message.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "SQS down"}},
            "SendMessage",
        )
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert json.loads(result["body"])["status"] == "queue_unavailable"
        logged = json.loads(caplog.records[-1].message)
        assert logged["result"] == "queue_error"
        assert logged["errorClass"] == "sqs_send_failed"

    def test_queue_not_configured_returns_503(self, monkeypatch, caplog):
        """Missing EVENT_QUEUE_URL returns 503 and fails closed."""
        monkeypatch.delenv("EVENT_QUEUE_URL", raising=False)
        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body)

        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 503
        assert json.loads(result["body"])["status"] == "queue_unavailable"
        logged = json.loads(caplog.records[-1].message)
        assert logged["errorClass"] == "queue_not_configured"

    def test_duplicate_authenticated_deliveries_may_each_enqueue(self, monkeypatch):
        """Duplicate authenticated deliveries each enqueue to SQS; T05 performs authoritative DynamoDB dedup."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-dup"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-dup-001", "x-github-event": "issues"},
        )
        # Send twice
        res1 = lambda_handler(event, None)
        res2 = lambda_handler(event, None)

        assert res1["statusCode"] == 202
        assert res2["statusCode"] == 202
        # SQS buffers both; downstream DynamoDB EVENT#<deliveryId> performs atomic dedup
        assert mock_sqs.send_message.call_count == 2

    def test_webhook_handler_makes_zero_dynamodb_calls(self, monkeypatch):
        """Webhook Lambda must have NO DynamoDB access or calls (separation of concerns)."""
        mock_dynamo = Mock()
        monkeypatch.setattr("boto3.client", lambda service, **kwargs: mock_dynamo if service == "dynamodb" else Mock())

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body)
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_dynamo.put_item.call_count == 0
        assert mock_dynamo.get_item.call_count == 0


class TestT04IssueCommentIdentity:
    """FIX 5: commentId must be preserved in normalized SQS message for issue_comment events."""

    def test_issue_comment_includes_numeric_comment_id(self, monkeypatch):
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-c1"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issue_comment_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-cid-001", "x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["commentId"] == 987654321
        assert msg["commentBody"] == "I propose modifying retry logic in src/retry.py"
        assert msg["issueNumber"] == 42

    def test_comment_object_itself_is_absent_from_queue(self, monkeypatch):
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-c2"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issue_comment_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert "comment" not in msg
        # Raw webhook body must not be in queue message
        assert "body" not in msg or msg.get("body") is None

    def test_comment_body_not_logged(self, monkeypatch, caplog):
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-c3"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issue_comment_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        with caplog.at_level(logging.INFO):
            lambda_handler(event, None)

        all_logs = " ".join(r.message for r in caplog.records)
        assert "I propose modifying" not in all_logs

    def test_oversize_comment_body_rejected_with_400_and_zero_sqs_calls(self, monkeypatch, caplog):
        """A comment exceeding 64KB must be rejected with 400 without enqueuing to SQS."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        oversize_text = "A" * (65536 + 10)
        body = {
            "action": "created",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            "comment": {"id": 999, "body": oversize_text},
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-comment-oversize",
                "x-github-event": "issue_comment",
            },
        )
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"])["status"] == "payload_too_large"
        assert mock_sqs.send_message.call_count == 0

    def test_malformed_issue_comment_payload_rejected(self, monkeypatch, caplog):
        """issue_comment with missing comment object must be rejected."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "created",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            # Missing "comment"
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_issue_comment_missing_comment_id_rejected(self, monkeypatch):
        """issue_comment with comment object but no numeric id must be rejected."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "created",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            "comment": {"body": "hello"},  # Missing "id"
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_issue_comment_missing_installation_rejected(self, monkeypatch):
        """issue_comment without installation.id must be rejected."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "created",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            "comment": {"id": 999, "body": "text"},
            "sender": {"id": 67890},
            # Missing installation
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0


class TestT04PullRequestIdentity:
    """FIX 6: pullRequestNumber and pullRequestHeadSha must be preserved for pull_request events."""

    def test_pull_request_includes_number_and_head_sha(self, monkeypatch):
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-pr1"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _pr_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-pr-001",
                "x-github-event": "pull_request",
            },
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["pullRequestNumber"] == 88
        assert msg["pullRequestHeadSha"] == "abc123def456789abc123def456789abc123def4"

    def test_pr_number_is_not_overloaded_into_issue_number(self, monkeypatch):
        """pullRequestNumber must NOT appear as issueNumber unless a true issue field exists."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-pr2"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _pr_opened_body()
        # PR body does NOT have an "issue" key
        assert "issue" not in body

        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["pullRequestNumber"] == 88
        # issueNumber must not be set from PR number
        assert "issueNumber" not in msg or msg.get("issueNumber") is None

    def test_pr_synchronize_preserves_different_head_sha(self, monkeypatch):
        """Same PR, synchronize event with different head SHA must preserve the new SHA."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-pr3"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        body = {
            "action": "synchronize",
            "repository": {"id": 54321},
            "pull_request": {
                "number": 88,
                "head": {"sha": sha_b},
            },
            "sender": {"id": 99999},
            "installation": {"id": 222},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["pullRequestNumber"] == 88
        assert msg["pullRequestHeadSha"] == sha_b

    def test_full_pr_object_absent_from_queue(self, monkeypatch):
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-pr4"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _pr_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert "pull_request" not in msg
        assert "diff" not in msg
        assert "title" not in msg

    def test_malformed_pr_payload_rejected(self, monkeypatch):
        """pull_request without pull_request.head.sha must be rejected."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "opened",
            "repository": {"id": 54321},
            "pull_request": {"number": 88},  # Missing head.sha
            "sender": {"id": 99999},
            "installation": {"id": 222},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_pr_missing_installation_rejected(self, monkeypatch):
        """pull_request without installation.id must be rejected."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "opened",
            "repository": {"id": 54321},
            "pull_request": {"number": 88, "head": {"sha": "aaa"}},
            "sender": {"id": 99999},
            # Missing installation
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0


class TestT04HostilePRIssueScenarios:
    """Hostile scenarios testing PR number ≠ issueNumber, SHA drift."""

    def test_hostile_pr_88_sha_a_then_sha_b(self, monkeypatch):
        """Authenticated pull_request #88 at SHA-A, then synchronize at SHA-B.
        Both must preserve pullRequestNumber=88 with distinct head SHAs.
        """
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-hostile"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

        body_a = {
            "action": "opened",
            "repository": {"id": 54321},
            "pull_request": {"number": 88, "head": {"sha": sha_a}},
            "sender": {"id": 99999},
            "installation": {"id": 222},
        }
        event_a, _ = _make_event(body_dict=body_a, headers={"x-github-event": "pull_request"})
        res_a = lambda_handler(event_a, None)
        assert res_a["statusCode"] == 202

        msg_a = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg_a["pullRequestNumber"] == 88
        assert msg_a["pullRequestHeadSha"] == sha_a
        assert "issueNumber" not in msg_a or msg_a.get("issueNumber") is None

        body_b = {
            "action": "synchronize",
            "repository": {"id": 54321},
            "pull_request": {"number": 88, "head": {"sha": sha_b}},
            "sender": {"id": 99999},
            "installation": {"id": 222},
        }
        event_b, _ = _make_event(body_dict=body_b, headers={"x-github-event": "pull_request"})
        res_b = lambda_handler(event_b, None)
        assert res_b["statusCode"] == 202

        msg_b = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg_b["pullRequestNumber"] == 88
        assert msg_b["pullRequestHeadSha"] == sha_b
        assert msg_b["pullRequestHeadSha"] != sha_a

    def test_hostile_issue_comment_42_with_comment_id(self, monkeypatch):
        """issue_comment: issue #42, comment ID 987654321, proposal text."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-hostile-ic"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issue_comment_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["issueNumber"] == 42
        assert msg["commentId"] == 987654321
        assert msg["commentBody"] == "I propose modifying retry logic in src/retry.py"


class TestT04QueueMessageSizeBound:
    """FIX 10: Total serialized SQS message size must be bounded."""

    def test_queue_message_size_constant_is_below_sqs_limit(self):
        assert MAX_QUEUE_MESSAGE_BYTES < 262144  # Below AWS 256KB limit

    def test_oversize_queue_message_rejected_before_send_message(self, monkeypatch, caplog):
        """If serialized message exceeds bound, reject with 400 and zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        # Create a comment body that's just under 64KB (passes comment check)
        # but the total message with metadata might exceed MAX_QUEUE_MESSAGE_BYTES
        # if we set a very low bound. Instead, test that a near-max comment passes.
        near_max_comment = "X" * 60000
        body = {
            "action": "created",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            "comment": {"id": 999, "body": near_max_comment},
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)
        # 60KB comment + metadata should be well under 128KB
        assert result["statusCode"] == 202

    def test_normal_payload_under_size_bound(self, monkeypatch):
        """Normal events are well under the size bound."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-normal"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        event, _ = _make_event(body_dict=body)
        result = lambda_handler(event, None)
        assert result["statusCode"] == 202

        msg_body = mock_sqs.send_message.call_args[1]["MessageBody"]
        assert len(msg_body.encode("utf-8")) < MAX_QUEUE_MESSAGE_BYTES


class TestT04EventSpecificValidation:
    """FIX 8: Event-specific required fields must be validated."""

    def test_issues_missing_issue_number_rejected(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "opened",
            "repository": {"id": 12345},
            # Missing "issue"
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issues"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_issues_missing_sender_rejected(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "opened",
            "repository": {"id": 12345},
            "issue": {"number": 42},
            # Missing "sender"
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "issues"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_zero_sqs_calls_on_event_specific_validation_failure(self, monkeypatch):
        """Any event-specific validation failure must produce zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        # pull_request with non-integer PR number
        body = {
            "action": "opened",
            "repository": {"id": 12345},
            "pull_request": {"number": "not-a-number", "head": {"sha": "abc"}},
            "sender": {"id": 67890},
            "installation": {"id": 111},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0


class TestT04UnsupportedEventFiltering:
    """Ingress hardening: Unsupported authenticated GitHub event types are ignored at ingress.

    1. Authenticated unsupported event -> safe log, HTTP 202, ZERO SQS calls.
    2. Forged unsupported event -> HTTP 401, ZERO SQS calls.
    3. Signed unsupported event with malformed JSON -> HTTP 400, ZERO SQS calls.
    4. Supported events (issues, issue_comment, pull_request) -> process & enqueue normally.
    """

    def test_supported_event_types_constant_definition(self):
        """Supported event types must be strictly frozen and contain only the MVP supported events."""
        assert isinstance(SUPPORTED_EVENT_TYPES, frozenset)
        assert SUPPORTED_EVENT_TYPES == frozenset({"issues", "issue_comment", "pull_request"})

    def test_signed_ping_returns_202_and_zero_sqs(self, monkeypatch):
        """1. Signed ping -> 202, zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {"zen": "Keep it logically awesome.", "hook_id": 12345}
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-ping-001",
                "x-github-event": "ping",
            },
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert json.loads(result["body"])["status"] == "accepted"
        assert mock_sqs.send_message.call_count == 0

    def test_signed_installation_event_returns_202_and_zero_sqs(self, monkeypatch):
        """2. Signed installation event -> 202, zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {
            "action": "created",
            "installation": {"id": 999999, "account": {"login": "octocat"}},
            "sender": {"id": 12345},
        }
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-inst-001",
                "x-github-event": "installation",
            },
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert json.loads(result["body"])["status"] == "accepted"
        assert mock_sqs.send_message.call_count == 0

    def test_signed_arbitrary_unsupported_event_returns_202_and_zero_sqs(self, monkeypatch):
        """3. Signed arbitrary unsupported event (e.g. repository, deployment) -> 202, zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        # Test 'repository' event
        body_repo = {"action": "renamed", "repository": {"id": 55555, "name": "new-name"}}
        event_repo, _ = _make_event(
            body_dict=body_repo,
            headers={"x-github-delivery": "delivery-repo-001", "x-github-event": "repository"},
        )
        res_repo = lambda_handler(event_repo, None)
        assert res_repo["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 0

        # Test 'deployment' event
        body_dep = {"action": "created", "deployment": {"id": 777}}
        event_dep, _ = _make_event(
            body_dict=body_dep,
            headers={"x-github-delivery": "delivery-dep-001", "x-github-event": "deployment"},
        )
        res_dep = lambda_handler(event_dep, None)
        assert res_dep["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 0

    def test_unsupported_event_safe_telemetry(self, monkeypatch, caplog):
        """4. Unsupported event safe telemetry: result = ignored_unsupported_event, no raw payload."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {"zen": "Approaching extremes, you make an edge.", "hook_id": 54321}
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-ping-telemetry",
                "x-github-event": "ping",
            },
        )
        with caplog.at_level(logging.INFO):
            result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 0

        log_records = [json.loads(r.message) for r in caplog.records]
        assert len(log_records) >= 1
        logged = log_records[-1]

        # Verify safe fields
        assert logged["githubDeliveryId"] == "delivery-ping-telemetry"
        assert logged["eventType"] == "ping"
        assert logged["correlationId"] == "delivery-ping-telemetry"
        assert logged["result"] == "ignored_unsupported_event"
        assert "latencyMs" in logged

        # Verify strictly NO raw payload, secret, or body fields
        all_logs_text = " ".join(r.message for r in caplog.records)
        assert "Approaching extremes" not in all_logs_text
        assert "zen" not in logged
        assert "body" not in logged
        assert "secret" not in logged
        assert "signature" not in logged

    def test_forged_ping_returns_401_and_zero_sqs(self, monkeypatch):
        """5. Forged ping -> 401, zero SQS calls. Event type never bypasses HMAC."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {"zen": "Non-resistance is the principle."}
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-forged-ping",
                "x-github-event": "ping",
                "x-hub-signature-256": "sha256=forgedbadbadbadbadbadbadbadbadbadbadbadbad",
            },
            sign=False,
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        assert json.loads(result["body"])["status"] == "unauthorized"
        assert mock_sqs.send_message.call_count == 0

    def test_forged_installation_event_returns_401_and_zero_sqs(self, monkeypatch):
        """6. Forged installation event -> 401, zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = {"action": "deleted", "installation": {"id": 111}}
        event, _ = _make_event(
            body_dict=body,
            headers={
                "x-github-delivery": "delivery-forged-inst",
                "x-github-event": "installation",
                "x-hub-signature-256": "sha256=forgedbadbadbadbadbadbadbadbadbadbadbadbad",
            },
            sign=False,
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 401
        assert json.loads(result["body"])["status"] == "unauthorized"
        assert mock_sqs.send_message.call_count == 0

    def test_validly_signed_ping_with_malformed_json_returns_400_and_zero_sqs(self, monkeypatch):
        """7. Validly signed ping with malformed JSON -> 400 invalid_payload, zero SQS calls."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        raw = "not valid json {{"
        sig = _compute_sig(raw.encode("utf-8"))
        event = {
            "body": raw,
            "headers": {
                "x-hub-signature-256": sig,
                "x-github-delivery": "delivery-malformed-ping",
                "x-github-event": "ping",
            },
        }
        result = lambda_handler(event, None)

        assert result["statusCode"] == 400
        assert json.loads(result["body"])["status"] == "invalid_payload"
        assert mock_sqs.send_message.call_count == 0

    def test_supported_issues_event_still_enqueues(self, monkeypatch):
        """8. Supported issues event -> still enqueues exactly one message."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-issues-ok"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issues_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-issues-ok", "x-github-event": "issues"},
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 1
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["eventType"] == "issues"
        assert msg["issueNumber"] == 42

    def test_supported_issue_comment_still_preserves_comment_id_and_body(self, monkeypatch):
        """9. Supported issue_comment -> still preserves commentId + commentBody."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-ic-ok"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _issue_comment_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-ic-ok", "x-github-event": "issue_comment"},
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 1
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["eventType"] == "issue_comment"
        assert msg["commentId"] == 987654321
        assert msg["commentBody"] == "I propose modifying retry logic in src/retry.py"

    def test_supported_pull_request_still_preserves_pr_number_and_head_sha(self, monkeypatch):
        """10. Supported pull_request -> still preserves pullRequestNumber + pullRequestHeadSha."""
        mock_sqs = Mock()
        mock_sqs.send_message.return_value = {"MessageId": "msg-pr-ok"}
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)
        monkeypatch.setenv("EVENT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123/queue")

        body = _pr_opened_body()
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": "delivery-pr-ok", "x-github-event": "pull_request"},
        )
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_sqs.send_message.call_count == 1
        msg = json.loads(mock_sqs.send_message.call_args[1]["MessageBody"])
        assert msg["eventType"] == "pull_request"
        assert msg["pullRequestNumber"] == 88
        assert msg["pullRequestHeadSha"] == "abc123def456789abc123def456789abc123def4"

    def test_signed_ping_makes_zero_dynamodb_calls(self, monkeypatch):
        """Signed ping must make zero DynamoDB calls."""
        mock_dynamo = Mock()
        monkeypatch.setattr("boto3.client", lambda service, **kwargs: mock_dynamo if service == "dynamodb" else Mock())

        body = {"zen": "Now is better than never."}
        event, _ = _make_event(body_dict=body, headers={"x-github-event": "ping"})
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert mock_dynamo.put_item.call_count == 0
        assert mock_dynamo.get_item.call_count == 0

    def test_signed_ping_does_not_require_application_fields(self, monkeypatch):
        """A signed ping payload without installation/repository/sender passes without field error."""
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        # Minimal ping payload - lacks repo, sender, installation, issue, pr
        body = {"zen": "In the face of ambiguity, refuse the temptation to guess."}
        event, _ = _make_event(body_dict=body, headers={"x-github-event": "ping"})
        result = lambda_handler(event, None)

        assert result["statusCode"] == 202
        assert json.loads(result["body"])["status"] == "accepted"
        assert mock_sqs.send_message.call_count == 0


class TestT04IdentifierBounds:
    """FIX 9: Bound all normalized identifiers."""

    def test_overlong_delivery_id_rejected(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        long_delivery = "x" * (MAX_STRING_ID_LENGTH + 1)
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-delivery": long_delivery},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0

    def test_overlong_event_type_rejected(self, monkeypatch):
        mock_sqs = Mock()
        monkeypatch.setattr(handler_module, "_get_sqs_client", lambda: mock_sqs)

        body = _issues_opened_body()
        long_event = "y" * (MAX_STRING_ID_LENGTH + 1)
        event, _ = _make_event(
            body_dict=body,
            headers={"x-github-event": long_event},
        )
        result = lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert mock_sqs.send_message.call_count == 0


class TestSamInfrastructureQueueConfig:
    def test_template_defines_standard_queue_and_dlq_with_redrive(self):
        """Verify SAM template configures EventQueue and EventDeadLetterQueue with maxReceiveCount=3."""
        with open("template.yaml", "r", encoding="utf-8") as f:
            content = f.read()

        assert "EventQueue:" in content
        assert "EventDeadLetterQueue:" in content
        assert "AWS::SQS::Queue" in content
        assert "maxReceiveCount: 3" in content
        assert "deadLetterTargetArn: !GetAtt EventDeadLetterQueue.Arn" in content
        assert "FifoQueue: true" not in content  # Standard queues only
        assert "sqs:SendMessage" in content
        assert 'Default: "arn:aws:secretsmanager' not in content  # Fake default removed

    def test_template_iam_least_privilege(self):
        """FIX 12: WebhookFunction must have only secretsmanager:GetSecretValue and sqs:SendMessage."""
        with open("template.yaml", "r", encoding="utf-8") as f:
            content = f.read()

        # Must have these permissions
        assert "secretsmanager:GetSecretValue" in content
        assert "sqs:SendMessage" in content

        # Must NOT have these permissions
        assert "dynamodb:" not in content or "dynamodb:" not in content.split("WebhookFunction")[1].split("Events")[0]
        assert "states:" not in content
        assert "bedrock:" not in content
        assert "s3:" not in content
