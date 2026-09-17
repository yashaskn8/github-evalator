"""Authoritative DynamoDB State Model & Transaction Builders (T03).

Core Directives:
1. DynamoDB is authoritative for internal state; GitHub remains external system of record.
2. All state transitions must be tenant-bound by repositoryId / installationId.
3. No Read-Check-Write: Atomic conditional writes or TransactWriteItems gate state.
4. DynamoDB TTL is background cleanup only; application logic explicitly checks expiration.
5. All external side-effects use deterministic idempotency keys.
6. Contributor identity uses stable numeric GitHub user IDs.
"""

import json
import time
from typing import Any, Dict, List, Optional

VALID_QUALIFICATION_STATUSES = frozenset({"VERIFIED", "NEEDS_REVISION", "ESCALATED"})


# ─────────────────────────────────────────────────────────────────────────────
# Key Formatting & Tenant Isolation Helpers
# ─────────────────────────────────────────────────────────────────────────────

def format_event_pk(delivery_id: str) -> str:
    """Format partition key for webhook event admission."""
    return f"EVENT#{delivery_id}"


def format_issue_pk(repo_id: int | str) -> str:
    """Format partition key for an issue, strictly scoped to repositoryId."""
    return f"REPO#{repo_id}"


def format_issue_sk(issue_number: int | str) -> str:
    """Format sort key for an issue."""
    return f"ISSUE#{issue_number}"


def format_lease_pk(repo_id: int | str, issue_number: int | str) -> str:
    """Format partition key for an issue lease, scoped to repositoryId and issueNumber."""
    return f"REPO#{repo_id}#ISSUE#{issue_number}"


def format_lease_sk(lease_id: str) -> str:
    """Format sort key for a lease record."""
    return f"LEASE#{lease_id}"


def format_qualification_sk(qualification_id: str) -> str:
    """Format sort key for a qualification record."""
    return f"QUAL#{qualification_id}"


def format_verification_sk(verification_id: str) -> str:
    """Format sort key for a verification run record."""
    return f"VER#{verification_id}"


def format_idempotency_pk() -> str:
    """Format partition key for side-effect idempotency records."""
    return "IDEMPOTENCY"


def format_idempotency_sk(idempotency_key: str) -> str:
    """Format sort key for a deterministic side-effect record."""
    return f"KEY#{idempotency_key}"


# ─────────────────────────────────────────────────────────────────────────────
# Application-Level Authorization & Expiry Validation
# ─────────────────────────────────────────────────────────────────────────────

def is_lease_active(expires_at: float, current_time: Optional[float] = None) -> bool:
    """Application-enforced lease expiry check.
    
    DynamoDB TTL is strictly for garbage collection and MUST NOT be used
    for authoritative authorization logic.
    """
    now = current_time if current_time is not None else time.time()
    return now < expires_at


# ─────────────────────────────────────────────────────────────────────────────
# Request Builders for Conditional Writes & Transactions
# ─────────────────────────────────────────────────────────────────────────────

def build_event_admission_request(
    table_name: str,
    delivery_id: str,
    event_type: str,
    installation_id: Optional[int] = None,
    admitted_at: Optional[float] = None,
    ttl_seconds: int = 86400,
) -> Dict[str, Any]:
    """Build atomic PutItem request for webhook event admission.
    
    Condition: attribute_not_exists(PK) guarantees duplicate deliveries are
    safely rejected without race conditions.
    """
    now = admitted_at if admitted_at is not None else time.time()
    pk = format_event_pk(delivery_id)
    item: Dict[str, Any] = {
        "PK": {"S": pk},
        "SK": {"S": "METADATA"},
        "deliveryId": {"S": delivery_id},
        "eventType": {"S": event_type},
        "admittedAt": {"N": str(int(now))},
        "status": {"S": "ADMITTED"},
        "ttl": {"N": str(int(now + ttl_seconds))},
    }
    if installation_id is not None:
        item["installationId"] = {"N": str(installation_id)}

    return {
        "TableName": table_name,
        "Item": item,
        "ConditionExpression": "attribute_not_exists(PK)",
    }


