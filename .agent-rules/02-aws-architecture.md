# 02 — AWS Architecture & Boundaries

> **DIRECTIVE**: Every AWS service in this system must have a concrete, demonstrable failure-mode or operational justification. **AWS logos are not architecture.**

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

Maintainer / Judge
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
- **Amazon S3 (Data Minimization)**: Stores ONLY what is required for reproducibility:
  - Verification evidence bundles.
  - Large repository/diff artifacts when necessary.
  - Pinned analysis artifacts.
  - Oversized event payloads (>256 KB) only when required.
  - All objects must be encrypted, repository-scoped, installation-scoped, access-controlled, and retention-limited. Store less, not more.
- **AWS Secrets Manager**: GitHub App private key ARN, webhook HMAC secret ARN. Plaintext secrets are strictly forbidden in configuration.
- **Amazon CloudWatch**: Embedded Metric Format (EMF) logs, structured operational telemetry, DLQ alarms.
- **Amazon Amplify Hosting**: Static hosting for the minimal judge-facing evidence dashboard (justified specifically by the First Commit Ship It live URL requirement).

---

## 3. MVP Default-Deny Policy for Unapproved Services

For the hackathon MVP, do NOT introduce ECS, Fargate, AWS Batch, Aurora, RDS, OpenSearch, DocumentDB, ElastiCache/Redis, Amazon Kinesis, MSK (Kafka), CodeBuild, EKS, EC2, or heavy third-party agent frameworks (LangChain, CrewAI, AutoGen) unless an accepted requirement genuinely demands it.

Any proposed new AWS service must answer:
1. *What exact product behavior requires it?*
2. *What exact failure mode does it solve?*
3. *Why can't an existing approved service (Lambda/SQS/Step Functions/DynamoDB/S3) solve it?*
4. *Does it demonstrably improve the 3-minute hackathon demo?*
5. *What implementation and testing cost does it add?*

If the answers are weak: **REJECT THE SERVICE.**

---

## 4. Architectural Invariants

1. **Synchronous Boundary Isolation**: Webhook Lambda NEVER invokes Step Functions or Bedrock directly. It validates HMAC, enqueues to SQS Standard, and returns HTTP 202 quickly within GitHub's delivery timeout.
2. **Standard vs. Express Workflows**: Use Step Functions Standard for auditability, visual execution history, and state pause/retry tolerance.
3. **Least Privilege IAM**: Every Lambda function has its own dedicated execution role scoping permissions strictly to exact DynamoDB tables, S3 prefixes, and Secrets Manager ARNs (`08-security-threat-model.md`).
4. **SQS Buffering vs DynamoDB Authority**: SQS Standard provides buffering, backpressure, retries, and failure isolation. SQS does NOT provide authoritative deduplication. Authoritative deduplication is enforced by DynamoDB conditional writes on `EVENT#<deliveryId>`.

---

## 5. Cost Discipline

Because Ship It evaluates architecture and cost choices, each service implements strict cost controls:
- **AWS Lambda**: Serverless compute with zero idle cost; no continuously running instances.
- **Amazon SQS Standard**: Buffers burst traffic smoothly instead of overprovisioning compute.
- **AWS Step Functions Standard**: Coordinates only workflow states actually required; fail-closed halts prevent runaway execution.
- **Amazon DynamoDB**: On-demand / serverless billing for conditional state transitions; pay only for actual read/write operations.
- **Amazon S3**: Strict data minimization; stores large evidence only when necessary, with automated lifecycle expiration.
- **Amazon Bedrock**: Invoked only after admission and deduplication succeed; strictly bounded input/output tokens; never called for duplicate deliveries.
- **Amazon Amplify Hosting**: Lightweight static hosting for the judge-facing dashboard with near-zero idle cost.
