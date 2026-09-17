"""GitHub Evalator — Webhook Ingress, HMAC Authentication, & SQS Enqueue (T01-T04).

Receives GitHub webhook events via API Gateway HTTP API (payload format 2.0).
Recovers unmodified payload bytes, verifies X-Hub-Signature-256 HMAC-SHA256
signature using secret from AWS Secrets Manager (retrieved per-request to support
rotation without indefinite warm-Lambda caching), validates required GitHub
headers, parses JSON payload object, safely normalizes event data, enqueues
bounded normalized event to Amazon SQS Standard queue, emits safe structured
telemetry, and returns HTTP 202.

Execution invariants:
1. No JSON parsing, metadata processing, or application logic occurs before
   cryptographic authentication succeeds.
2. Pre-authentication log correlation derives strictly from AWS infrastructure
   request IDs (API Gateway / Lambda), never untrusted request headers.
3. Webhook secret is never cached indefinitely in Lambda memory.
4. Only normalized, bounded metadata is enqueued to SQS; raw payloads, secrets,
   and auth headers are strictly excluded.
5. Queue send failure returns 503; HTTP 202 is returned ONLY when SQS enqueue succeeds.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Explicit allowlist of fields permitted in telemetry output.
# Anything not in this set is silently dropped by log_event().
# Raw bodies, comments, secrets, and auth headers must NEVER appear here.
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

# Maximum permitted comment body length in bytes for issue_comment events (64 KB)
MAX_COMMENT_BODY_BYTES = 65536

# Module-level AWS clients and injection overrides
_secretsmanager_client = None
_sqs_client = None
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


def _get_sqs_client():
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def _get_webhook_secret() -> Optional[str]:
    """Retrieve the GitHub webhook secret securely from Secrets Manager or injectable provider.

    Does not cache indefinitely in global memory: retrieves the current secret
    per request to ensure rotation takes effect immediately.

    Returns:
        str | None: The plaintext secret string, or None if unavailable.
    """
    if _secret_provider_override is not None:
        try:
            return _secret_provider_override()
        except Exception:
            return None

    secret_arn = os.environ.get("GITHUB_WEBHOOK_SECRET_ARN")
    if not secret_arn:
        return None

    try:
        client = _get_secretsmanager_client()
        response = client.get_secret_value(SecretId=secret_arn)
        return response.get("SecretString")
    except (BotoCoreError, ClientError, Exception):
        # Fail closed on any Secrets Manager exception; never leak details
        return None


def _verify_hmac_signature(raw_body_bytes: bytes, signature_header: Optional[str], secret: str) -> Tuple[bool, str]:
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


def _recover_body_bytes(event: Any) -> Tuple[bytes, Optional[str]]:
    """Recover payload bytes from the API Gateway v2 event without reserialization.

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


def _normalize_headers(event: Any) -> Dict[str, Any]:
    """Build a lowercase header map for case-insensitive lookups."""
    if not isinstance(event, dict):
        return {}
    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): v for k, v in headers.items() if k is not None}


def _resolve_preauth_correlation_id(event: Any, context: Any) -> str:
    """Determine correlation token strictly from trusted AWS infrastructure metadata.

    Before cryptographic authentication succeeds, HTTP headers (including
    x-github-delivery) are attacker-controlled and MUST NOT be trusted for
    log correlation.
    Priority:
    1. API Gateway requestContext.requestId
    2. Lambda context.aws_request_id
    3. Safe fallback 'unknown'
    """
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


def _extract_safe_metadata(parsed_body: Any) -> Dict[str, Any]:
    """Extract only safe metadata fields from the parsed webhook body."""
    meta: Dict[str, Any] = {}
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


def _build_queue_message(
    github_delivery_id: str,
    event_type: str,
    body_hash: str,
    meta: Dict[str, Any],
    comment_body: Optional[str],
    received_at: int,
) -> Dict[str, Any]:
    """Build bounded normalized message for downstream SQS dispatch (T04).

    Strictly excludes HMAC signature, auth headers, and raw webhook payload.
    """
    msg: Dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "githubDeliveryId": github_delivery_id,
        "eventType": event_type,
        "action": meta.get("action"),
        "installationId": meta.get("installationId"),
        "repositoryId": meta.get("repositoryId"),
        "senderId": meta.get("senderId"),
        "receivedAt": received_at,
        "bodyHash": body_hash,
    }
    if meta.get("issueNumber") is not None:
        msg["issueNumber"] = meta.get("issueNumber")
    if comment_body is not None:
        msg["commentBody"] = comment_body
    return msg


