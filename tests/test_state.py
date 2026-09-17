"""Deterministic unit and adversarial tests for T03 DynamoDB Authoritative State Model & Transaction Builders."""

import json
import pytest

from src.state.models import (
    format_event_pk,
    format_issue_pk,
    format_issue_sk,
    format_lease_pk,
    format_lease_sk,
    format_qualification_sk,
    format_verification_sk,
    format_idempotency_pk,
    format_idempotency_sk,
    is_lease_active,
    build_event_admission_request,
    build_event_workflow_started_update_request,
    build_issue_creation_request,
    build_qualification_request,
    build_verification_run_request,
    build_side_effect_idempotency_request,
    build_issue_fencing_update_request,
    build_lease_acquisition_transaction,
)

TABLE_NAME = "github-evaluator-state"
INSTALLATION_ID = 111222
REPO_ID = 12345
ISSUE_NUMBER = 42
CONTRIBUTOR_ID = 67890
BASE_COMMIT_SHA = "a1b2c3d4e5f67890123456789abcdef012345678"


class TestRepositoryAndInstallationIsolation:
    def test_issues_cannot_collide_across_repositories(self):
        """Issue #42 in Repo 100 and Issue #42 in Repo 200 must have distinct PKs."""
        pk_repo_1 = format_issue_pk(100)
        pk_repo_2 = format_issue_pk(200)
        sk = format_issue_sk(42)

        assert pk_repo_1 != pk_repo_2
        assert pk_repo_1 == "REPO#100"
        assert pk_repo_2 == "REPO#200"
        assert sk == "ISSUE#42"

    def test_leases_cannot_collide_across_repositories(self):
        """Lease for Issue #42 in Repo 100 vs Repo 200 must have distinct PKs."""
        lease_pk_1 = format_lease_pk(100, 42)
        lease_pk_2 = format_lease_pk(200, 42)

        assert lease_pk_1 != lease_pk_2
        assert lease_pk_1 == "REPO#100#ISSUE#42"
        assert lease_pk_2 == "REPO#200#ISSUE#42"

    def test_installation_isolation_in_records(self):
        """Authoritative records must carry installationId for tenancy boundary verification."""
        event_req = build_event_admission_request(
            table_name=TABLE_NAME,
            delivery_id="del-uuid-001",
            event_type="issues",
            installation_id=INSTALLATION_ID,
        )
        assert event_req["Item"]["installationId"]["N"] == str(INSTALLATION_ID)

        qual_req = build_qualification_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-1",
            status="VERIFIED",
        )
        assert qual_req["Item"]["installationId"]["N"] == str(INSTALLATION_ID)


class TestEventAdmissionAtomicity:
    def test_admission_uses_atomic_condition_not_read_check_write(self):
        """Event admission must use attribute_not_exists(PK) rather than GET then PUT."""
        req = build_event_admission_request(
            table_name=TABLE_NAME,
            delivery_id="delivery-guid-001",
            event_type="issues",
            installation_id=INSTALLATION_ID,
            admitted_at=1000.0,
            ttl_seconds=86400,
        )

        assert req["TableName"] == TABLE_NAME
        assert req["ConditionExpression"] == "attribute_not_exists(PK)"
        item = req["Item"]
        assert item["PK"]["S"] == "EVENT#delivery-guid-001"
        assert item["SK"]["S"] == "METADATA"
        assert item["eventType"]["S"] == "issues"
        assert item["status"]["S"] == "ADMITTED"
        assert item["ttl"]["N"] == str(1000 + 86400)

    def test_admission_binds_t05_mandatory_fields(self):
        """T05 event admission binds deliveryId, installationId, repositoryId, bodyHash, workflowInputHash."""
        req = build_event_admission_request(
            table_name=TABLE_NAME,
            delivery_id="delivery-t05-001",
            event_type="issues",
            installation_id=INSTALLATION_ID,
            repository_id=REPO_ID,
            body_hash="sha256:abc123bodyhash",
            workflow_input_hash="hash-workflow-input-xyz",
            admitted_at=2000.0,
            ttl_seconds=86400,
        )

        assert req["TableName"] == TABLE_NAME
        assert req["ConditionExpression"] == "attribute_not_exists(PK)"
        item = req["Item"]
        assert item["PK"]["S"] == "EVENT#delivery-t05-001"
        assert item["SK"]["S"] == "METADATA"
        assert item["deliveryId"]["S"] == "delivery-t05-001"
        assert item["githubDeliveryId"]["S"] == "delivery-t05-001"
        assert item["eventType"]["S"] == "issues"
        assert item["installationId"]["N"] == str(INSTALLATION_ID)
        assert item["repositoryId"]["N"] == str(REPO_ID)
        assert item["bodyHash"]["S"] == "sha256:abc123bodyhash"
        assert item["workflowInputHash"]["S"] == "hash-workflow-input-xyz"
        assert item["status"]["S"] == "ADMITTED"
        assert item["admittedAt"]["N"] == "2000"
        assert item["ttl"]["N"] == str(2000 + 86400)


