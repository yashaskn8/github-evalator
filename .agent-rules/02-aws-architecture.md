# 02 — AWS Architecture & Boundaries

> **Directive**: Every AWS service in this system must have a concrete, demonstrable failure-mode or operational justification.

---

## 1. System Pipeline

```text
GitHub Webhook
   │
   ▼
Amazon API Gateway (HTTP/REST Endpoint)
   │
   ▼
Webhook Lambda (HMAC Verification & Fast Enqueue — Sub-second ACK SLO)
   │
   ▼
Amazon SQS Standard + Dead-Letter Queue (DLQ)
   │
   ▼
Dispatcher Lambda (Atomic Event Admission via EVENT#<deliveryId> in DynamoDB)
   │
   ▼
AWS Step Functions (Standard Workflow)
   ├── 1. Repository Retrieval (GitHub Trees/Blobs / pinned SHA)
   ├── 2. Bedrock Claim Extraction (Claude Sonnet 4.6 structured JSON claims via bedrock-runtime)
   ├── 3. Deterministic Verifier (Python AST / Git tree inspection → SUPPORTED/CONTRADICTED/UNKNOWN)
   ├── 4. Policy Evaluation Engine (Rules/Thresholds → VERIFIED/NEEDS_REVISION/ESCALATED)
   ├── 5. DynamoDB Lease Acquisition (TransactWriteItems conditional write)
   └── 6. GitHub API Side Effect (Idempotent Assignee, comment, check run)

Maintainer / Viewer
   │
   ▼
Amazon Amplify Hosting
   │
   ▼
Minimal React Maintainer / Evidence Dashboard
   │
   ▼
Read-Only Evidence / Status API
```

---

## 2. Supporting Infrastructure

- **Amazon DynamoDB**: Single-table authoritative state, atomic leases, event admission (`EVENT#<deliveryId>`), idempotency records.
- **Amazon S3 (Data Minimization)**: Stores only what is required for reproducibility:
  - Verification evidence bundles.
  - Large repository/diff artifacts when necessary.
  - Pinned analysis artifacts.
  - Oversized event payloads (>256 KB) only when required.
  - All objects must be encrypted, repository-scoped, installation-scoped, access-controlled, and retention-limited.
- **AWS Secrets Manager**: GitHub App private key ARN, webhook HMAC secret ARN. Plaintext secrets are strictly forbidden in configuration.
- **Amazon CloudWatch**: Embedded Metric Format (EMF) logs, structured operational telemetry, DLQ alarms.
- **Amazon Amplify Hosting**: Static hosting for the read-only evidence dashboard, providing an accessible web interface for system state.

---

## 3. Architecture Policy for Unapproved Services

For the MVP, do not introduce ECS, Fargate, AWS Batch, Aurora, RDS, OpenSearch, DocumentDB, ElastiCache/Redis, Amazon Kinesis, MSK (Kafka), CodeBuild, EKS, EC2, or heavy third-party agent frameworks (LangChain, CrewAI, AutoGen) unless an accepted requirement genuinely demands it.

Any proposed new AWS service must answer:
1. *What exact product behavior requires it?*
2. *What exact failure mode does it solve?*
3. *Why can't an existing approved service (Lambda/SQS/Step Functions/DynamoDB/S3) solve it?*
4. *Does it demonstrably improve the core verification or demonstration workflow?*
5. *What implementation and testing cost does it add?*

If the answers are weak, the service is rejected.

---

## 4. Architectural Invariants

1. **Synchronous Boundary Isolation**: Webhook Lambda never invokes Step Functions or Bedrock directly. It validates HMAC, enqueues to SQS Standard, and returns HTTP 202 quickly within GitHub's delivery timeout.
2. **Standard vs. Express Workflows**: Use Step Functions Standard for auditability, visual execution history, and state pause/retry tolerance.
3. **Least Privilege IAM**: Every Lambda function has its own dedicated execution role scoping permissions strictly to exact DynamoDB tables, S3 prefixes, and Secrets Manager ARNs (`08-security-threat-model.md`).
4. **SQS Buffering vs DynamoDB Authority**: SQS Standard provides buffering, backpressure, retries, and failure isolation. SQS does not provide authoritative deduplication. Authoritative deduplication is enforced by DynamoDB conditional writes on `EVENT#<deliveryId>`.

---

## 5. Cost Discipline

Each architecture component implements concrete engineering cost controls:
- **AWS Lambda**: Request-driven serverless compute without a provisioned server fleet.
- **Amazon SQS Standard**: Buffers burst traffic smoothly instead of overprovisioning compute resources.
- **AWS Step Functions Standard**: Coordinates only workflow states actually required; fail-closed halts prevent runaway execution.
- **Amazon DynamoDB**: On-demand capacity billing for conditional state transitions; pay only for actual read/write operations without idle provisioned throughput.
- **Amazon S3**: Data minimization; stores large evidence only when necessary, with automated lifecycle expiration.
- **Amazon Bedrock**: Invoked only after admission and deduplication succeed; strictly bounded input/output tokens; never called for duplicate deliveries.
- **Amazon Amplify Hosting**: Static hosting for the read-only dashboard that scales on demand.

---

## 6. Observability & Telemetry

> Log real operational events. Zero synthetic or hardcoded metrics for demo purposes.

### Structured Logging
All Lambda functions and Step Functions tasks emit JSON structured logs with safe correlation context. Allowed fields: `githubDeliveryId`, `eventType`, `action`, `installationId`, `repositoryId`, `issueNumber`, `senderId`, `bodyHash`, `correlationId`, `latency`, `result`, `errorClass`. Private issue bodies, source code, secrets, and raw diff payloads are strictly excluded.

### CloudWatch EMF Metrics
Emit real runtime operational metrics via Embedded Metric Format:
- **Ingestion & Latency**: `WebhookLatency`, `VerificationLatency`, `BedrockLatency`, `QueueDepth`.
- **System Reliability**: `GitHubApiFailureRate`, `WorkflowRetryCount`, `LeaseConflicts`, `DuplicateAssignmentAttempts`.
- **Domain Outcomes**: `ProposalsVerified`, `ProposalsNeedsRevision`, `HumanEscalations`, `ExpiredLeases`, `ProposalPRDrift`.

### Decision Auditability
Every system decision (lease grant, proposal revision, PR drift flag) must be fully reconstructible by combining persisted evidence records (in S3/DynamoDB) with correlated CloudWatch logs using `verificationId` or `githubDeliveryId`. Observability failures must not alter authoritative DynamoDB state transitions.
