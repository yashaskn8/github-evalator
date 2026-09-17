# 06 — DynamoDB Authoritative State & Leases

> **CORE LAW**: DynamoDB holds absolute authority over issue assignments and leases. Read-check-write patterns are strictly forbidden.

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

## 2. Atomic Lease Acquisition (Zero Read-Check-Write)

Lease acquisition MUST be executed via `TransactWriteItems` or conditional `PutItem`/`UpdateItem`.

### Transactional Requirements
1. **Condition on Issue**: Issue `activeLeaseId` does not exist OR existing `leaseExpiresAt < :now`.
2. **Condition on Qualification**: Qualification status is `VERIFIED` and `consumedAt` attribute does not exist.
3. **Action on Issue**: Set `activeLeaseId = :newLeaseId`, `assigneeId = :contributorId`, `leaseExpiresAt = :expiresAt`, increment `version`.
4. **Action on Qualification**: Set `consumedAt = :now`, `boundLeaseId = :newLeaseId`.
5. **Action on Lease**: Write new `Lease` record with generation token.

```text
FORBIDDEN ANTI-PATTERN:
  issue = dynamodb.get_item(...)
  if not issue.get('assigneeId'):
      dynamodb.put_item(...) # RACE CONDITION: Another worker can assign in between
```

---

## 3. Stale-Worker Fencing

Every subsequent mutation to an issue or lease (e.g., releasing lease, marking PR verified, revoking lease) MUST condition on:
```text
ConditionExpression: "activeLeaseId = :expectedLeaseId AND version = :expectedVersion"
```
If a worker gets delayed (e.g. Lambda cold start / GC pause) and its lease was revoked or reassigned in the interim, the conditional write fails and prevents corrupted state.

---

## 4. Lease Expiry Semantics

- **Authorization Logic**: All code MUST compare `leaseExpiresAt > now()`.
- **DynamoDB TTL**: The `ttl` attribute is strictly for low-cost background garbage collection. Never assume an expired item has been removed from DynamoDB.
