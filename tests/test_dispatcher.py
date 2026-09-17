"""Unit, hostile regression, and local concurrency tests for T05 Dispatcher Lambda."""

import concurrent.futures
import hashlib
import json
import logging
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

import src.dispatcher.handler as dispatcher_module
from src.dispatcher.handler import (
    canonicalize_stable_workflow_input,
    compute_deterministic_execution_name,
    lambda_handler,
    reconstruct_execution_arn,
    SUPPORTED_EVENT_TYPES,
)

TABLE_NAME = "test-state-table"
STATE_MACHINE_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:EvaluationStateMachine"


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    """Setup test environment variables and default clients."""
    monkeypatch.setenv("STATE_TABLE_NAME", TABLE_NAME)
    monkeypatch.setenv("EVALUATION_STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    dispatcher_module._dynamodb_client = None
    dispatcher_module._sfn_client = None


def _make_sqs_record(body_dict: Any, message_id: str = "msg-sqs-001") -> Dict[str, Any]:
    """Helper to construct an SQS record fixture."""
    return {
        "messageId": message_id,
        "receiptHandle": f"handle-{message_id}",
        "body": json.dumps(body_dict) if not isinstance(body_dict, str) else body_dict,
        "attributes": {"ApproximateReceiveCount": "1"},
    }


def _sample_issues_message() -> Dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "githubDeliveryId": "deliv-issues-001",
        "eventType": "issues",
        "action": "opened",
        "installationId": 111,
        "repositoryId": 222,
        "senderId": 333,
        "issueNumber": 42,
        "receivedAt": 1700000000,
        "bodyHash": "sha256:1111222233334444555566667777888899990000111122223333444455556666",
    }


def _sample_comment_message() -> Dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "githubDeliveryId": "deliv-comment-001",
        "eventType": "issue_comment",
        "action": "created",
        "installationId": 111,
        "repositoryId": 222,
        "senderId": 333,
        "issueNumber": 42,
        "commentId": 98765,
        "commentBody": "I propose fixing bug in src/handler.py",
        "receivedAt": 1700000000,
        "bodyHash": "sha256:2222333344445555666677778888999900001111222233334444555566667777",
    }


def _sample_pr_message() -> Dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "githubDeliveryId": "deliv-pr-001",
        "eventType": "pull_request",
        "action": "opened",
        "installationId": 111,
        "repositoryId": 222,
        "senderId": 333,
        "pullRequestNumber": 88,
        "pullRequestHeadSha": "abcdef1234567890abcdef1234567890abcdef12",
        "receivedAt": 1700000000,
        "bodyHash": "sha256:3333444455556666777788889999000011112222333344445555666677778888",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Deterministic Execution Name, Canonical Input, and ARN Reconstruction
# ─────────────────────────────────────────────────────────────────────────────

class TestDeterministicNamingAndInput:
    def test_deterministic_execution_name_is_stable_and_bounded(self):
        deliv = "12345678-1234-1234-1234-123456789abc"
        name1 = compute_deterministic_execution_name(deliv)
        name2 = compute_deterministic_execution_name(deliv)
        assert name1 == name2
        assert name1.startswith("gh-")
        assert len(name1) == 67  # 3 + 64 <= 80
        # Different delivery yields different name
        assert name1 != compute_deterministic_execution_name("different-delivery-id")

    def test_canonical_input_excludes_volatile_received_at(self):
        msg1 = _sample_issues_message()
        msg1["receivedAt"] = 1700000000

        msg2 = _sample_issues_message()
        msg2["receivedAt"] = 1700099999  # Arrived later

        input1, hash1 = canonicalize_stable_workflow_input(msg1)
        input2, hash2 = canonicalize_stable_workflow_input(msg2)

        assert input1 == input2
        assert hash1 == hash2
        assert "receivedAt" not in input1

    def test_canonical_input_different_fields_give_different_hash(self):
        msg1 = _sample_issues_message()
        msg2 = _sample_issues_message()
        msg2["issueNumber"] = 999  # modified field

        _, hash1 = canonicalize_stable_workflow_input(msg1)
        _, hash2 = canonicalize_stable_workflow_input(msg2)
        assert hash1 != hash2

    def test_reconstruct_execution_arn_layout(self):
        sm_arn = "arn:aws:states:us-east-1:123456789012:stateMachine:EvaluationStateMachine"
        exec_name = "gh-123456"
        expected = "arn:aws:states:us-east-1:123456789012:execution:EvaluationStateMachine:gh-123456"
        assert reconstruct_execution_arn(sm_arn, exec_name) == expected


