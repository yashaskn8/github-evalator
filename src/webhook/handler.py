"""GitHub Evalator — Webhook Ingress & HMAC Authentication (T01/T02).

Receives GitHub webhook events via API Gateway HTTP API (payload format 2.0).
Recovers unmodified payload bytes, verifies X-Hub-Signature-256 HMAC-SHA256
signature using secret from AWS Secrets Manager, validates required GitHub
headers, parses JSON payload object, emits safe structured telemetry, and
returns HTTP 202.

Execution invariant:
No JSON parsing, metadata processing, or application logic occurs before
cryptographic authentication succeeds.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Explicit allowlist of fields permitted in telemetry output.
# Anything not in this set is silently dropped by log_event().
_ALLOWED_FIELDS = frozenset({
    "githubDeliveryId",
    "eventType",
    "action",
    "installationId",
    "repositoryId",
    "issueNumber",
    "senderId",
    "bodyHash",
    "correlationId",
    "latencyMs",
    "result",
    "errorClass",
})

# Module-level Secrets Manager state
_secretsmanager_client = None
_cached_secret = None
_secret_provider_override = None


def log_event(**kwargs):
    """Emit a single structured JSON log line with only allowlisted fields."""
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_FIELDS and v is not None}
    logger.info(json.dumps(safe, default=str))


def _get_secretsmanager_client():
    global _secretsmanager_client
    if _secretsmanager_client is None:
        _secretsmanager_client = boto3.client("secretsmanager")
    return _secretsmanager_client


def _get_webhook_secret():
    """Retrieve the GitHub webhook secret securely from Secrets Manager or injectable provider.

    Returns:
        str | None: The plaintext secret string, or None if unavailable.
    """
    global _cached_secret
    if _secret_provider_override is not None:
        try:
            return _secret_provider_override()
        except Exception:
            return None

    if _cached_secret is not None:
        return _cached_secret

    secret_arn = os.environ.get("GITHUB_WEBHOOK_SECRET_ARN")
    if not secret_arn:
        return None

    try:
        client = _get_secretsmanager_client()
        response = client.get_secret_value(SecretId=secret_arn)
        secret_val = response.get("SecretString")
        if secret_val:
            _cached_secret = secret_val
            return secret_val
    except (BotoCoreError, ClientError, Exception):
        # Fail closed on any Secrets Manager exception; never leak details
        return None

    return None


def _verify_hmac_signature(raw_body_bytes: bytes, signature_header: str | None, secret: str) -> tuple[bool, str]:
    """Verify GitHub X-Hub-Signature-256 header using constant-time comparison.

    Returns:
        tuple[bool, str]: (is_valid, error_class)
    """
    if not signature_header:
        return False, "missing_signature"

    if not signature_header.startswith("sha256="):
        return False, "malformed_signature"

    try:
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            raw_body_bytes,
            hashlib.sha256,
        ).hexdigest()
    except Exception:
        return False, "invalid_signature"

    if not hmac.compare_digest(expected, signature_header):
        return False, "invalid_signature"

    return True, ""


def _recover_body_bytes(event):
    """Recover payload bytes from the API Gateway v2 event without reserialization.

    Payload bytes are reconstructed from the API Gateway v2 event without JSON
    reserialization and are ready for T02 signature verification.

    Returns:
        tuple[bytes, str | None]: (raw_bytes, error_class).
        If an error occurred, raw_bytes is b"" and error_class is set.
    """
    if not isinstance(event, dict):
        return b"", "invalid_body"

    body = event.get("body")
    if body is None:
        return b"", "empty_body"

    if not isinstance(body, (str, bytes)):
        return b"", "invalid_body"

    if event.get("isBase64Encoded", False):
        try:
            decoded = base64.b64decode(body, validate=True)
            if not decoded:
                return b"", "empty_body"
            return decoded, None
        except (binascii.Error, ValueError, TypeError):
            return b"", "invalid_base64"

    if isinstance(body, bytes):
        if not body:
            return b"", "empty_body"
        return body, None

    if isinstance(body, str):
        if not body:
            return b"", "empty_body"
        try:
            return body.encode("utf-8"), None
        except UnicodeEncodeError:
            return b"", "invalid_body"

    return b"", "invalid_body"


def _normalize_headers(event):
    """Build a lowercase header map for case-insensitive lookups."""
    if not isinstance(event, dict):
        return {}
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): v for k, v in headers.items() if k is not None}


def _resolve_correlation_id(headers, event, context):
    """Determine correlation token in priority order:
    1. x-github-delivery when present
    2. API Gateway requestContext.requestId
    3. Lambda context.aws_request_id
    4. final safe fallback 'unknown'
    """
    delivery = headers.get("x-github-delivery")
    if delivery:
        return str(delivery)
    if isinstance(event, dict):
        req_ctx = event.get("requestContext")
        if isinstance(req_ctx, dict):
            req_id = req_ctx.get("requestId")
            if req_id:
                return str(req_id)
    if context is not None:
        aws_req_id = getattr(context, "aws_request_id", None)
        if aws_req_id:
            return str(aws_req_id)
    return "unknown"


def _extract_safe_metadata(parsed_body):
    """Extract only safe metadata fields from the parsed webhook body."""
    meta = {}
    if not isinstance(parsed_body, dict):
        return meta
    meta["action"] = parsed_body.get("action")
    repo = parsed_body.get("repository")
    if isinstance(repo, dict):
        meta["repositoryId"] = repo.get("id")
    issue = parsed_body.get("issue")
    if isinstance(issue, dict):
        meta["issueNumber"] = issue.get("number")
    sender = parsed_body.get("sender")
    if isinstance(sender, dict):
        meta["senderId"] = sender.get("id")
    installation = parsed_body.get("installation")
    if isinstance(installation, dict):
        meta["installationId"] = installation.get("id")
    return meta


def _json_response(status_code, body_dict):
    """Build API Gateway v2 HTTP response with explicit Content-Type header."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body_dict),
    }