class TestEventWorkflowStartedUpdate:
    def test_started_update_enforces_workflow_input_hash_and_status(self):
        """build_event_workflow_started_update_request builds condition preventing stale/unrelated updates."""
        req = build_event_workflow_started_update_request(
            table_name=TABLE_NAME,
            delivery_id="delivery-t05-001",
            workflow_execution_arn="arn:aws:states:us-east-1:123:execution:sm:gh-exec-001",
            expected_workflow_input_hash="hash-workflow-input-xyz",
            started_at=2005.0,
        )

        assert req["TableName"] == TABLE_NAME
        assert req["Key"]["PK"]["S"] == "EVENT#delivery-t05-001"
        assert req["Key"]["SK"]["S"] == "METADATA"
        assert "SET #status = :started" in req["UpdateExpression"]
        assert "workflowExecutionArn = :arn" in req["UpdateExpression"]
        assert "workflowStartedAt = :started_at" in req["UpdateExpression"]

        cond = req["ConditionExpression"]
        assert "attribute_exists(PK)" in cond
        assert "workflowInputHash = :expected_hash" in cond
        assert "#status = :admitted" in cond
        assert "workflowExecutionArn = :arn" in cond  # idempotent branch

        vals = req["ExpressionAttributeValues"]
        assert vals[":started"]["S"] == "STARTED"
        assert vals[":admitted"]["S"] == "ADMITTED"
        assert vals[":arn"]["S"] == "arn:aws:states:us-east-1:123:execution:sm:gh-exec-001"
        assert vals[":expected_hash"]["S"] == "hash-workflow-input-xyz"
        assert vals[":started_at"]["N"] == "2005"


class TestLeaseExpirySemantics:
    def test_application_explicitly_enforces_lease_expiry(self):
        """Active check must evaluate now < expiresAt; TTL is not used for authorization."""
        now = 1000.0
        assert is_lease_active(expires_at=1050.0, current_time=now) is True
        assert is_lease_active(expires_at=950.0, current_time=now) is False
        assert is_lease_active(expires_at=1000.0, current_time=now) is False

    def test_expired_lease_with_future_ttl_is_not_authorized(self):
        """A lease that expired at T=1000 but has background cleanup TTL at T=87400
        must NOT be authorized by application logic.
        """
        now = 1050.0
        lease_expires_at = 1000.0
        assert is_lease_active(expires_at=lease_expires_at, current_time=now) is False


class TestQualificationSafetyAndDefaults:
    def test_qualification_requires_explicit_status_no_implicit_default(self):
        """A caller must explicitly specify status; implicit default VERIFIED is forbidden."""
        with pytest.raises(TypeError):
            # Missing status parameter must raise TypeError at call time
            build_qualification_request(
                table_name=TABLE_NAME,
                installation_id=INSTALLATION_ID,
                repo_id=REPO_ID,
                issue_number=ISSUE_NUMBER,
                contributor_id=CONTRIBUTOR_ID,
                base_commit_sha=BASE_COMMIT_SHA,
                qualification_id="qual-1",
            )  # type: ignore

    def test_qualification_rejects_invalid_status(self):
        """Only VERIFIED, NEEDS_REVISION, ESCALATED are valid statuses."""
        with pytest.raises(ValueError, match="Invalid qualification status"):
            build_qualification_request(
                table_name=TABLE_NAME,
                installation_id=INSTALLATION_ID,
                repo_id=REPO_ID,
                issue_number=ISSUE_NUMBER,
                contributor_id=CONTRIBUTOR_ID,
                base_commit_sha=BASE_COMMIT_SHA,
                qualification_id="qual-1",
                status="AUTOMATICALLY_APPROVED_INVALID",
            )

    def test_valid_qualification_persists_numeric_contributor_and_bindings(self):
        req = build_qualification_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-uuid-001",
            status="VERIFIED",
            created_at=2000.0,
        )

        item = req["Item"]
        assert item["contributorId"]["N"] == str(CONTRIBUTOR_ID)
        assert item["installationId"]["N"] == str(INSTALLATION_ID)
        assert item["repositoryId"]["N"] == str(REPO_ID)
        assert item["issueNumber"]["N"] == str(ISSUE_NUMBER)
        assert item["baseCommitSha"]["S"] == BASE_COMMIT_SHA
        assert item["status"]["S"] == "VERIFIED"
        assert item["consumed"]["BOOL"] is False