# ─────────────────────────────────────────────────────────────────────────────
# 2. Defensive SQS Message Schema Validation & Poison Message Isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestDefensiveMessageValidation:
    def test_malformed_json_body_returns_batch_item_failure(self):
        record = _make_sqs_record("invalid json {", message_id="poison-1")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-1"}]

    def test_non_object_json_body_returns_batch_item_failure(self):
        record = _make_sqs_record(["array", "not", "object"], message_id="poison-2")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-2"}]

    def test_wrong_schema_version_returns_batch_item_failure(self):
        msg = _sample_issues_message()
        msg["schemaVersion"] = "2.0.0"
        record = _make_sqs_record(msg, message_id="poison-3")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-3"}]

    def test_unsupported_event_type_returns_batch_item_failure(self):
        msg = _sample_issues_message()
        msg["eventType"] = "deployment"
        record = _make_sqs_record(msg, message_id="poison-4")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-4"}]

    def test_missing_required_identifiers_returns_batch_item_failure(self):
        for field in ["githubDeliveryId", "installationId", "repositoryId", "senderId", "bodyHash"]:
            msg = _sample_issues_message()
            del msg[field]
            record = _make_sqs_record(msg, message_id=f"poison-{field}")
            res = lambda_handler({"Records": [record]}, None)
            assert res["batchItemFailures"] == [{"itemIdentifier": f"poison-{field}"}]

    def test_issues_missing_issue_number_returns_batch_failure(self):
        msg = _sample_issues_message()
        del msg["issueNumber"]
        record = _make_sqs_record(msg, message_id="poison-issue-num")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-issue-num"}]

    def test_comment_missing_comment_id_returns_batch_failure(self):
        msg = _sample_comment_message()
        del msg["commentId"]
        record = _make_sqs_record(msg, message_id="poison-comment-id")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-comment-id"}]

    def test_comment_oversize_body_returns_batch_failure(self):
        msg = _sample_comment_message()
        msg["commentBody"] = "X" * (65536 + 10)
        record = _make_sqs_record(msg, message_id="poison-comment-oversize")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-comment-oversize"}]

    def test_pr_missing_head_sha_returns_batch_failure(self):
        msg = _sample_pr_message()
        del msg["pullRequestHeadSha"]
        record = _make_sqs_record(msg, message_id="poison-pr-sha")
        res = lambda_handler({"Records": [record]}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "poison-pr-sha"}]


