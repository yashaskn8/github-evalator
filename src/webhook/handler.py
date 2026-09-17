"""GitHub Evalator — Webhook Ingress Lambda (T01).

Receives GitHub webhook events via API Gateway HTTP API (payload format 2.0).
Extracts safe metadata, computes a body hash from the unmodified payload bytes,
emits structured telemetry, and returns HTTP 202.

This handler is a GitHub-compatible webhook ingress. It is NOT authenticated
until T02 implements HMAC-SHA256 signature verification.
"""

import base64
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

    If isBase64Encoded is true, base64-decode the body.
    Otherwise, encode the body string as UTF-8 bytes.
    Returns the raw bytes for hashing (and future T02 HMAC verification).
    """
    body_str = event.get("body", "")
    if event.get("isBase64Encoded", False):
        return base64.b64decode(body_str)
    return body_str.encode("utf-8") if body_str else b""


def _normalize_headers(event):
    """Build a lowercase header map for case-insensitive lookups."""
    return {k.lower(): v for k, v in event.get("headers", {}).items()}


def _extract_safe_metadata(parsed_body):
    """Extract only safe metadata fields from the parsed webhook body."""
    meta = {}
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


def lambda_handler(event, context):
    """Webhook ingress entry point."""
    start_time = time.time()

    # Normalize headers for case-insensitive access
    headers = _normalize_headers(event)

    # Correlation: prefer GitHub delivery ID, fall back to API Gateway request ID
    github_delivery_id = headers.get("x-github-delivery")
    apigw_request_id = event.get("requestContext", {}).get("requestId", "unknown")
    correlation_id = github_delivery_id or apigw_request_id

    # Event type from GitHub header
    event_type = headers.get("x-github-event")

    # Recover exact payload bytes — never JSON-reserialize before hashing
    raw_bytes = _recover_body_bytes(event)
    body_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    # Parse body as JSON from the raw bytes (read-only operation on the bytes)
    try:
        parsed_body = json.loads(raw_bytes) if raw_bytes else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        elapsed_ms = int((time.time() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=github_delivery_id,
            eventType=event_type,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="invalid_json",
        )
        return {
            "statusCode": 400,
            "body": json.dumps({"status": "invalid_payload"}),
        }

    # Extract safe metadata from parsed body
    meta = _extract_safe_metadata(parsed_body)

    elapsed_ms = int((time.time() - start_time) * 1000)

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

    return {
        "statusCode": 202,
        "body": json.dumps({"status": "accepted"}),
    }