class TestVerificationRunProvenance:
    def test_verification_records_complete_immutable_provenance(self):
        """VerificationRun must store full evidence: filesInspected, extractedClaims,
        deterministicEvidence, modelId, promptSchemaVersion, decisionOutcome, commitSha.
        """
        files = ["src/retry.py", "tests/test_retry.py"]
        claims = [{"claim": "FILE_EXISTS", "target": "src/retry.py"}]
        evidence = [{"claim": "FILE_EXISTS", "status": "SUPPORTED", "evidence": "tree_sha"}]

        req = build_verification_run_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            verification_id="ver-uuid-001",
            files_inspected=files,
            extracted_claims=claims,
            deterministic_evidence=evidence,
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            prompt_schema_version="1.0.0",
            decision_outcome="VERIFIED",
            created_at=2000.0,
        )

        item = req["Item"]
        assert item["PK"]["S"] == f"REPO#{REPO_ID}"
        assert item["SK"]["S"] == "VER#ver-uuid-001"
        assert item["baseCommitSha"]["S"] == BASE_COMMIT_SHA
        assert item["modelId"]["S"] == "anthropic.claude-3-sonnet-20240229-v1:0"
        assert item["promptSchemaVersion"]["S"] == "1.0.0"
        assert item["decisionOutcome"]["S"] == "VERIFIED"
        assert json.loads(item["filesInspected"]["S"]) == files
        assert json.loads(item["extractedClaims"]["S"]) == claims
        assert json.loads(item["deterministicEvidence"]["S"]) == evidence


