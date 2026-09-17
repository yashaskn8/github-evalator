"""GitHub Evalator — Webhook Ingress Lambda (T01).

Receives GitHub webhook events via API Gateway HTTP API (payload format 2.0).
Extracts safe metadata, computes a body hash from the unmodified payload bytes,
emits structured telemetry, and returns HTTP 202.

Payload bytes are reconstructed from the API Gateway v2 event without JSON
reserialization and are ready for T02 signature verification.
This handler is a GitHub-compatible webhook ingress. It is NOT authenticated
until T02 implements HMAC-SHA256 signature verification.
"""

import base64
import binascii
import hashlib
import json
import logging
import time

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


def log_event(**kwargs):
    """Emit a single structured JSON log line with only allowlisted fields."""
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_FIELDS and v is not None}
    logger.info(json.dumps(safe, default=str))


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
    pull_request = parsed_body.get("pull_request")
    if isinstance(pull_request, dict) and "issueNumber" not in meta:
        meta["issueNumber"] = pull_request.get("number")
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
    """Webhook ingress entry point."""
    start_time = time.perf_counter()

    # Normalize headers for case-insensitive access (safe against headers=None)
    headers = _normalize_headers(event)

    # Resolve correlation token safely
    correlation_id = _resolve_correlation_id(headers, event, context)
    github_delivery_id = headers.get("x-github-delivery")
    event_type = headers.get("x-github-event")

    # Recover exact payload bytes without reserialization
    raw_bytes, body_error = _recover_body_bytes(event)

    # Handle body recovery errors (missing body, empty body, invalid base64, invalid body type)
    if body_error is not None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=github_delivery_id,
            eventType=event_type,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass=body_error,
        )
        return _json_response(400, {"status": "invalid_payload"})

    body_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    # Parse body as JSON from the raw bytes
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

    # Require JSON root to be a dictionary/object (reject array, scalar, string, null)
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

    # Extract safe metadata from parsed body object
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
