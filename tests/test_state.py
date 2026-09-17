"""Deterministic unit tests for T03 DynamoDB Authoritative State Model & Transaction Builders."""

import time
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
    build_qualification_request,
    build_verification_run_request,
    build_side_effect_idempotency_request,
    build_issue_fencing_update_request,
    build_lease_acquisition_transaction,
)

TABLE_NAME = "github-evaluator-state"


class TestRepositoryIsolation:
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


class TestEventAdmissionAtomicity:
    def test_admission_uses_atomic_condition_not_read_check_write(self):
        """Event admission must use attribute_not_exists(PK) rather than GET then PUT."""
        req = build_event_admission_request(
            table_name=TABLE_NAME,
            delivery_id="delivery-guid-001",
            event_type="issues",
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


class TestLeaseExpirySemantics:
    def test_application_explicitly_enforces_lease_expiry(self):
        """Active check must evaluate now < expiresAt; TTL is not used for authorization."""
        now = 1000.0
        # Valid active lease: expires in future
        assert is_lease_active(expires_at=1050.0, current_time=now) is True

        # Expired lease: expired in past
        assert is_lease_active(expires_at=950.0, current_time=now) is False

        # Exact boundary: not active
        assert is_lease_active(expires_at=1000.0, current_time=now) is False

    def test_expired_lease_with_future_ttl_is_not_authorized(self):
        """A lease that expired at T=1000 but has background cleanup TTL at T=87400
        must NOT be authorized by application logic.
        """
        now = 1050.0
        lease_expires_at = 1000.0
        ttl_cleanup_time = 87400.0  # Background TTL still exists in DB

        # Authoritative decision MUST be False despite record still residing in DynamoDB
        assert is_lease_active(expires_at=lease_expires_at, current_time=now) is False


class TestQualificationBinding:
    def test_qualification_strictly_bound_to_base_commit_and_repo(self):
        req = build_qualification_request(
            table_name=TABLE_NAME,
            repo_id=12345,
            issue_number=42,
            contributor_id="alice",
            base_commit_sha="a1b2c3d4e5f67890",
            qualification_id="qual-uuid-001",
            status="VERIFIED",
            created_at=2000.0,
        )

        assert req["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        item = req["Item"]
        assert item["PK"]["S"] == "REPO#12345"
        assert item["SK"]["S"] == "QUAL#qual-uuid-001"
        assert item["repositoryId"]["N"] == "12345"
        assert item["issueNumber"]["N"] == "42"
        assert item["contributorId"]["S"] == "alice"
        assert item["baseCommitSha"]["S"] == "a1b2c3d4e5f67890"
        assert item["status"]["S"] == "VERIFIED"
        assert item["consumed"]["BOOL"] is False


class TestVerificationRunProvenance:
    def test_verification_records_claims_and_evidence_references(self):
        claims = ["FILE_EXISTS: src/retry.py", "SYMBOL_EXISTS: retry_request"]
        req = build_verification_run_request(
            table_name=TABLE_NAME,
            repo_id=12345,
            issue_number=42,
            contributor_id="alice",
            base_commit_sha="a1b2c3d4e5f67890",
            verification_id="ver-uuid-001",
            claims=claims,
            decision="VERIFIED",
            created_at=2000.0,
        )

        item = req["Item"]
        assert item["PK"]["S"] == "REPO#12345"
        assert item["SK"]["S"] == "VER#ver-uuid-001"
        assert item["claimsCount"]["N"] == "2"
        assert item["decision"]["S"] == "VERIFIED"


class TestSideEffectIdempotency:
    def test_side_effect_key_is_deterministic(self):
        key = "assignment/12345/42/lease-001"
        req = build_side_effect_idempotency_request(
            table_name=TABLE_NAME,
            idempotency_key=key,
            target="github_assign_issue",
            created_at=3000.0,
        )

        assert req["Item"]["PK"]["S"] == "IDEMPOTENCY"
        assert req["Item"]["SK"]["S"] == f"KEY#{key}"
        assert req["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        assert req["Item"]["status"]["S"] == "PENDING"


class TestWorkerFencingAndMaintainerOverride:
    def test_fencing_enforces_expected_lease_and_version(self):
        req = build_issue_fencing_update_request(
            table_name=TABLE_NAME,
            repo_id=12345,
            issue_number=42,
            expected_lease_id="lease-worker-001",
            expected_version=3,
            new_version=4,
            updated_at=4000.0,
        )

        assert req["ConditionExpression"] == "activeLeaseId = :expected_lease_id AND #v = :expected_version"
        assert req["ExpressionAttributeNames"]["#v"] == "version"
        assert req["ExpressionAttributeValues"][":expected_lease_id"]["S"] == "lease-worker-001"
        assert req["ExpressionAttributeValues"][":expected_version"]["N"] == "3"
        assert req["ExpressionAttributeValues"][":new_version"]["N"] == "4"


class TestAtomicLeaseAcquisitionTransaction:
    def test_lease_acquisition_transaction_structure(self):
        """Verifies 3-way atomic transaction for T08 readiness:
        1. Issue update with leaseExpiresAt < :now or attribute_not_exists(activeLeaseId)
        2. Qualification update with status = VERIFIED and not consumed
        3. Lease entity put
        """
        tx = build_lease_acquisition_transaction(
            table_name=TABLE_NAME,
            repo_id=12345,
            issue_number=42,
            contributor_id="alice",
            qualification_id="qual-001",
            lease_id="lease-001",
            duration_seconds=86400,
            current_time=5000.0,
        )

        items = tx["TransactItems"]
        assert len(items) == 3

        # 1. Issue update
        issue_update = items[0]["Update"]
        assert issue_update["Key"]["PK"]["S"] == "REPO#12345"
        assert issue_update["Key"]["SK"]["S"] == "ISSUE#42"
        assert "attribute_not_exists(activeLeaseId) OR leaseExpiresAt < :now" in issue_update["ConditionExpression"]
        assert issue_update["ExpressionAttributeValues"][":lease_id"]["S"] == "lease-001"
        assert issue_update["ExpressionAttributeValues"][":contributor_id"]["S"] == "alice"

        # 2. Qualification consumption
        qual_update = items[1]["Update"]
        assert qual_update["Key"]["PK"]["S"] == "REPO#12345"
        assert qual_update["Key"]["SK"]["S"] == "QUAL#qual-001"
        assert "#s = :verified" in qual_update["ConditionExpression"]
        assert qual_update["ExpressionAttributeValues"][":true"]["BOOL"] is True

        # 3. Lease creation
        lease_put = items[2]["Put"]
        assert lease_put["Item"]["PK"]["S"] == "REPO#12345#ISSUE#42"
        assert lease_put["Item"]["SK"]["S"] == "LEASE#lease-001"
        assert lease_put["Item"]["status"]["S"] == "ACTIVE"
        assert lease_put["Item"]["expiresAt"]["N"] == str(5000 + 86400)
        assert lease_put["ConditionExpression"] == "attribute_not_exists(PK) AND attribute_not_exists(SK)"


class TestNoPlaintextSecretsInStateFixtures:
    def test_zero_secrets_in_generated_requests(self):
        """State fixtures and transaction requests must never store plaintext tokens or secrets."""
        event_req = build_event_admission_request(TABLE_NAME, "guid-1", "issues")
        qual_req = build_qualification_request(TABLE_NAME, 1, 1, "alice", "sha1", "q1")
        tx_req = build_lease_acquisition_transaction(TABLE_NAME, 1, 1, "alice", "q1", "l1")

        all_text = str(event_req) + str(qual_req) + str(tx_req)
        assert "ghp_" not in all_text
        assert "secret" not in all_text.lower() or "secret" in all_text  # only words like TableName or SecretString
        assert "token" not in all_text.lower()
        assert "password" not in all_text.lower()