class TestSideEffectIdempotency:
    def test_side_effect_key_is_deterministic(self):
        key = f"assignment/{REPO_ID}/{ISSUE_NUMBER}/lease-001"
        req = build_side_effect_idempotency_request(
            table_name=TABLE_NAME,
            idempotency_key=key,
            target="github_assign_issue",
            installation_id=INSTALLATION_ID,
            repository_id=REPO_ID,
            created_at=3000.0,
        )

        assert req["Item"]["PK"]["S"] == "IDEMPOTENCY"
        assert req["Item"]["SK"]["S"] == f"KEY#{key}"
        assert req["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        assert req["Item"]["status"]["S"] == "PENDING"
        assert req["Item"]["installationId"]["N"] == str(INSTALLATION_ID)


class TestWorkerFencingAndMaintainerOverride:
    def test_fencing_enforces_expected_lease_version_and_installation(self):
        """Fencing condition must always check lease, version, AND installation."""
        req = build_issue_fencing_update_request(
            table_name=TABLE_NAME,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            expected_lease_id="lease-worker-001",
            expected_version=3,
            new_version=4,
            installation_id=INSTALLATION_ID,
            updated_at=4000.0,
        )

        condition = req["ConditionExpression"]
        assert "activeLeaseId = :expected_lease_id" in condition
        assert "#v = :expected_version" in condition
        assert "installationId = :installation_id" in condition

        vals = req["ExpressionAttributeValues"]
        assert vals[":installation_id"]["N"] == str(INSTALLATION_ID)

    def test_fencing_requires_installation_id_argument(self):
        """installation_id is a required argument — cannot be omitted."""
        with pytest.raises(TypeError):
            build_issue_fencing_update_request(
                table_name=TABLE_NAME,
                repo_id=REPO_ID,
                issue_number=ISSUE_NUMBER,
                expected_lease_id="lease-001",
                expected_version=1,
                new_version=2,
                # Missing installation_id — must raise TypeError
            )  # type: ignore

    def test_fencing_installation_condition_always_present(self):
        """Even for different installation IDs, the condition always checks installationId."""
        req = build_issue_fencing_update_request(
            table_name=TABLE_NAME,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            expected_lease_id="lease-xyz",
            expected_version=5,
            new_version=6,
            installation_id=999888,
        )
        assert "installationId = :installation_id" in req["ConditionExpression"]
        assert req["ExpressionAttributeValues"][":installation_id"]["N"] == "999888"


class TestIssueCreationContract:
    def test_issue_creation_uses_atomic_condition(self):
        """Issue creation must use attribute_not_exists on both PK and SK — no read-check-write."""
        req = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            created_at=5000.0,
        )
        assert req["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        assert req["TableName"] == TABLE_NAME

    def test_issue_creation_persists_required_fields(self):
        """Created Issue must contain installationId, repositoryId, issueNumber, status, version."""
        req = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            created_at=5000.0,
        )
        item = req["Item"]
        assert item["PK"]["S"] == f"REPO#{REPO_ID}"
        assert item["SK"]["S"] == f"ISSUE#{ISSUE_NUMBER}"
        assert item["installationId"]["N"] == str(INSTALLATION_ID)
        assert item["repositoryId"]["N"] == str(REPO_ID)
        assert item["issueNumber"]["N"] == str(ISSUE_NUMBER)
        assert item["status"]["S"] == "OPEN"
        assert "version" in item

    def test_issue_creation_initializes_version(self):
        """Version must be initialized to a consistent value."""
        req = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
        )
        version = int(req["Item"]["version"]["N"])
        assert version >= 1  # version = 1 is the initial contract

    def test_issue_creation_has_no_lease_fields(self):
        """New Issue must NOT have activeLeaseId, assigneeId, or leaseExpiresAt."""
        req = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
        )
        item = req["Item"]
        assert "activeLeaseId" not in item
        assert "assigneeId" not in item
        assert "leaseExpiresAt" not in item

    def test_duplicate_issue_creation_contract_uses_condition(self):
        """Same repo + same issue number should produce identical PK/SK;
        the condition prevents duplicates.
        """
        req1 = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
        )
        req2 = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
        )
        # Same PK/SK means DynamoDB condition prevents the second write
        assert req1["Item"]["PK"]["S"] == req2["Item"]["PK"]["S"]
        assert req1["Item"]["SK"]["S"] == req2["Item"]["SK"]["S"]
        assert req1["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"

    def test_different_repo_same_issue_number_has_different_pk(self):
        """Issue #42 in Repo 100 vs Repo 200 must have different PKs."""
        req_a = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=100,
            issue_number=42,
        )
        req_b = build_issue_creation_request(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=200,
            issue_number=42,
        )
        assert req_a["Item"]["PK"]["S"] != req_b["Item"]["PK"]["S"]
        assert req_a["Item"]["SK"]["S"] == req_b["Item"]["SK"]["S"]