# ─────────────────────────────────────────────────────────────────────────────
# 3. First Delivery Normal Path (New Admission + Step Functions Start)
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstDeliveryNormalPath:
    def test_valid_issues_message_starts_workflow_and_updates_started(self, monkeypatch):
        mock_dynamo = Mock()
        mock_sfn = Mock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123:execution:EvaluationStateMachine:gh-exec-1"
        }
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        msg = _sample_issues_message()
        record = _make_sqs_record(msg, message_id="sqs-normal-1")
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == []

        # 1. DynamoDB PutItem called with atomic condition
        assert mock_dynamo.put_item.call_count == 1
        put_call = mock_dynamo.put_item.call_args[1]
        assert put_call["TableName"] == TABLE_NAME
        assert put_call["ConditionExpression"] == "attribute_not_exists(PK)"
        assert put_call["Item"]["PK"]["S"] == f"EVENT#{msg['githubDeliveryId']}"
        assert put_call["Item"]["status"]["S"] == "ADMITTED"

        # 2. Step Functions StartExecution called with deterministic name
        assert mock_sfn.start_execution.call_count == 1
        sfn_call = mock_sfn.start_execution.call_args[1]
        assert sfn_call["stateMachineArn"] == STATE_MACHINE_ARN
        assert sfn_call["name"] == compute_deterministic_execution_name(msg["githubDeliveryId"])
        assert "receivedAt" not in sfn_call["input"]

        # 3. DynamoDB UpdateItem called with STARTED status
        assert mock_dynamo.update_item.call_count == 1
        update_call = mock_dynamo.update_item.call_args[1]
        assert update_call["Key"]["PK"]["S"] == f"EVENT#{msg['githubDeliveryId']}"
        assert "SET #status = :started" in update_call["UpdateExpression"]
        assert update_call["ExpressionAttributeValues"][":started"]["S"] == "STARTED"

    def test_valid_issue_comment_and_pull_request_events_flow(self, monkeypatch):
        mock_dynamo = Mock()
        mock_sfn = Mock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test:exec"}
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        for sample in [_sample_comment_message(), _sample_pr_message()]:
            record = _make_sqs_record(sample)
            res = lambda_handler({"Records": [record]}, None)
            assert res["batchItemFailures"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Duplicate Deliveries & Recovery Scenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateAndCrashRecovery:
    def test_duplicate_already_started_is_cheaply_acknowledged(self, monkeypatch):
        """Duplicate delivery where DynamoDB already records STARTED status:
        Acknowledged immediately with 0 new Step Functions calls and 0 DynamoDB updates.
        """
        msg = _sample_issues_message()
        _, w_hash = canonicalize_stable_workflow_input(msg)

        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        mock_dynamo.get_item.return_value = {
            "Item": {
                "PK": {"S": f"EVENT#{msg['githubDeliveryId']}"},
                "SK": {"S": "METADATA"},
                "status": {"S": "STARTED"},
                "workflowExecutionArn": {"S": "arn:aws:states:us-east-1:123:execution:EvaluationStateMachine:existing"},
                "workflowInputHash": {"S": w_hash},
                "bodyHash": {"S": msg["bodyHash"]},
                "installationId": {"N": str(msg["installationId"])},
                "repositoryId": {"N": str(msg["repositoryId"])},
                "eventType": {"S": msg["eventType"]},
            }
        }
        mock_sfn = Mock()
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        record = _make_sqs_record(msg)
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == []
        assert mock_sfn.start_execution.call_count == 0
        assert mock_dynamo.update_item.call_count == 0

    def test_crash_before_startexecution_recovery(self, monkeypatch):
        """Crash after DynamoDB admission, before StartExecution:
        On retry, dispatcher reads ADMITTED status, calls StartExecution, updates to STARTED.
        """
        msg = _sample_issues_message()
        _, w_hash = canonicalize_stable_workflow_input(msg)

        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        mock_dynamo.get_item.return_value = {
            "Item": {
                "PK": {"S": f"EVENT#{msg['githubDeliveryId']}"},
                "SK": {"S": "METADATA"},
                "status": {"S": "ADMITTED"},
                "workflowInputHash": {"S": w_hash},
                "bodyHash": {"S": msg["bodyHash"]},
                "installationId": {"N": str(msg["installationId"])},
                "repositoryId": {"N": str(msg["repositoryId"])},
                "eventType": {"S": msg["eventType"]},
            }
        }
        mock_sfn = Mock()
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123:execution:EvaluationStateMachine:recovered-1"
        }
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        record = _make_sqs_record(msg)
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == []
        assert mock_sfn.start_execution.call_count == 1
        assert mock_dynamo.update_item.call_count == 1
        update_call = mock_dynamo.update_item.call_args[1]
        assert update_call["ExpressionAttributeValues"][":arn"]["S"] == "arn:aws:states:us-east-1:123:execution:EvaluationStateMachine:recovered-1"

    def test_crash_after_startexecution_and_execution_already_exists_reconstruction(self, monkeypatch):
        """StartExecution succeeded in prior attempt, workflow finished quickly, DDB update failed.
        On retry, start_execution raises ExecutionAlreadyExists. Dispatcher reconstructs ARN and marks STARTED.
        """
        msg = _sample_issues_message()
        _, w_hash = canonicalize_stable_workflow_input(msg)

        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        mock_dynamo.get_item.return_value = {
            "Item": {
                "PK": {"S": f"EVENT#{msg['githubDeliveryId']}"},
                "SK": {"S": "METADATA"},
                "status": {"S": "ADMITTED"},
                "workflowInputHash": {"S": w_hash},
                "bodyHash": {"S": msg["bodyHash"]},
                "installationId": {"N": str(msg["installationId"])},
                "repositoryId": {"N": str(msg["repositoryId"])},
                "eventType": {"S": msg["eventType"]},
            }
        }
        mock_sfn = Mock()
        mock_sfn.start_execution.side_effect = ClientError(
            {"Error": {"Code": "ExecutionAlreadyExists", "Message": "Already running or closed"}},
            "StartExecution",
        )
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        record = _make_sqs_record(msg)
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == []
        assert mock_dynamo.update_item.call_count == 1
        update_call = mock_dynamo.update_item.call_args[1]
        expected_arn = reconstruct_execution_arn(STATE_MACHINE_ARN, compute_deterministic_execution_name(msg["githubDeliveryId"]))
        assert update_call["ExpressionAttributeValues"][":arn"]["S"] == expected_arn

    def test_new_admission_with_unexpected_execution_already_exists_fails_closed(self, monkeypatch):
        """Brand new event admission immediately encounters ExecutionAlreadyExists:
        Treated as an anomaly/collision and returned as a batch failure (not silently accepted).
        """
        mock_dynamo = Mock()
        # PutItem succeeds (brand new event!)
        mock_dynamo.put_item.return_value = {}

        mock_sfn = Mock()
        mock_sfn.start_execution.side_effect = ClientError(
            {"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution"
        )
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        msg = _sample_issues_message()
        record = _make_sqs_record(msg, message_id="anomaly-msg-1")
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == [{"itemIdentifier": "anomaly-msg-1"}]
        # Must NOT have updated to STARTED
        assert mock_dynamo.update_item.call_count == 0

    def test_payload_collision_on_same_delivery_id_rejected(self, monkeypatch, caplog):
        """Same delivery ID with different bodyHash:
        Must NOT call StartExecution, must NOT update DynamoDB, must return batch failure.
        """
        msg = _sample_issues_message()
        _, w_hash = canonicalize_stable_workflow_input(msg)

        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        mock_dynamo.get_item.return_value = {
            "Item": {
                "PK": {"S": f"EVENT#{msg['githubDeliveryId']}"},
                "SK": {"S": "METADATA"},
                "status": {"S": "ADMITTED"},
                "workflowInputHash": {"S": w_hash},
                "bodyHash": {"S": "sha256:different-body-hash-999"},  # MISMATCH
                "installationId": {"N": str(msg["installationId"])},
                "repositoryId": {"N": str(msg["repositoryId"])},
                "eventType": {"S": msg["eventType"]},
            }
        }
        mock_sfn = Mock()
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        record = _make_sqs_record(msg, message_id="collision-msg")
        with caplog.at_level(logging.INFO):
            res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == [{"itemIdentifier": "collision-msg"}]
        assert mock_sfn.start_execution.call_count == 0
        assert mock_dynamo.update_item.call_count == 0

        # Verify safe telemetry logged
        logs = [json.loads(r.message) for r in caplog.records]
        assert any(l.get("result") == "delivery_collision" for l in logs)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Partial Batch Failure & Transient Error Handling
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialBatchFailureAndTransientErrors:
    def test_mixed_batch_returns_only_failed_item_identifier(self, monkeypatch):
        """Batch: [Valid Msg A, Poison Msg B (malformed JSON), Valid Msg C].
        Only Msg B must be in batchItemFailures. Msg A and C succeed.
        """
        mock_dynamo = Mock()
        mock_sfn = Mock()
        mock_sfn.start_execution.return_value = {"executionArn": "arn:test"}
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        msg_a = _sample_issues_message()
        msg_a["githubDeliveryId"] = "deliv-A"
        msg_c = _sample_comment_message()
        msg_c["githubDeliveryId"] = "deliv-C"

        records = [
            _make_sqs_record(msg_a, message_id="msg-A"),
            _make_sqs_record("bad json {", message_id="msg-B"),
            _make_sqs_record(msg_c, message_id="msg-C"),
        ]

        res = lambda_handler({"Records": records}, None)
        assert res["batchItemFailures"] == [{"itemIdentifier": "msg-B"}]

    def test_transient_dynamodb_put_failure_marks_record_failed(self, monkeypatch):
        mock_dynamo = Mock()
        mock_dynamo.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "PutItem"
        )
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)

        msg = _sample_issues_message()
        record = _make_sqs_record(msg, message_id="transient-ddb")
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == [{"itemIdentifier": "transient-ddb"}]

    def test_transient_sfn_start_failure_marks_record_failed(self, monkeypatch):
        mock_dynamo = Mock()
        mock_sfn = Mock()
        mock_sfn.start_execution.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException"}}, "StartExecution"
        )
        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        msg = _sample_issues_message()
        record = _make_sqs_record(msg, message_id="transient-sfn")
        res = lambda_handler({"Records": [record]}, None)

        assert res["batchItemFailures"] == [{"itemIdentifier": "transient-sfn"}]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Local Concurrency Simulation (10 Concurrent Duplicates)
