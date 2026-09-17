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
Amazon SQS Standard + Dead-Letter Queue (DLQ)
   │
   ▼
Dispatcher Lambda (Atomic Event Admission via EVENT#<deliveryId> in DynamoDB)
   │
   ▼
AWS Step Functions Standard (Auditable Orchestration)
   ├── 1. Repository Retrieval (GitHub Trees/Blobs API)
   ├── 2. Bedrock Claim Extraction (Structured JSON Schema Output via bedrock-runtime)
   ├── 3. Deterministic Verifier (Python AST & Git Tree Inspection → SUPPORTED/CONTRADICTED/UNKNOWN)
   ├── 4. Policy Evaluation Engine (Threshold & Rule Checks → VERIFIED/NEEDS_REVISION/ESCALATED)
   ├── 5. DynamoDB Atomic Lease Acquisition (TransactWriteItems)
   └── 6. GitHub Side Effect (Idempotent Assignment/Comment/Check)

Maintainer / Viewer
   │
   ▼
Amazon Amplify Hosting (Live Dashboard)
   │
   ▼
Minimal React Maintainer / Evidence Dashboard
   │
   ▼
Read-Only Evidence / Status API (API Gateway + Lambda)
```

**Supporting Services**: Amazon DynamoDB, Amazon S3, AWS Secrets Manager, Amazon CloudWatch, Amazon Amplify Hosting.

---

## Service Justifications

| Service | Responsibility | Operational Justification |
| :--- | :--- | :--- |
| **API Gateway** | HTTPS ingress for GitHub webhooks and dashboard API. | Provides TLS termination, edge rate limiting, and request routing. |
| **Webhook Lambda** | Fast HMAC-SHA256 signature validation and SQS enqueue. | Isolates fast sub-second acknowledgement from downstream analysis; prevents webhook delivery timeouts. |
| **Amazon SQS Standard + DLQ** | At-least-once delivery buffer and backpressure control. | Absorbs webhook bursts; isolates poison messages to DLQ; decouples ingestion from workflow execution. *(Does not provide authoritative deduplication).* |
| **Dispatcher Lambda** | Atomic delivery admission via DynamoDB `EVENT#<deliveryId>`. | Drops duplicate webhook deliveries safely without auth errors or duplicated Step Functions workflows. |
| **Step Functions Standard** | Staged, auditable orchestration of verification and PR integrity. | Visual execution traces, built-in retry/catch policies, and state isolation; eliminates monolithic Lambda anti-patterns. |
| **Amazon Bedrock** | Structured semantic claim extraction from natural language. | Invoked via `bedrock-runtime` with JSON Schema (e.g. Claude Sonnet 4.6); extracts typed claims without holding state authority. |
| **Deterministic Verifier** | Independent static AST and Git tree analysis. | Confirms or contradicts Bedrock claims against actual repository code; eliminates reliance on unverified model output. |
| **Policy Engine** | Deterministic qualification gating. | Evaluates verifier evidence against explicit rules (`VERIFIED` only if concrete claims supported and target bound). |
| **Amazon DynamoDB** | Single-table authoritative state: events, leases, issues, idempotency. | Conditional writes and `TransactWriteItems` prevent read-check-write races and enforce single-lease ownership. |
| **Amazon S3** | Data-minimized evidence storage. | Stores only verification evidence bundles, large diffs, and oversized payloads (>256 KB); encrypted and retention-limited. |
| **AWS Secrets Manager** | GitHub App private key ARN and webhook secret ARN. | Eliminates plaintext secrets in configuration; supports zero-downtime rotation. |
| **Amazon CloudWatch** | Structured JSON logs and EMF operational metrics. | Full decision auditability and real-time operational telemetry. |
| **Amazon Amplify Hosting** | Static hosting for the read-only evidence dashboard. | Serves the web-accessible dashboard URL and scales with demand. |

---

## Core Execution Model

```text
AI (Bedrock via bedrock-runtime)
  ↓ structured semantic claims (JSON Schema)
Repository Ground Truth (Python AST & Git Tree)
  ↓ independent inspection
Deterministic Verifier
  ↓ SUPPORTED / CONTRADICTED / UNKNOWN evidence records
Policy Engine
  ↓ VERIFIED / NEEDS_REVISION / ESCALATED decision
Atomic Authoritative State (DynamoDB TransactWriteItems)
  ↓ single-winner lease acquisition
External Side Effect (GitHub API — Idempotent)
  ↓ assignment + evidence comment
```

Bedrock never grants ownership. AI extracts claims. Deterministic code verifies them. DynamoDB is authoritative for internal lease and idempotency state, while GitHub maintainer actions take precedence and reconcile upon detection.

---

## Trust Boundaries

| Boundary | Classification | Examples |
| :--- | :--- | :--- |
| **UNTRUSTED** | All GitHub-originated content | Issue bodies, comments, READMEs, source files, commit messages, PR descriptions, PR diffs. Wrapped in `<untrusted_contributor_text>` or `<untrusted_repository_content>`. |
| **UNTRUSTED** | Bedrock model output | Extracted claims, hypothesized file paths, proposed symbols. Must be verified by deterministic code before state transitions. |
| **TRUSTED AUTHORITY** | Deterministic Policy Engine | Rule-based evaluation of evidence records against strict qualification thresholds. |
| **TRUSTED AUTHORITY** | DynamoDB conditional state | Lease acquisition, qualification consumption, delivery admission, version-fenced mutations. |

---

## State Machines

### Issue Lifecycle
```text
OPEN → CLAIM_WINDOW → QUALIFIED → LEASED → PR_OPEN → INTEGRITY_CHECK → PASS / DRIFT / REVIEW
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

Every external side effect is protected by an internal deterministic idempotency record stored in DynamoDB before execution:

| Side Effect | Idempotency Key Format | Pre-Flight Reconciliation |
| :--- | :--- | :--- |
| Webhook event admission | `EVENT#<deliveryId>` | Conditional write `attribute_not_exists(PK)` drops duplicate deliveries safely. |
| Issue assignment | `assignment/<repoId>/<issueNumber>/<leaseId>` | Checks whether issue is already assigned to target contributor on GitHub. |
| Evaluation comment | `comment/<verificationId>/<decisionType>` | Checks whether comment with matching verification marker exists in timeline. |
| PR check run | `check/<repoId>/<prNumber>/<headSha>/<checkType>` | Checks existing Check Run status on GitHub. |

---

## Concurrency Strategy

Lease acquisition is the highest-risk concurrent operation. It uses DynamoDB `TransactWriteItems`:

1. Multiple workers may simultaneously attempt to claim the same issue.
2. A simple `GetItem → check → PutItem` sequence creates a time-of-check/time-of-use race condition.
3. The atomic transaction verifies `(qualification exists AND is VERIFIED AND is unconsumed AND issue has no active unexpired lease)` and writes `(new lease + consumed qualification + updated issue with incremented version)` in a single all-or-nothing transaction.
4. Stale-worker fencing uses `ConditionExpression: activeLeaseId = :expected AND version = :expected` on every subsequent mutation.
5. In the concurrency test on fresh Issue #43: 100 parallel worker transactions yield exactly 1 success and 99 `TransactionCanceledException` conflicts.

---

## Cost Discipline

Each architecture component implements concrete engineering cost controls:
- **AWS Lambda**: Request-driven serverless compute with no provisioned server fleet.
- **Amazon SQS Standard**: Buffers burst traffic smoothly instead of overprovisioning compute resources.
- **AWS Step Functions Standard**: Coordinates only states actually required; fail-closed halts prevent runaway retries.
- **Amazon DynamoDB**: On-demand capacity billing for conditional state transitions with no idle provisioned capacity.
- **Amazon S3**: Data minimization; stores large evidence only when necessary, with automated lifecycle rules.
- **Amazon Bedrock**: Invoked only after admission and deduplication succeed; bounded token budgets; never called for duplicate deliveries.
- **Amazon Amplify Hosting**: Static dashboard hosting that scales with demand.

---

## Scope Exclusions (Default-Deny)

The following components are not part of the MVP architecture:
ECS, Fargate, AWS Batch, Aurora/RDS, ElastiCache/Redis, OpenSearch, Amazon Kinesis, MSK (Kafka), EKS, EC2, CodeBuild, arbitrary contributor code execution environments, heavy third-party agent frameworks.