def lambda_handler(event, context):
    """Webhook ingress & HMAC authentication entry point."""
    start_time = time.perf_counter()

    # Step 1: Normalize headers for case-insensitive access
    headers = _normalize_headers(event)
    correlation_id = _resolve_correlation_id(headers, event, context)

    # Step 2: Recover exact payload bytes BEFORE authentication
    raw_bytes, body_error = _recover_body_bytes(event)
    if body_error is not None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass=body_error,
        )
        return _json_response(400, {"status": "invalid_payload"})

    # Step 3: Retrieve webhook secret (fail closed if unavailable)
    secret = _get_webhook_secret()
    if secret is None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            latencyMs=elapsed_ms,
            result="authentication_error",
            errorClass="secret_unavailable",
        )
        return _json_response(503, {"status": "authentication_unavailable"})

    # Step 4: Verify HMAC signature BEFORE JSON parsing or metadata extraction
    sig_header = headers.get("x-hub-signature-256")
    is_valid, sig_error = _verify_hmac_signature(raw_bytes, sig_header, secret)
    if not is_valid:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            latencyMs=elapsed_ms,
            result="unauthorized",
            errorClass=sig_error,
        )
        return _json_response(401, {"status": "unauthorized"})

    # ──────── REQUEST IS CRYPTOGRAPHICALLY AUTHENTICATED BEYOND THIS POINT ────────

    # Step 5: Validate required GitHub headers after authentication
    github_delivery_id = headers.get("x-github-delivery")
    event_type = headers.get("x-github-event")
    if not github_delivery_id or not event_type or not str(github_delivery_id).strip() or not str(event_type).strip():
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="missing_github_headers",
        )
        return _json_response(400, {"status": "invalid_webhook"})

    body_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    # Step 6: Parse body as JSON from raw bytes
    try:
        parsed_body = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=github_delivery_id,
            eventType=event_type,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="invalid_json",
        )
        return _json_response(400, {"status": "invalid_payload"})

    # Step 7: Require JSON root to be an object (reject arrays, scalars, null)
    if not isinstance(parsed_body, dict):
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=github_delivery_id,
            eventType=event_type,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="invalid_json_shape",
        )
        return _json_response(400, {"status": "invalid_payload"})

    # Step 8: Extract safe metadata from authenticated and validated object
    meta = _extract_safe_metadata(parsed_body)

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    log_event(
        githubDeliveryId=github_delivery_id,
        eventType=event_type,
        action=meta.get("action"),
        repositoryId=meta.get("repositoryId"),
        issueNumber=meta.get("issueNumber"),
        senderId=meta.get("senderId"),
        installationId=meta.get("installationId"),
        bodyHash=body_hash,
        correlationId=correlation_id,
        latencyMs=elapsed_ms,
        result="accepted",
    )

    return _json_response(202, {"status": "accepted"})
