# 06 — DynamoDB Authoritative State & Leases

> **CORE LAW**: DynamoDB holds absolute authority over issue assignments and leases. Read-check-write patterns on authoritative state are strictly forbidden.

---

## 1. Domain Entities & Single-Table Keys

| Entity | Partition Key (PK) | Sort Key (SK) | Core Attributes |
| :--- | :--- | :--- | :--- |
| **Issue** | `REPO#<repoId>` | `ISSUE#<issueNum>` | `status`, `activeLeaseId`, `assigneeId`, `leaseExpiresAt`, `version` |
| **Lease** | `REPO#<repoId>#ISSUE#<issueNum>` | `LEASE#<leaseId>` | `contributorId`, `status`, `leaseExpiresAt`, `qualificationId` |
| **Qualification** | `REPO#<repoId>` | `QUAL#<qualId>` | `issueNum`, `contributorId`, `commitSha`, `status`, `consumedAt` |
| **VerificationRun** | `REPO#<repoId>` | `VER#<verId>` | `issueNum`, `claims`, `evidence`, `verdict`, `timestamp` |
| **SideEffect** | `IDEMPOTENCY` | `KEY#<idempotencyKey>` | `target`, `status`, `executedAt`, `responsePayload` |

---

## 2. Atomic Transitions for Authoritative State

All authoritative, concurrency-sensitive state transitions (lease acquisition, renewal, revocation, qualification consumption, issue ownership, stale-worker fencing, maintainer override handling) MUST use `TransactWriteItems` or conditional writes.

### Atomic Lease Acquisition
1. **Condition on Issue**: `attribute_not_exists(activeLeaseId) OR leaseExpiresAt < :now`.
2. **Condition on Qualification**: `status = :verified AND attribute_not_exists(consumedAt)`.
3. **Action on Issue**: Set `activeLeaseId = :newLeaseId`, `assigneeId = :contributorId`, `leaseExpiresAt = :expiresAt`, increment `version`.
4. **Action on Qualification**: Set `consumedAt = :now`, `boundLeaseId = :newLeaseId`.
5. **Action on Lease**: Put new `Lease` record with unique generation ID.

```text
FORBIDDEN ANTI-PATTERN:
  issue = dynamodb.get_item(...)
  if not issue.get('assigneeId'):
      dynamodb.put_item(...) # RACE CONDITION: Concurrently executing worker can assign in between
```

---

## 3. Stale-Worker Fencing & Maintainer Override Protection

Every subsequent mutation to an active lease or issue MUST enforce version fencing:
```text
ConditionExpression: "activeLeaseId = :expectedLeaseId AND version = :expectedVersion"
```
- **Stale Worker Protection**: If a worker suffers a network delay or execution pause, and the lease is reassigned in the interim, its subsequent write fails immediately.
- **Maintainer Override Protection**: When a maintainer manually reassigns or revokes an issue, the `version` increments and `activeLeaseId` changes. Any delayed automated workflow execution fails its condition and halts.

---

## 4. Lease Expiry Semantics

- **Authoritative Authorization Logic**: All backend policy code MUST compare `now() < leaseExpiresAt`.
- **DynamoDB TTL**: The `ttl` attribute is strictly for low-cost background garbage collection. Never rely on TTL deletion for authorization logic.