def _json_response(status_code: int, body_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Build API Gateway v2 HTTP response with explicit Content-Type header."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body_dict),
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """Webhook ingress, HMAC authentication, & SQS enqueue entry point."""
    start_time = time.perf_counter()
    received_at = int(time.time())

    # Pre-authentication correlation ID uses infrastructure request IDs only.
    preauth_correlation_id = _resolve_preauth_correlation_id(event, context)

    # Step 1: Normalize headers for case-insensitive access
    headers = _normalize_headers(event)

    # Step 2: Recover exact payload bytes BEFORE authentication
    raw_bytes, body_error = _recover_body_bytes(event)
    if body_error is not None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=preauth_correlation_id,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass=body_error,
        )
        return _json_response(400, {"status": "invalid_payload"})

    # Step 3: Retrieve webhook secret (fail closed if unavailable; queries SM per request)
    secret = _get_webhook_secret()
    if secret is None:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=preauth_correlation_id,
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
            correlationId=preauth_correlation_id,
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
            correlationId=preauth_correlation_id,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="missing_github_headers",
        )
        return _json_response(400, {"status": "invalid_webhook"})

    # Now that HMAC is verified and delivery header is validated, switch to GitHub delivery ID
    correlation_id = str(github_delivery_id)
    event_type_str = str(event_type).strip()
    body_hash = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()

    # Step 6: Parse body as JSON from raw bytes
    try:
        parsed_body = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=correlation_id,
            eventType=event_type_str,
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
            githubDeliveryId=correlation_id,
            eventType=event_type_str,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="invalid_payload",
            errorClass="invalid_json_shape",
        )
        return _json_response(400, {"status": "invalid_payload"})

    # Step 8: Extract safe metadata from authenticated and validated object
    meta = _extract_safe_metadata(parsed_body)

    # Step 9: If issue_comment, extract proposal text with enforced size bound
    comment_body: Optional[str] = None
    if event_type_str == "issue_comment":
        comment_obj = parsed_body.get("comment")
        if isinstance(comment_obj, dict):
            raw_comment = comment_obj.get("body")
            if isinstance(raw_comment, str):
                if len(raw_comment.encode("utf-8")) > MAX_COMMENT_BODY_BYTES:
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    log_event(
                        correlationId=correlation_id,
                        githubDeliveryId=correlation_id,
                        eventType=event_type_str,
                        bodyHash=body_hash,
                        latencyMs=elapsed_ms,
                        result="invalid_payload",
                        errorClass="payload_too_large",
                    )
                    return _json_response(400, {"status": "payload_too_large"})
                comment_body = raw_comment

    # Step 10: Build bounded normalized message for SQS
    queue_message = _build_queue_message(
        github_delivery_id=correlation_id,
        event_type=event_type_str,
        body_hash=body_hash,
        meta=meta,
        comment_body=comment_body,
        received_at=received_at,
    )

    # Step 11: Enqueue normalized event to SQS Standard Queue (T04)
    queue_url = os.environ.get("EVENT_QUEUE_URL")
    if not queue_url:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=correlation_id,
            eventType=event_type_str,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="queue_error",
            errorClass="queue_not_configured",
        )
        return _json_response(503, {"status": "queue_unavailable"})

    try:
        sqs = _get_sqs_client()
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(queue_message),
        )
    except (BotoCoreError, ClientError, Exception):
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            correlationId=correlation_id,
            githubDeliveryId=correlation_id,
            eventType=event_type_str,
            bodyHash=body_hash,
            latencyMs=elapsed_ms,
            result="queue_error",
            errorClass="sqs_send_failed",
        )
        return _json_response(503, {"status": "queue_unavailable"})

    # Step 12: Success — Emit structured telemetry & return HTTP 202
    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    log_event(
        githubDeliveryId=correlation_id,
        eventType=event_type_str,
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