# ─────────────────────────────────────────────────────────────────────────────

class ThreadSafeMockDynamo:
    """Thread-safe in-memory mock for DynamoDB state table simulating conditional write semantics."""

    def __init__(self):
        self._lock = threading.Lock()
        self.items: Dict[str, Dict[str, Any]] = {}
        self.put_count = 0
        self.update_count = 0

    def put_item(self, **kwargs):
        with self._lock:
            self.put_count += 1
            pk = kwargs["Item"]["PK"]["S"]
            if pk in self.items:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
                )
            self.items[pk] = kwargs["Item"]
            return {}

    def get_item(self, **kwargs):
        with self._lock:
            pk = kwargs["Key"]["PK"]["S"]
            return {"Item": self.items.get(pk)}

    def update_item(self, **kwargs):
        with self._lock:
            self.update_count += 1
            pk = kwargs["Key"]["PK"]["S"]
            if pk in self.items:
                self.items[pk]["status"] = {"S": "STARTED"}
                self.items[pk]["workflowExecutionArn"] = kwargs["ExpressionAttributeValues"][":arn"]
            return {}


class ThreadSafeMockSFN:
    """Thread-safe in-memory mock for Step Functions Standard execution."""

    def __init__(self):
        self._lock = threading.Lock()
        self.executions: Dict[str, str] = {}
        self.call_count = 0

    def start_execution(self, **kwargs):
        with self._lock:
            self.call_count += 1
            name = kwargs["name"]
            if name in self.executions:
                # Standard Step Functions idempotency for same name while running
                return {"executionArn": self.executions[name]}
            arn = f"arn:aws:states:us-east-1:123456789012:execution:EvaluationStateMachine:{name}"
            self.executions[name] = arn
            return {"executionArn": arn}


