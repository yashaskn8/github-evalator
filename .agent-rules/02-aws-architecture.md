# 02 — AWS Architecture & Boundaries

> **DIRECTIVE**: Every AWS service in this system must have a concrete, demonstrable failure-mode or operational justification. **AWS logos are not architecture.**

---

## 1. System Pipeline

```text
GitHub Webhook
   │
   ▼
API Gateway (HTTP API / REST)
   │
   ▼
Webhook Lambda (HMAC Verification & Fast Enqueue — Sub-second ACK SLO)
   │
   ▼
Amazon SQS (FIFO / Standard + Dead-Letter Queue)
   │
   ▼
Dispatcher Lambda
   │
   ▼
AWS Step Functions (Standard Workflow)
   ├── 1. Repository Retrieval (GitHub Trees/Blobs / S3 cache)
   ├── 2. Bedrock Claim Extraction (Claude / Titan JSON claims)
   ├── 3. Deterministic Verifier (AST / file existence / symbols)
   ├── 4. Policy Evaluation Engine (Rules / Thresholds)
   ├── 5. DynamoDB Lease Acquisition (Conditional write / TransactWrite)
   └── 6. GitHub API Side Effect (Assignee, issue comment, check run)
```

---

## 2. Supporting Infrastructure

- **Amazon DynamoDB**: Single-table authoritative state, atomic leases, idempotency records.
- **Amazon S3**: Immutable payload storage (raw webhook bodies, AST cache, large diff snapshots).
- **AWS Secrets Manager**: GitHub App private key, webhook HMAC secret, App ID.
- **Amazon CloudWatch**: Embedded Metric Format (EMF) logs, structured operational telemetry, DLQ alarms.

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

1. **Synchronous Boundary Isolation**: Webhook Lambda NEVER invokes Step Functions or Bedrock directly. It validates HMAC, enqueues to SQS, and returns HTTP 202 quickly within GitHub's delivery timeout.
2. **Standard vs. Express Workflows**: Use Step Functions Standard for auditability, visual execution history, and state pause/retry tolerance.
3. **Least Privilege IAM**: Every Lambda function has its own dedicated execution role scoping permissions strictly to exact DynamoDB tables, S3 prefixes, and Secrets Manager ARNs (`08-security-threat-model.md`).
