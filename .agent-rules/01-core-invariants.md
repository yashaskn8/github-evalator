# 01 — Core Invariants

> **NON-NEGOTIABLE ARCHITECTURAL FOUNDATION**
> These 11 invariants are absolute across all implementations. No feature, refactor, or optimization may violate them.

---

## The Execution Model

```text
AI (Bedrock)
  ↓ [structured claims & hypothesis]
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

### 1. AI Cannot Grant Ownership
Bedrock outputs semantic claims, affected files, and hypotheses. Bedrock NEVER writes to DynamoDB, assigns an issue, or transitions an authoritative state. Only deterministic backend logic evaluates policy and grants state transitions.

### 2. Exactly One Active Lease Per Issue
A GitHub issue may have at most ONE valid, active lease at any point in time. Concurrent claims MUST be resolved through DynamoDB atomic conditional writes or transactions.

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

### 7. Immutable Evidence Provenance
Every verification decision must be recorded immutably with full provenance:
- `commitSha`, `filesInspected`, `extractedClaims`, `deterministicEvidence`, `modelId`, `promptSchemaVersion`, `timestamp`, `decisionOutcome`.

### 8. Untrusted Repository Data
All content originating from GitHub (issue titles/bodies, comments, READMEs, source files, commit messages, PR diffs) is **untrusted user input**. It must never be evaluated as code, executed in an unconfined environment, or allowed to alter system security policies.

### 9. Strict Repository Isolation
Data, AST caches, and evaluation records for a repository are strictly isolated. No cross-repository or cross-installation data leakage is permitted under any circumstances.

### 10. Idempotent Side Effects
All GitHub interactions (issue assignment, commenting, check run submission) MUST use deterministic deduplication identifiers to prevent duplicate comments or assignments during network retries.

### 11. Human Maintainer Authority Override
Human maintainer actions (manual assignment, issue closure, explicit label overrides) always take precedence over automated system decisions. Automated workflows must detect and defer to maintainer interventions.