class TestLocalConcurrencySimulation:
    def test_ten_concurrent_duplicates_produce_one_event_and_one_execution(self, monkeypatch):
        """LOCAL CONCURRENCY SIMULATION:
        10 concurrent threads process identical delivery ID.
        Proves:
        - exactly 1 atomic DynamoDB EVENT record
        - exactly 1 logical Step Functions execution
        - all 10 threads succeed without poison failures
        """
        mock_dynamo = ThreadSafeMockDynamo()
        mock_sfn = ThreadSafeMockSFN()

        monkeypatch.setattr(dispatcher_module, "_get_dynamodb_client", lambda: mock_dynamo)
        monkeypatch.setattr(dispatcher_module, "_get_sfn_client", lambda: mock_sfn)

        msg = _sample_issues_message()
        msg["githubDeliveryId"] = "concurrent-deliv-001"

        def run_worker(thread_idx: int):
            record = _make_sqs_record(msg, message_id=f"sqs-thread-{thread_idx}")
            return lambda_handler({"Records": [record]}, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(run_worker, i) for i in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All 10 invocations must succeed (batchItemFailures empty)
        for res in results:
            assert res["batchItemFailures"] == []

        # Exactly ONE EVENT record exists in the table
        assert len(mock_dynamo.items) == 1
        stored = mock_dynamo.items[f"EVENT#{msg['githubDeliveryId']}"]
        assert stored["status"]["S"] == "STARTED"

        # Exactly ONE logical execution exists in Step Functions
        assert len(mock_sfn.executions) == 1
        expected_exec_name = compute_deterministic_execution_name(msg["githubDeliveryId"])
        assert expected_exec_name in mock_sfn.executions


# ─────────────────────────────────────────────────────────────────────────────
# 7. IAM Least Privilege Static Assertions for DispatcherFunction
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatcherIamLeastPrivilege:
    def test_dispatcher_iam_in_template(self):
        """Verify DispatcherFunction IAM policy in template.yaml has strictly least-privilege permissions."""
        with open("template.yaml", "r", encoding="utf-8") as f:
            content = f.read()

        assert "DispatcherFunction:" in content
        assert "EvaluationStateMachine:" in content

        dispatcher_block = content.split("DispatcherFunction:")[1].split("Outputs:")[0]

        # Allowed actions
        assert "sqs:ReceiveMessage" in dispatcher_block
        assert "sqs:DeleteMessage" in dispatcher_block
        assert "sqs:GetQueueAttributes" in dispatcher_block
        assert "dynamodb:PutItem" in dispatcher_block
        assert "dynamodb:GetItem" in dispatcher_block
        assert "dynamodb:UpdateItem" in dispatcher_block
        assert "states:StartExecution" in dispatcher_block

        # Forbidden wildcards and cross-service permissions
        assert 'Resource: "*"' not in dispatcher_block
        assert "secretsmanager:" not in dispatcher_block
        assert "bedrock:" not in dispatcher_block
        assert "s3:" not in dispatcher_block
        assert "dynamodb:*" not in dispatcher_block
        assert "states:*" not in dispatcher_block
        assert "sqs:*" not in dispatcher_block

        # Standard Step Functions type configured
        assert "Type: STANDARD" in content
        assert "ReportBatchItemFailures" in dispatcher_block
