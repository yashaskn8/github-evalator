"""GitHub Evalator — Event Dispatcher & Step Functions Orchestrator (T05).

Consumes normalized GitHub webhook events from Amazon SQS Standard queue,
performs defensive schema validation, achieves atomic DynamoDB event admission
(avoiding naive duplicate-drop patterns that lose events during crashes), and
orchestrates crash-safe exactly-one logical AWS Step Functions Standard execution.

Execution invariants:
1. SQS is a transport buffer, NOT authoritative deduplication. DynamoDB is authoritative.
2. The admission decision uses a single atomic conditional PutItem (attribute_not_exists(PK)).
3. Crash window survival: A worker crash after DynamoDB admission but before/during
   StartExecution is safely recovered on SQS retry via strongly consistent GetItem.
4. Step Functions Standard StartExecution idempotency (same execution name + same canonical
   input) guarantees exactly one logical workflow execution per delivery ID.
5. Volatile ingress timing (receivedAt) is excluded from canonical workflow input and hashes.
6. ExecutionAlreadyExists on a brand-new admission is treated as an anomaly and fails closed.
7. Payload or tenant collisions on duplicate delivery IDs are rejected as poison events.
8. Partial batch failure (ReportBatchItemFailures) isolates failed/poison records from peers.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

try:
    from state.models import (
        build_event_admission_request,
        build_event_workflow_started_update_request,
        format_event_pk,
    )
except ImportError:
    from src.state.models import (
        build_event_admission_request,
        build_event_workflow_started_update_request,
        format_event_pk,
    )

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Supported event types at dispatcher
SUPPORTED_EVENT_TYPES = frozenset({
    "issues",
    "issue_comment",
    "pull_request",
})

# Allowlist of safe structured telemetry fields.
# Raw payloads, comments, secrets, and auth tokens must NEVER be logged.
_ALLOWED_FIELDS = frozenset({
    "githubDeliveryId",
    "eventType",
    "installationId",
    "repositoryId",
    "sqsMessageId",
    "workflowExecutionArn",
    "result",
    "errorClass",
    "latencyMs",
})

# Module-level AWS clients and injection overrides
_dynamodb_client = None
_sfn_client = None


def log_event(**kwargs):
    """Emit a single structured JSON log line with only allowlisted safe fields."""
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_FIELDS and v is not None}
    logger.info(json.dumps(safe, default=str))


def _get_dynamodb_client():
    global _dynamodb_client
    if _dynamodb_client is None:
        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _dynamodb_client = boto3.client("dynamodb", region_name=region)
    return _dynamodb_client


def _get_sfn_client():
    global _sfn_client
    if _sfn_client is None:
        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _sfn_client = boto3.client("stepfunctions", region_name=region)
    return _sfn_client


def compute_deterministic_execution_name(delivery_id: str) -> str:
    """Generate a deterministic safe execution name from delivery ID.

    Step Functions execution name limit: 1-80 characters [a-zA-Z0-9_-]+.
    Length: 3 + 64 = 67 chars.
    """
    return "gh-" + hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()


def reconstruct_execution_arn(state_machine_arn: str, execution_name: str) -> str:
    """Reconstruct execution ARN from state machine ARN and execution name.

    Standard ARN layout:
    arn:aws:states:<region>:<account>:stateMachine:<name>
    -> arn:aws:states:<region>:<account>:execution:<name>:<executionName>
    """
    if ":stateMachine:" in state_machine_arn:
        base = state_machine_arn.replace(":stateMachine:", ":execution:")
        return f"{base}:{execution_name}"
    return f"{state_machine_arn}:{execution_name}"


def canonicalize_stable_workflow_input(msg_dict: Dict[str, Any]) -> Tuple[str, str]:
    """Construct deterministic canonical workflow input and hash.

    Excludes volatile timing (receivedAt) so different arrival times for the
    same logical event produce the exact same execution input and hash.
    """
    stable = {k: v for k, v in msg_dict.items() if k != "receivedAt"}
    canonical_str = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    workflow_input_hash = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
    return canonical_str, workflow_input_hash


def _validate_sqs_message(msg: Any) -> Tuple[bool, Optional[str]]:
    """Defensively validate normalized SQS message schema and contracts."""
    if not isinstance(msg, dict):
        return False, "invalid_json_shape"

    if msg.get("schemaVersion") != "1.0.0":
        return False, "invalid_schema_version"

    delivery_id = msg.get("githubDeliveryId")
    if not isinstance(delivery_id, str) or not (0 < len(delivery_id) <= 1024):
        return False, "missing_or_invalid_delivery_id"

    event_type = msg.get("eventType")
    if event_type not in SUPPORTED_EVENT_TYPES:
        return False, "unsupported_event_type"

    inst_id = msg.get("installationId")
    if not isinstance(inst_id, int) or inst_id <= 0:
        return False, "missing_or_invalid_installation_id"

    repo_id = msg.get("repositoryId")
    if not isinstance(repo_id, int) or repo_id <= 0:
        return False, "missing_or_invalid_repository_id"

    sender_id = msg.get("senderId")
    if not isinstance(sender_id, int) or sender_id <= 0:
        return False, "missing_or_invalid_sender_id"

    body_hash = msg.get("bodyHash")
    if not isinstance(body_hash, str) or not (0 < len(body_hash) <= 1024):
        return False, "missing_or_invalid_body_hash"

    # Event-specific validation
    if event_type == "issues":
        issue_num = msg.get("issueNumber")
        if not isinstance(issue_num, int) or issue_num <= 0:
            return False, "missing_or_invalid_issue_number"

    elif event_type == "issue_comment":
        issue_num = msg.get("issueNumber")
        if not isinstance(issue_num, int) or issue_num <= 0:
            return False, "missing_or_invalid_issue_number"
        comment_id = msg.get("commentId")
        if not isinstance(comment_id, int) or comment_id <= 0:
            return False, "missing_or_invalid_comment_id"
        comment_body = msg.get("commentBody")
        if not isinstance(comment_body, str) or len(comment_body.encode("utf-8")) > 65536:
            return False, "missing_or_oversize_comment_body"

    elif event_type == "pull_request":
        pr_num = msg.get("pullRequestNumber")
        if not isinstance(pr_num, int) or pr_num <= 0:
            return False, "missing_or_invalid_pr_number"
        head_sha = msg.get("pullRequestHeadSha")
        if not isinstance(head_sha, str) or not (0 < len(head_sha) <= 256):
            return False, "missing_or_invalid_head_sha"

    return True, None


def _process_record(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Process a single SQS record with atomic admission and crash recovery.

    Returns:
        (success, error_class). If success is False, record must be retried.
    """
    start_time = time.perf_counter()
    sqs_msg_id = str(record.get("messageId", "unknown"))

    raw_body = record.get("body")
    if not isinstance(raw_body, str):
        log_event(sqsMessageId=sqs_msg_id, result="invalid_message", errorClass="missing_body")
        return False, "missing_body"

    try:
        msg = json.loads(raw_body)
    except Exception:
        log_event(sqsMessageId=sqs_msg_id, result="invalid_message", errorClass="invalid_json")
        return False, "invalid_json"

    is_valid, validation_err = _validate_sqs_message(msg)
    if not is_valid:
        log_event(
            sqsMessageId=sqs_msg_id,
            githubDeliveryId=msg.get("githubDeliveryId") if isinstance(msg, dict) else None,
            eventType=msg.get("eventType") if isinstance(msg, dict) else None,
            result="invalid_message",
            errorClass=validation_err,
        )
        return False, validation_err or "validation_failed"

    delivery_id = str(msg["githubDeliveryId"])
    event_type = str(msg["eventType"])
    installation_id = int(msg["installationId"])
    repo_id = int(msg["repositoryId"])
    body_hash = str(msg["bodyHash"])

    table_name = os.environ.get("STATE_TABLE_NAME")
    state_machine_arn = os.environ.get("EVALUATION_STATE_MACHINE_ARN")
    if not table_name or not state_machine_arn:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            sqsMessageId=sqs_msg_id,
            result="dependency_error",
            errorClass="missing_configuration",
        )
        return False, "missing_configuration"

    canonical_input, workflow_input_hash = canonicalize_stable_workflow_input(msg)
    execution_name = compute_deterministic_execution_name(delivery_id)

    dynamodb = _get_dynamodb_client()

    # Step 1: Atomic DynamoDB EVENT Admission (First Authority Decision)
    admission_req = build_event_admission_request(
        table_name=table_name,
        delivery_id=delivery_id,
        event_type=event_type,
        installation_id=installation_id,
        repository_id=repo_id,
        body_hash=body_hash,
        workflow_input_hash=workflow_input_hash,
    )

    try:
        dynamodb.put_item(**admission_req)
        # Admission succeeded — this is a new event
        is_new_admission = True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            is_new_admission = False
        else:
            log_event(
                githubDeliveryId=delivery_id,
                eventType=event_type,
                installationId=installation_id,
                repositoryId=repo_id,
                sqsMessageId=sqs_msg_id,
                result="dependency_error",
                errorClass=f"dynamodb_put_{code}",
            )
            return False, f"dynamodb_put_{code}"
    except Exception:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            result="dependency_error",
            errorClass="dynamodb_put_exception",
        )
        return False, "dynamodb_put_exception"

    # Step 2: Handle Brand-New Admission
    if is_new_admission:
        try:
            sfn = _get_sfn_client()
            resp = sfn.start_execution(
                stateMachineArn=state_machine_arn,
                name=execution_name,
                input=canonical_input,
            )
            execution_arn = resp["executionArn"]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "ExecutionAlreadyExists":
                # Unexpected collision for a brand-new admission item
                log_event(
                    githubDeliveryId=delivery_id,
                    eventType=event_type,
                    installationId=installation_id,
                    repositoryId=repo_id,
                    sqsMessageId=sqs_msg_id,
                    result="dependency_error",
                    errorClass="unexpected_execution_already_exists",
                )
                return False, "unexpected_execution_already_exists"
            else:
                log_event(
                    githubDeliveryId=delivery_id,
                    eventType=event_type,
                    installationId=installation_id,
                    repositoryId=repo_id,
                    sqsMessageId=sqs_msg_id,
                    result="dependency_error",
                    errorClass=f"sfn_start_{code}",
                )
                return False, f"sfn_start_{code}"
        except Exception:
            log_event(
                githubDeliveryId=delivery_id,
                eventType=event_type,
                installationId=installation_id,
                repositoryId=repo_id,
                sqsMessageId=sqs_msg_id,
                result="dependency_error",
                errorClass="sfn_start_exception",
            )
            return False, "sfn_start_exception"

        # Record STARTED status conditionally
        update_req = build_event_workflow_started_update_request(
            table_name=table_name,
            delivery_id=delivery_id,
            workflow_execution_arn=execution_arn,
            expected_workflow_input_hash=workflow_input_hash,
        )
        try:
            dynamodb.update_item(**update_req)
        except Exception:
            log_event(
                githubDeliveryId=delivery_id,
                eventType=event_type,
                installationId=installation_id,
                repositoryId=repo_id,
                sqsMessageId=sqs_msg_id,
                workflowExecutionArn=execution_arn,
                result="dependency_error",
                errorClass="update_started_failed",
            )
            return False, "update_started_failed"

        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            workflowExecutionArn=execution_arn,
            latencyMs=elapsed_ms,
            result="workflow_started",
        )
        return True, ""

    # Step 3: Duplicate Delivery or Crash Recovery
    # Strongly consistent read to inspect authoritative event state
    try:
        get_resp = dynamodb.get_item(
            TableName=table_name,
            Key={
                "PK": {"S": format_event_pk(delivery_id)},
                "SK": {"S": "METADATA"},
            },
            ConsistentRead=True,
        )
        stored_item = get_resp.get("Item")
    except Exception:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            sqsMessageId=sqs_msg_id,
            result="dependency_error",
            errorClass="dynamodb_get_failed",
        )
        return False, "dynamodb_get_failed"

    if not stored_item:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            sqsMessageId=sqs_msg_id,
            result="dependency_error",
            errorClass="item_missing_after_condition_failed",
        )
        return False, "item_missing_after_condition_failed"

    stored_body_hash = stored_item.get("bodyHash", {}).get("S")
    stored_input_hash = stored_item.get("workflowInputHash", {}).get("S")
    stored_installation_id = int(stored_item.get("installationId", {}).get("N", 0))
    stored_repo_id = int(stored_item.get("repositoryId", {}).get("N", 0))
    stored_event_type = stored_item.get("eventType", {}).get("S")
    stored_status = stored_item.get("status", {}).get("S")
    stored_execution_arn = stored_item.get("workflowExecutionArn", {}).get("S")

    # Hostile Check: Delivery ID collision with mismatched payload/tenant
    if (
        stored_body_hash != body_hash
        or stored_input_hash != workflow_input_hash
        or stored_installation_id != installation_id
        or stored_repo_id != repo_id
        or stored_event_type != event_type
    ):
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            result="delivery_collision",
            errorClass="payload_or_identity_collision",
        )
        return False, "delivery_collision"

    # Case A: Duplicate already STARTED
    if stored_status == "STARTED" and stored_execution_arn:
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            workflowExecutionArn=stored_execution_arn,
            latencyMs=elapsed_ms,
            result="duplicate_started",
        )
        return True, ""

    # Case B: Crash Recovery — ADMITTED but workflow execution not recorded
    try:
        sfn = _get_sfn_client()
        resp = sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=canonical_input,
        )
        recovery_arn = resp.get("executionArn")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ExecutionAlreadyExists":
            # Execution already exists in Step Functions from prior attempt
            recovery_arn = reconstruct_execution_arn(state_machine_arn, execution_name)
        else:
            log_event(
                githubDeliveryId=delivery_id,
                eventType=event_type,
                installationId=installation_id,
                repositoryId=repo_id,
                sqsMessageId=sqs_msg_id,
                result="dependency_error",
                errorClass=f"recovery_sfn_{code}",
            )
            return False, f"recovery_sfn_{code}"
    except Exception:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            result="dependency_error",
            errorClass="recovery_sfn_exception",
        )
        return False, "recovery_sfn_exception"

    # Update state to STARTED with recovered ARN
    update_req = build_event_workflow_started_update_request(
        table_name=table_name,
        delivery_id=delivery_id,
        workflow_execution_arn=recovery_arn,
        expected_workflow_input_hash=workflow_input_hash,
    )
    try:
        dynamodb.update_item(**update_req)
    except Exception:
        log_event(
            githubDeliveryId=delivery_id,
            eventType=event_type,
            installationId=installation_id,
            repositoryId=repo_id,
            sqsMessageId=sqs_msg_id,
            workflowExecutionArn=recovery_arn,
            result="dependency_error",
            errorClass="recovery_update_started_failed",
        )
        return False, "recovery_update_started_failed"

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
    log_event(
        githubDeliveryId=delivery_id,
        eventType=event_type,
        installationId=installation_id,
        repositoryId=repo_id,
        sqsMessageId=sqs_msg_id,
        workflowExecutionArn=recovery_arn,
        latencyMs=elapsed_ms,
        result="recovered_pending",
    )
    return True, ""


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    """SQS Standard consumer entry point with partial batch failure reporting."""
    if not isinstance(event, dict):
        return {"batchItemFailures": []}

    records: List[Dict[str, Any]] = event.get("Records", [])
    if not isinstance(records, list):
        return {"batchItemFailures": []}

    batch_item_failures = []
    for record in records:
        if not isinstance(record, dict):
            continue
        msg_id = record.get("messageId")
        if not msg_id:
            continue

        success, _ = _process_record(record)
        if not success:
            batch_item_failures.append({"itemIdentifier": str(msg_id)})

    return {"batchItemFailures": batch_item_failures}