def build_qualification_request(
    table_name: str,
    installation_id: int,
    repo_id: int,
    issue_number: int,
    contributor_id: int,
    base_commit_sha: str,
    qualification_id: str,
    status: str,  # Required argument: no implicit default allowed!
    created_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Build atomic PutItem request for a contributor qualification.
    
    Strictly binds (installationId, repoId, issueNumber, contributorId, baseCommitSha).
    Requires explicit valid status; raises ValueError on invalid status.
    """
    if status not in VALID_QUALIFICATION_STATUSES:
        raise ValueError(
            f"Invalid qualification status '{status}'. Must be one of {sorted(VALID_QUALIFICATION_STATUSES)}"
        )

    now = created_at if created_at is not None else time.time()
    pk = format_issue_pk(repo_id)
    sk = format_qualification_sk(qualification_id)
    return {
        "TableName": table_name,
        "Item": {
            "PK": {"S": pk},
            "SK": {"S": sk},
            "installationId": {"N": str(installation_id)},
            "repositoryId": {"N": str(repo_id)},
            "issueNumber": {"N": str(issue_number)},
            "contributorId": {"N": str(contributor_id)},
            "baseCommitSha": {"S": base_commit_sha},
            "qualificationId": {"S": qualification_id},
            "status": {"S": status},
            "consumed": {"BOOL": False},
            "createdAt": {"N": str(int(now))},
        },
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
    }


def build_verification_run_request(
    table_name: str,
    installation_id: int,
    repo_id: int,
    issue_number: int,
    contributor_id: int,
    base_commit_sha: str,
    verification_id: str,
    files_inspected: List[str],
    extracted_claims: List[Any],
    deterministic_evidence: List[Any],
    model_id: str,
    prompt_schema_version: str,
    decision_outcome: str,
    created_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Build PutItem request recording complete immutable verification provenance.
    
    Preserves: filesInspected, extractedClaims, deterministicEvidence, modelId,
    promptSchemaVersion, decisionOutcome, commitSha. No private repository source code stored.
    """
    now = created_at if created_at is not None else time.time()
    pk = format_issue_pk(repo_id)
    sk = format_verification_sk(verification_id)
    return {
        "TableName": table_name,
        "Item": {
            "PK": {"S": pk},
            "SK": {"S": sk},
            "installationId": {"N": str(installation_id)},
            "repositoryId": {"N": str(repo_id)},
            "issueNumber": {"N": str(issue_number)},
            "contributorId": {"N": str(contributor_id)},
            "baseCommitSha": {"S": base_commit_sha},
            "verificationId": {"S": verification_id},
            "filesInspected": {"S": json.dumps(files_inspected)},
            "extractedClaims": {"S": json.dumps(extracted_claims)},
            "deterministicEvidence": {"S": json.dumps(deterministic_evidence)},
            "modelId": {"S": model_id},
            "promptSchemaVersion": {"S": prompt_schema_version},
            "decisionOutcome": {"S": decision_outcome},
            "createdAt": {"N": str(int(now))},
        },
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
    }


def build_side_effect_idempotency_request(
    table_name: str,
    idempotency_key: str,
    target: str,
    installation_id: Optional[int] = None,
    repository_id: Optional[int] = None,
    ttl_seconds: int = 604800,
    created_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Build atomic PutItem request registering external side-effect idempotency."""
    now = created_at if created_at is not None else time.time()
    pk = format_idempotency_pk()
    sk = format_idempotency_sk(idempotency_key)
    item: Dict[str, Any] = {
        "PK": {"S": pk},
        "SK": {"S": sk},
        "idempotencyKey": {"S": idempotency_key},
        "target": {"S": target},
        "status": {"S": "PENDING"},
        "createdAt": {"N": str(int(now))},
        "ttl": {"N": str(int(now + ttl_seconds))},
    }
    if installation_id is not None:
        item["installationId"] = {"N": str(installation_id)}
    if repository_id is not None:
        item["repositoryId"] = {"N": str(repository_id)}

    return {
        "TableName": table_name,
        "Item": item,
        "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
    }


def build_issue_fencing_update_request(
    table_name: str,
    repo_id: int,
    issue_number: int,
    expected_lease_id: str,
    expected_version: int,
    new_version: int,
    installation_id: Optional[int] = None,
    updated_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Build UpdateItem request enforcing worker lease and version fencing.
    
    Fails closed if activeLeaseId or version has changed (e.g. maintainer override
    or reassigned lease).
    """
    now = updated_at if updated_at is not None else time.time()
    pk = format_issue_pk(repo_id)
    sk = format_issue_sk(issue_number)
    condition = "activeLeaseId = :expected_lease_id AND #v = :expected_version"
    expr_vals: Dict[str, Any] = {
        ":expected_lease_id": {"S": expected_lease_id},
        ":expected_version": {"N": str(expected_version)},
        ":new_version": {"N": str(new_version)},
        ":updated_at": {"N": str(int(now))},
    }
    if installation_id is not None:
        condition += " AND installationId = :installation_id"
        expr_vals[":installation_id"] = {"N": str(installation_id)}

    return {
        "TableName": table_name,
        "Key": {
            "PK": {"S": pk},
            "SK": {"S": sk},
        },
        "UpdateExpression": "SET #v = :new_version, #u = :updated_at",
        "ConditionExpression": condition,
        "ExpressionAttributeNames": {
            "#v": "version",
            "#u": "updatedAt",
        },
        "ExpressionAttributeValues": expr_vals,
    }


def build_lease_acquisition_transaction(
    table_name: str,
    installation_id: int,
    repo_id: int,
    issue_number: int,
    contributor_id: int,
    base_commit_sha: str,
    qualification_id: str,
    lease_id: str,
    duration_seconds: int = 86400,
    current_time: Optional[float] = None,
) -> Dict[str, Any]:
    """Build atomic TransactWriteItems request acquiring an exclusive issue lease.
    
    Guarantees:
    1. Issue entity MUST ALREADY EXIST (attribute_exists(PK) AND attribute_exists(SK))
       AND must be unassigned OR existing lease has expired.
    2. Qualification MUST MATCH EXACTLY (issueNumber, contributorId, baseCommitSha, installationId),
       must be VERIFIED, and must be unconsumed.
    3. Issue ownership and version increment atomically.
    4. Qualification is marked consumed with boundLeaseId.
    5. Lease record is created with acquiredAt, expiresAt, and installationId.
    """
    now = current_time if current_time is not None else time.time()
    expires_at = now + duration_seconds

    issue_pk = format_issue_pk(repo_id)
    issue_sk = format_issue_sk(issue_number)

    qual_pk = format_issue_pk(repo_id)
    qual_sk = format_qualification_sk(qualification_id)

    lease_pk = format_lease_pk(repo_id, issue_number)
    lease_sk = format_lease_sk(lease_id)

    return {
        "TransactItems": [
            # 1. Update Issue with lease acquisition & version increment
            # Requires that the Issue already exists (attribute_exists)
            {
                "Update": {
                    "TableName": table_name,
                    "Key": {
                        "PK": {"S": issue_pk},
                        "SK": {"S": issue_sk},
                    },
                    "UpdateExpression": (
                        "SET activeLeaseId = :lease_id, "
                        "assigneeId = :contributor_id, "
                        "leaseExpiresAt = :expires_at, "
                        "updatedAt = :now, "
                        "#v = if_not_exists(#v, :zero) + :one"
                    ),
                    "ConditionExpression": (
                        "attribute_exists(PK) AND attribute_exists(SK) AND "
                        "(attribute_not_exists(activeLeaseId) OR leaseExpiresAt < :now)"
                    ),
                    "ExpressionAttributeNames": {
                        "#v": "version",
                    },
                    "ExpressionAttributeValues": {
                        ":lease_id": {"S": lease_id},
                        ":contributor_id": {"N": str(contributor_id)},
                        ":expires_at": {"N": str(int(expires_at))},
                        ":now": {"N": str(int(now))},
                        ":zero": {"N": "0"},
                        ":one": {"N": "1"},
                    },
                }
            },
            # 2. Mark Qualification as consumed
            # Requires exact binding to: issueNumber, contributorId, baseCommitSha, installationId
            {
                "Update": {
                    "TableName": table_name,
                    "Key": {
                        "PK": {"S": qual_pk},
                        "SK": {"S": qual_sk},
                    },
                    "UpdateExpression": "SET consumed = :true, consumedAt = :now, boundLeaseId = :lease_id",
                    "ConditionExpression": (
                        "#s = :verified AND "
                        "(attribute_not_exists(consumed) OR consumed = :false) AND "
                        "issueNumber = :expected_issue AND "
                        "contributorId = :expected_contributor AND "
                        "baseCommitSha = :expected_base_sha AND "
                        "installationId = :expected_installation"
                    ),
                    "ExpressionAttributeNames": {
                        "#s": "status",
                    },
                    "ExpressionAttributeValues": {
                        ":verified": {"S": "VERIFIED"},
                        ":true": {"BOOL": True},
                        ":false": {"BOOL": False},
                        ":now": {"N": str(int(now))},
                        ":lease_id": {"S": lease_id},
                        ":expected_issue": {"N": str(issue_number)},
                        ":expected_contributor": {"N": str(contributor_id)},
                        ":expected_base_sha": {"S": base_commit_sha},
                        ":expected_installation": {"N": str(installation_id)},
                    },
                }
            },
            # 3. Put new Lease entity
            {
                "Put": {
                    "TableName": table_name,
                    "Item": {
                        "PK": {"S": lease_pk},
                        "SK": {"S": lease_sk},
                        "leaseId": {"S": lease_id},
                        "installationId": {"N": str(installation_id)},
                        "repositoryId": {"N": str(repo_id)},
                        "issueNumber": {"N": str(issue_number)},
                        "contributorId": {"N": str(contributor_id)},
                        "qualificationId": {"S": qualification_id},
                        "acquiredAt": {"N": str(int(now))},
                        "expiresAt": {"N": str(int(expires_at))},
                        "status": {"S": "ACTIVE"},
                        "version": {"N": "1"},
                        "ttl": {"N": str(int(expires_at + 86400))},
                    },
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ]
    }
