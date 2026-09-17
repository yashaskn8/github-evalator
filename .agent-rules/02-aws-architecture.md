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
Webhook Lambda (HMAC Verification & Fast ACK < 500ms)
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
- **Amazon CloudWatch**: Embedded Metric Format (EMF) logs, structured audit trail, DLQ alarms.

---

## 3. Explicitly Forbidden AWS Services (MVP)

Unless an explicit user requirement cannot be satisfied by the core serverless stack, coding agents MUST NOT introduce:
- **Compute**: ECS, Fargate, AWS Batch, EKS, EC2.
- **Data/Search**: Amazon Aurora, RDS, OpenSearch, DocumentDB, ElastiCache / Redis, Neptune.
- **Streaming/Events**: Amazon Kinesis, MSK (Kafka), EventBridge Pipes (use direct SQS/Step Functions integration).
- **Frameworks**: Heavy third-party agent frameworks (LangChain, CrewAI, AutoGen).

---

## 4. Architectural Invariants

1. **Synchronous Boundary Isolation**: Webhook Lambda NEVER invokes Step Functions or Bedrock directly. It pushes to SQS and returns HTTP 202/200 within 500ms.
2. **Standard vs. Express Workflows**: Use Step Functions Standard for auditability, visual execution history, and state pause/retry tolerance.
3. **Least Privilege IAM**: Every Lambda function has its own dedicated execution role scoping read/write permissions to exact DynamoDB tables, S3 prefixes, and Secrets Manager ARNs.