class TestAtomicLeaseAcquisitionAndStrictBinding:
    def test_lease_acquisition_enforces_issue_existence_and_installation(self):
        """A nonexistent Issue must produce conditional write failure;
        Issue update condition must also check installationId.
        """
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-001",
            lease_id="lease-001",
        )

        issue_update = tx["TransactItems"][0]["Update"]
        cond = issue_update["ConditionExpression"]
        vals = issue_update["ExpressionAttributeValues"]
        # Must require that PK and SK already exist
        assert "attribute_exists(PK)" in cond
        assert "attribute_exists(SK)" in cond
        assert "attribute_not_exists(activeLeaseId) OR leaseExpiresAt < :now" in cond
        # Must also check installationId
        assert "installationId = :expected_installation" in cond
        assert vals[":expected_installation"]["N"] == str(INSTALLATION_ID)

    def test_qualification_binding_enforces_issue_contributor_sha_installation(self):
        """ConditionExpression must strictly bind:
        issueNumber, contributorId, baseCommitSha, installationId.
        """
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-001",
            lease_id="lease-001",
        )

        qual_update = tx["TransactItems"][1]["Update"]
        cond = qual_update["ConditionExpression"]
        vals = qual_update["ExpressionAttributeValues"]

        # 1. Issue binding: cannot authorize another issue
        assert "issueNumber = :expected_issue" in cond
        assert vals[":expected_issue"]["N"] == str(ISSUE_NUMBER)

        # 2. Contributor binding: cannot authorize another contributor
        assert "contributorId = :expected_contributor" in cond
        assert vals[":expected_contributor"]["N"] == str(CONTRIBUTOR_ID)

        # 3. Base Commit SHA binding: cannot authorize work against another SHA
        assert "baseCommitSha = :expected_base_sha" in cond
        assert vals[":expected_base_sha"]["S"] == BASE_COMMIT_SHA

        # 4. Installation binding: cannot authorize cross-tenant installation
        assert "installationId = :expected_installation" in cond
        assert vals[":expected_installation"]["N"] == str(INSTALLATION_ID)

    def test_qualification_for_issue_42_cannot_authorize_issue_43(self):
        """Adversarial: A verified qualification for Issue 42 cannot authorize Issue 43.
        The condition expression requires issueNumber == 43, causing transaction cancellation.
        """
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=43,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-for-issue-42",
            lease_id="lease-001",
        )
        qual_condition = tx["TransactItems"][1]["Update"]["ConditionExpression"]
        qual_vals = tx["TransactItems"][1]["Update"]["ExpressionAttributeValues"]
        assert "issueNumber = :expected_issue" in qual_condition
        assert qual_vals[":expected_issue"]["N"] == "43"
        # If stored qualification item has issueNumber=42, the condition evaluates False

    def test_qualification_for_contributor_a_cannot_authorize_contributor_b(self):
        """Adversarial: Qualification for Contributor 67890 cannot authorize Contributor 99999."""
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=99999,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-for-alice",
            lease_id="lease-001",
        )
        qual_condition = tx["TransactItems"][1]["Update"]["ConditionExpression"]
        qual_vals = tx["TransactItems"][1]["Update"]["ExpressionAttributeValues"]
        assert "contributorId = :expected_contributor" in qual_condition
        assert qual_vals[":expected_contributor"]["N"] == "99999"

    def test_qualification_at_sha_a_cannot_authorize_work_against_sha_b(self):
        """Adversarial: Qualification verified against SHA-A cannot authorize SHA-B."""
        sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=INSTALLATION_ID,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=sha_b,
            qualification_id="qual-at-sha-a",
            lease_id="lease-001",
        )
        qual_condition = tx["TransactItems"][1]["Update"]["ConditionExpression"]
        qual_vals = tx["TransactItems"][1]["Update"]["ExpressionAttributeValues"]
        assert "baseCommitSha = :expected_base_sha" in qual_condition
        assert qual_vals[":expected_base_sha"]["S"] == sha_b

    def test_qualification_from_installation_a_cannot_authorize_installation_b(self):
        """Adversarial: Cross-installation authorization attempt must fail condition check."""
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=999888,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-inst-a",
            lease_id="lease-001",
        )
        qual_condition = tx["TransactItems"][1]["Update"]["ConditionExpression"]
        qual_vals = tx["TransactItems"][1]["Update"]["ExpressionAttributeValues"]
        assert "installationId = :expected_installation" in qual_condition
        assert qual_vals[":expected_installation"]["N"] == "999888"

    def test_issue_update_in_lease_also_checks_installation(self):
        """FIX 2: The Issue mutation in lease acquisition must also check installationId."""
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            installation_id=777666,
            repo_id=REPO_ID,
            issue_number=ISSUE_NUMBER,
            contributor_id=CONTRIBUTOR_ID,
            base_commit_sha=BASE_COMMIT_SHA,
            qualification_id="qual-001",
            lease_id="lease-001",
        )
        issue_cond = tx["TransactItems"][0]["Update"]["ConditionExpression"]
        issue_vals = tx["TransactItems"][0]["Update"]["ExpressionAttributeValues"]
        assert "installationId = :expected_installation" in issue_cond
        assert issue_vals[":expected_installation"]["N"] == "777666"


class TestNoPlaintextSecretsInStateFixtures:
    def test_zero_secrets_in_generated_requests(self):
        """State fixtures and transaction requests must never store plaintext tokens or secrets."""
        event_req = build_event_admission_request(TABLE_NAME, "guid-1", "issues", installation_id=111)
        qual_req = build_qualification_request(
            TABLE_NAME, 111, REPO_ID, ISSUE_NUMBER, CONTRIBUTOR_ID, BASE_COMMIT_SHA, "q1", "VERIFIED"
        )
        tx_req = build_lease_acquisition_transaction(
            TABLE_NAME, 111, REPO_ID, ISSUE_NUMBER, CONTRIBUTOR_ID, BASE_COMMIT_SHA, "q1", "l1"
        )

        all_text = str(event_req) + str(qual_req) + str(tx_req)
        assert "ghp_" not in all_text
        assert "token" not in all_text.lower()
        assert "password" not in all_text.lower()
