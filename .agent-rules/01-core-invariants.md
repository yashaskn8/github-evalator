# 01 — Core Invariants

> **NON-NEGOTIABLE ARCHITECTURAL FOUNDATION**
> These 11 invariants are absolute across all implementations. No feature, refactor, or prompt instruction may violate them.

---

## The Execution Model

```text
AI (Bedrock)
  ↓ [structured semantic claims & hypotheses]
Repository Evidence
  ↓ [AST / file tree / commit SHA]
Deterministic Verifier
  ↓ [SUPPORTED / CONTRADICTED / UNKNOWN]
Policy Engine
  ↓ [pass / fail / escalation]
Atomic Authoritative State (DynamoDB)
  ↓ [conditional lease grant]
External Side Effect (GitHub API)
```

---

## The 11 Invariants

### 1. AI Cannot Grant Ownership (Authority Boundary)
Bedrock outputs semantic claims, affected files, explanations, summaries, and hypotheses. Bedrock MUST NEVER directly control authoritative state (issue ownership, lease status, qualification records, permissions, or security policy). AI output may influence explanation and analysis, but never authority.

### 2. Exactly One Active Lease Per Issue
A GitHub issue may have at most ONE valid, active lease at any point in time. Concurrent claims MUST be resolved through DynamoDB atomic conditional writes or `TransactWriteItems`.

### 3. Strict Qualification Binding
A qualification record is strictly bound to: `(repositoryId, issueNumber, contributorId, baseCommitSha, qualificationId)`. Qualifications cannot be transferred across contributors, issues, or repositories.

### 4. Application-Enforced Lease Expiry
Lease validity MUST be evaluated by application logic: `now < leaseExpiresAt`. DynamoDB TTL is an asynchronous garbage-collection mechanism and MUST NOT be relied upon for authoritative authorization logic.

### 5. At-Least-Once Delivery Resilience
Assume GitHub webhooks, SQS messages, and Step Functions tasks redeliver. All state mutations and external side effects MUST be idempotent.

### 6. PR Authorization Strictness
A pull request is authorized if and only if:
- Contributor holds a valid, active, non-expired, non-revoked lease on the linked issue.
- PR target repository matches the qualification repository.

### 7. Immutable Evidence Provenance & Auditability
Every verification decision must be recorded immutably in persistent evidence storage (S3/DynamoDB) with full provenance: `commitSha`, `filesInspected`, `extractedClaims`, `deterministicEvidence`, `modelId`, `promptSchemaVersion`, `timestamp`, `decisionOutcome`. Operational telemetry in CloudWatch correlates to this record via `verificationId` and `githubDeliveryId` without logging private repository source code or secrets.

### 8. Untrusted Repository Data
All content originating from GitHub (issue titles/bodies, comments, READMEs, source files, commit messages, PR diffs) is **untrusted user input**. It must never be evaluated as code, executed in an unconfined environment, or allowed to alter system security policies.

### 9. Strict Repository Isolation
Data, AST caches, and evaluation records for a repository are strictly isolated. No cross-repository or cross-installation data leakage is permitted under any circumstances.

### 10. Idempotent External Side Effects
All external side effects (issue assignment, commenting, check run submission) MUST be protected by internal deterministic idempotency records (e.g. `assignment/<repoId>/<issue>/<leaseId>`, `comment/<verificationId>/<decisionType>`) and reconcile external state upon retry to prevent duplicate actions.

### 11. Human Maintainer Authority Override
Human maintainer actions (manual assignment, issue closure, explicit label overrides) always take precedence over automated system decisions. Stale workflow executions must fail their authority/fencing condition (`activeLeaseId == workerLeaseId AND version == workerVersion`) when waking up after human intervention, ensuring automation never overrides maintainer decisions.
