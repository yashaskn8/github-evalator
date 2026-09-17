# Architecture — GitHub Evalator

> **AI reasons. Deterministic code verifies. DynamoDB owns authority.**

---

## System Architecture

```text
GitHub (System of Record)
   │
   ▼
Amazon API Gateway (HTTP/REST Endpoint)
   │
   ▼
Webhook Lambda (HMAC-SHA256 Verification → Fast SQS Enqueue)
   │
   ▼
Amazon SQS (Standard Queue + Dead-Letter Queue)
   │
   ▼
Dispatcher Lambda (Event Routing)
   │
   ▼
AWS Step Functions Standard (Auditable Orchestration)
   ├── 1. Repository Retrieval (GitHub Trees/Blobs API → S3 AST Cache)
   ├── 2. Bedrock Claim Extraction (Structured JSON Schema Output)
   ├── 3. Deterministic Verifier (File/Symbol/Test Ground-Truth)
   ├── 4. Policy Evaluation Engine (Threshold & Rule Checks)
   ├── 5. DynamoDB Atomic Lease Acquisition (TransactWriteItems)
   └── 6. GitHub Side Effect (Idempotent Assignment/Comment/Check)
```

**Supporting Services**: Amazon S3, AWS Secrets Manager, Amazon CloudWatch.

---

## Service Justifications

| Service | Responsibility | Failure Mode It Addresses |
| :--- | :--- | :--- |
| **API Gateway** | HTTPS ingress for GitHub webhooks. | Provides TLS termination, throttling, and rate limiting at the edge. |
| **Webhook Lambda** | HMAC-SHA256 signature validation, delivery ID extraction, SQS enqueue. | Isolates fast acknowledgement from downstream processing; prevents synchronous timeout on complex analysis. |
| **Amazon SQS + DLQ** | At-least-once delivery buffer between ingestion and processing. | Absorbs bursts; DLQ captures poison messages after retry exhaustion; decouples webhook latency from workflow duration. |
| **Dispatcher Lambda** | Routes SQS events into the correct Step Functions workflow. | Separates event classification from orchestration logic. |
| **Step Functions Standard** | Staged, auditable orchestration of the full verification pipeline. | Provides visual execution history, built-in retry/catch, and prevents monolithic Lambda anti-patterns. |
| **Amazon Bedrock** | Structured semantic claim extraction from natural-language proposals. | Converts ambiguous text into falsifiable JSON claims validated by schema before any downstream use. |
| **Amazon DynamoDB** | Single-table authoritative state: leases, qualifications, issues, idempotency records. | Conditional writes and `TransactWriteItems` prevent read-check-write races and enforce exactly-one-lease. |
| **Amazon S3** | Immutable evidence storage: raw webhook payloads, AST snapshots, verification records. | Provides durable, cost-effective storage for large payloads exceeding SQS limits and for audit provenance. |
| **Secrets Manager** | Stores GitHub App private key, webhook HMAC secret, App ID. | Prevents credential leakage; supports rotation without code deployment. |
| **CloudWatch** | Structured JSON logs with correlation IDs; Embedded Metric Format (EMF) operational metrics. | Enables full decision auditability and real-time operational visibility. |

---

## Core Execution Model

```text
AI (Bedrock)
  ↓ structured semantic claims & hypotheses
Repository Evidence (Git tree, AST, file inspection)
  ↓ SUPPORTED / CONTRADICTED / UNKNOWN
Deterministic Verifier
  ↓ evidence records with provenance
Policy Engine
  ↓ pass / fail / escalation
Atomic Authoritative State (DynamoDB Conditional Transaction)
  ↓ lease grant / rejection
External Side Effect (GitHub API — Idempotent)
```

**Bedrock never grants ownership.** AI extracts claims. Deterministic code verifies them. DynamoDB holds absolute state authority.

---

## Trust Boundaries

| Boundary | Classification | Examples |
| :--- | :--- | :--- |
| **UNTRUSTED** | All GitHub-originated content | Issue bodies, comments, READMEs, source files, commit messages, PR descriptions, PR diffs. |
| **UNTRUSTED** | Bedrock model output | Hallucinated paths, invented symbols, prompt-injected claims. Must be independently verified. |
| **TRUSTED AUTHORITY** | Deterministic policy engine | Rule-based evaluation of evidence against configurable thresholds. |
| **TRUSTED AUTHORITY** | DynamoDB conditional state | Lease acquisition, qualification consumption, version-fenced mutations. |

---

## State Machines

### Issue Lifecycle
```text
OPEN → CLAIM_WINDOW → QUALIFIED → LEASED → PR_OPEN → INTEGRITY_CHECK → PASS / REVIEW
```

### Qualification Lifecycle
```text
SUBMITTED → ANALYZING → EVIDENCE_CHECK → VERIFIED / NEEDS_REVISION / ESCALATED
```

### Lease Lifecycle
```text
NONE → ACTIVE → COMPLETED / EXPIRED / REVOKED
```

---

## Idempotency Strategy

Every external side effect is protected by an **internal deterministic idempotency record** stored in DynamoDB before execution:

| Side Effect | Idempotency Key Format |
| :--- | :--- |
| Webhook event processing | `X-GitHub-Delivery` GUID (stored on admission) |
| Issue assignment | `assignment/<repoId>/<issueNumber>/<leaseId>` |
| Evaluation comment | `comment/<verificationId>/<decisionType>` |
| PR check run | `check/<repoId>/<prNumber>/<headSha>/<checkType>` |

Upon network timeout after a successful GitHub API call, the retry worker reconciles external state before re-executing.

---

## Concurrency Strategy

Lease acquisition is the highest-risk concurrent operation. It **must** use DynamoDB `TransactWriteItems` because:

1. Multiple workers may simultaneously attempt to claim the same issue.
2. A simple `GetItem → check → PutItem` sequence creates a time-of-check/time-of-use race.
3. The atomic transaction verifies `(qualification exists AND is VERIFIED AND is unconsumed AND issue has no active unexpired lease)` and writes `(new lease + consumed qualification + updated issue)` in a single all-or-nothing operation.
4. Stale-worker fencing uses `ConditionExpression: activeLeaseId = :expected AND version = :expected` on every subsequent mutation.

---

## MVP Exclusions (Default-Deny)

The following are **not part of the approved MVP architecture**. Any introduction requires answering the 5-point justification test in `.agent-rules/02-aws-architecture.md`:

ECS, Fargate, AWS Batch, Aurora/RDS, ElastiCache/Redis, OpenSearch, Amazon Kinesis, MSK (Kafka), EKS, EC2, CodeBuild, arbitrary contributor code execution environments, heavy third-party agent frameworks.
