# GitHub Evalator

> **Evidence-backed issue assignment for high-traffic open-source repositories.**
> *AI reasons. Deterministic code verifies. DynamoDB owns authority.*

---

## Problem Overview

High-traffic open-source repositories often encounter an operational bottleneck when valuable issues are published:
- An issue attracts multiple generic "assign me" comments without implementation details.
- Maintainers have limited time to evaluate whether contributors understand the codebase before granting ownership.
- Multiple contributors may work concurrently in isolation, resulting in overlapping, competing pull requests.
- Maintainers then spend considerable effort reviewing off-target or conflicting pull requests.

GitHub Evalator is designed to evaluate repository-grounded implementation proposals before granting an exclusive issue lease, addressing overlapping claims and duplicate pull requests early.

---

## Core Execution Flow

```text
Issue Opened
    │
    ▼
Contributors Submit Proposals (Issue Comments)
    │
    ▼
Amazon Bedrock (Extracts Structured Claims via JSON Schema)
    │
    ▼
Deterministic Repository Verifier (Validates Claims Against AST & Git Tree)
    │
    ▼
Policy Evaluation Engine (Evaluates Evidence → VERIFIED / NEEDS_REVISION / ESCALATED)
    │
    ▼
Amazon DynamoDB (Atomic Conditional Lease Grant — Single Active Owner via TransactWriteItems)
    │
    ▼
GitHub API (Automated Issue Assignment & Idempotent Evidence Comment)
    │
    ▼
Contributor Submits Pull Request
    │
    ▼
PR Integrity Workflow (Compares PR Diff vs. Verified Qualification Intent)
    │
    ▼
Status: PASS | DRIFT | MAINTAINER REVIEW
```

---

## Core Capabilities

1. **Repository-Grounded Proposal Verification**: Amazon Bedrock extracts structured claims (`affected_files`, `target_symbols`, `test_strategy`), and a deterministic verifier independently checks the actual repository AST and Git tree. Inaccurate or hallucinated claims are marked `CONTRADICTED`.
2. **Evidence-Backed Assignment**: DynamoDB conditional transactions ensure that exactly one qualified contributor receives a time-bounded issue lease. Unsubstantiated claims are rejected or flagged for revision.
3. **Proposal-to-PR Integrity**: When a pull request is opened, the workflow verifies that the unified diff implements the file scope and architectural approach that earned the contributor the lease (`PASS` vs. `DRIFT`).
4. **Human Escalation on Uncertainty**: Human maintainer actions on GitHub override automation. Ambiguous proposals, prompt injections, or stale workers escalate to maintainers rather than making unauthorized state changes.

---

## AWS Architecture

```text
GitHub Webhooks
   │
   ▼
Amazon API Gateway (HTTP/REST Ingress)
   │
   ▼
AWS Lambda (HMAC Verification & Fast SQS Enqueue — Sub-second ACK SLO)
   │
   ▼
Amazon SQS Standard + Dead-Letter Queue (Burst Buffering & Retry Isolation)
   │
   ▼
AWS Lambda (Dispatcher — Atomic Event Admission via EVENT#<deliveryId> in DynamoDB)
   │
   ▼
AWS Step Functions Standard (Auditable Workflow Orchestration)
   ├── 1. Repository Retrieval (GitHub Trees/Blobs API)
   ├── 2. Amazon Bedrock (Structured Claim Extraction via bedrock-runtime)
   ├── 3. Deterministic Verifier (Python AST & Symbol Inspection)
   ├── 4. Policy Engine (Evidence Evaluation)
   ├── 5. Amazon DynamoDB (Atomic Conditional Lease Acquisition)
   └── 6. GitHub API (Idempotent Assignment & Comment Side Effects)

Maintainer / Viewer
   │
   ▼
Amazon Amplify Hosting (Dashboard URL)
   │
   ▼
Minimal React Maintainer / Evidence Dashboard
   │
   ▼
Read-Only Evidence / Status API
```

**Supporting Services**:
- **Amazon DynamoDB**: Single-table storage for internal lease state, event admission, and idempotency records.
- **Amazon S3**: Data-minimized storage for verification evidence bundles, pinned analysis artifacts, and oversized payloads (>256 KB).
- **AWS Secrets Manager**: Secure storage for GitHub App credentials and webhook HMAC secrets.
- **Amazon CloudWatch**: Operational logging and Embedded Metric Format (EMF) metrics.
- **Amazon Amplify Hosting**: Static hosting for the read-only evidence dashboard.

*GitHub serves as the external system of record.*

---

## Trust Boundaries & Authority

- **Bedrock does not grant issue ownership**: AI models interpret natural language into structured claims. Deterministic code verifies those claims against repository structure. DynamoDB is authoritative for the system's internal lease, qualification, fencing, and idempotency state. GitHub remains the external system of record. Explicit maintainer actions on GitHub override automation and are reconciled into internal state before workflows may continue.
- **Repository content is untrusted data**: Issue descriptions, comments, READMEs, and source files are treated as untrusted input and wrapped in explicit delimiters (`<untrusted_contributor_text>` and `<untrusted_repository_content>`). Untrusted text cannot modify system evaluation policy.
- **Static inspection only**: The MVP performs AST and file tree analysis; it does not execute untrusted contributor code.


---

## Cost Discipline

- **AWS Lambda**: Request-driven serverless compute with no provisioned server fleet.
- **Amazon SQS Standard**: Buffers burst traffic smoothly instead of overprovisioning compute resources.
- **AWS Step Functions Standard**: State transitions execute only when triggered; fail-closed halts prevent runaway retries.
- **Amazon DynamoDB**: On-demand capacity billing for conditional state transitions with no idle provisioned capacity.
- **Amazon S3**: Data minimization; stores large evidence only when necessary, with automated lifecycle expiration.
- **Amazon Bedrock**: Invoked only after admission and deduplication succeed; bounded token budgets; never called for duplicate deliveries.
- **Amazon Amplify Hosting**: Static dashboard hosting that scales with demand.

---

## What We Learned

This section will be updated with specific lessons as implementation progresses.

---

## AI Tools Used

- ChatGPT (OpenAI) — planning and architecture review
- Google Antigravity — development environment

Additional tools will be listed here if used during implementation.

---

## First Commit Submission

- **Track**: Ship It
- **Deployment**: Amazon Amplify Hosting (live dashboard URL provided at submission)
- **Scope**: Serverless end-to-end vertical slice

---

## Repository Structure

```text
AGENTS.md                      # Root agent router & global directives
.agent-rules/                  # Modular task-specific rule system
TASKS.md                       # Implementation plan (T01–T13) with P0/P1 priorities
.env.example                   # Environment configuration template

docs/
├── ARCHITECTURE.md            # Architectural specification & cost discipline
└── EVALS.md                   # Systematic evaluations (E01–E16) & testing principles
```

---

## Implementation Status

T01 webhook ingress is implemented and unit-tested. Live AWS/GitHub verification is pending. T02 and later pipeline stages are not yet implemented.
