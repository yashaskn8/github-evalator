# GitHub Evalator

> **Evidence-backed issue assignment for high-traffic open-source repositories.**
> *AI reasons. Deterministic code verifies. DynamoDB owns authority.*

---

## The Problem: The PR Flood

Popular open-source repositories face a recurring operational bottleneck:
- An issue is opened and immediately flooded with generic **"assign me"** comments.
- Maintainers lack time to vet whether contributors understand the codebase before granting ownership.
- Multiple contributors work concurrently in silos, producing competing, duplicate pull requests.
- Maintainers spend hours reviewing low-context, off-target, or broken PRs.

**GitHub Evalator attacks the PR flood before it happens.** It does not exist merely to review PRs faster after they are submitted; it qualifies implementation intent against actual repository reality before granting exclusive issue ownership.

---

## Core Execution Flow

```text
Issue Opened
    │
    ▼
Multiple Contributors Submit Proposals (Issue Comments)
    │
    ▼
Amazon Bedrock (Extracts Structured, Falsifiable Claims via JSON Schema)
    │
    ▼
Deterministic Repository Verifier (Validates Claims Against AST & Git Tree)
    │
    ▼
Policy Evaluation Engine (Verifies Evidence Thresholds → VERIFIED / NEEDS_REVISION / ESCALATED)
    │
    ▼
Amazon DynamoDB (Atomic Conditional Lease Grant — Max 1 Active Owner via TransactWriteItems)
    │
    ▼
GitHub API (Automated Issue Assignment & Idempotent Evidence Breakdown Comment)
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

## The 4 Killer Features

1. **Repository-Grounded Proposal Verification**: Bedrock extracts structured claims (`affected_files`, `target_symbols`, `test_strategy`), and a deterministic verifier independently checks the actual repository AST and Git tree. Deliberate falsehoods or hallucinations are marked `CONTRADICTED`.
2. **Evidence-Backed Assignment Gate**: DynamoDB conditional transactions ensure that exactly **one** qualified contributor receives an exclusive, time-bounded issue lease. Unqualified "assign me" requests are rejected.
3. **Proposal → PR Integrity Loop**: When a PR is opened, the system verifies that the actual unified diff implements the specific file scope and architectural approach that earned the contributor the lease (`PASS` vs. `DRIFT`).
4. **Safe Autonomous Escalation**: Human maintainer actions always override automation. Ambiguous proposals, prompt injections, or stale workers gracefully escalate without granting unauthorized access.

---

## Approved AWS Architecture (Ship It Track)

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
   ├── 3. Deterministic Verifier (Python AST & Symbol Ground-Truth Inspection)
   ├── 4. Policy Engine (Evidence Evaluation)
   ├── 5. Amazon DynamoDB (Atomic Conditional Lease Acquisition)
   └── 6. GitHub API (Idempotent Assignment & Comment Side Effects)

Maintainer / Judge
   │
   ▼
Amazon Amplify Hosting (Ship It Live URL)
   │
   ▼
Minimal React Maintainer / Evidence Dashboard
   │
   ▼
Read-Only Evidence / Status API
```

**Supporting Services**:
- **Amazon DynamoDB**: Single-table authoritative state, event admission, atomic leases, idempotency records.
- **Amazon S3 (Data Minimization)**: Verification evidence bundles, pinned analysis artifacts, and oversized payloads (>256 KB) only when required.
- **AWS Secrets Manager**: Secure storage for GitHub App private key ARN and webhook HMAC secret ARN.
- **Amazon CloudWatch**: Operational logging and Embedded Metric Format (EMF) metrics.
- **Amazon Amplify Hosting**: Static hosting for the minimal judge-facing evidence dashboard.

*GitHub serves as the external system of record.*

---

## Strict Trust Boundaries

- **Bedrock NEVER grants issue ownership**: AI reasons over natural language to extract structured hypotheses. Deterministic code verifies those hypotheses against repository code. DynamoDB owns absolute authority over leases and state transitions.
- **Repository content is untrusted data**: Issue bodies, READMEs, and source code are treated as untrusted input and isolated in delimited envelopes (`<untrusted_contributor_text>` and `<untrusted_repository_content>`). Untrusted instructions cannot alter system policy.
- **Static inspection only**: The MVP performs AST and file tree analysis; it never executes arbitrary contributor code.

---

## 3-Minute Demo Scenario Summary

- **Alice** posts `"assign me please"` → ❌ **Rejected** (no technical proposal).
- **Bob** posts `"I'll fix this in src/client.py"` → ⚠️ **NEEDS_REVISION** (file exists, but proposal lacks grounded implementation detail and repository evidence does not establish it as the relevant retry path).
- **Charlie** posts a structured plan referencing `src/retry.py` and `tests/test_retry.py` → ✅ **VERIFIED** by AST inspection (`retry_request confirmed in src/retry.py commit=<actual sha>`). Charlie atomically receives the exclusive lease on Issue #42.
- **Race Condition Demonstration**: On fresh **Issue #43**, 100 pre-qualified concurrency-test claimants execute parallel DynamoDB conditional transactions → **Exactly 1 succeeds**, 99 conflict failures.
- **PR Integrity Loop**:
  - Charlie submits a PR at **Head SHA A** modifying `src/retry.py` and `tests/test_retry.py` → ✅ **PASS**.
  - Charlie pushes a subsequent commit at **Head SHA B** additionally modifying `billing/stripe.ts` → ⚠️ **DRIFT** (flagged for maintainer review).
- **Live Evidence Dashboard**: Judges view real-time proposal statuses, evidence breakdowns, active leases, and operational counters on the live Amplify URL.

---

## Cost Discipline

- **AWS Lambda**: Pure serverless execution; zero idle compute cost.
- **Amazon SQS Standard**: Absorbs traffic spikes instead of overprovisioning compute.
- **AWS Step Functions Standard**: State transitions executed only when needed; fail-closed halts prevent runaway retries.
- **Amazon DynamoDB**: On-demand / serverless billing for conditional state transitions.
- **Amazon S3**: Strict data minimization; stores large evidence only when necessary, with automated lifecycle expiration.
- **Amazon Bedrock**: Invoked only after admission and deduplication succeed; bounded token budgets; never called for duplicate deliveries.
- **Amazon Amplify Hosting**: Static hosting with near-zero idle cost.

---

## What We Learned

<!-- Real learnings will be recorded here during implementation:
- DynamoDB conditional transactions and stale-worker fencing
- Step Functions retries/catch state isolation
- GitHub webhook HMAC-SHA256 signature verification on raw bytes
- Amazon Bedrock structured output generation via Converse/InvokeModel
- Distributed event deduplication with SQS Standard + DynamoDB
- Idempotent external side-effect execution against GitHub REST API
-->

---

## AI Coding Tools Used

- Claude Opus 4.6 / Claude Sonnet (Anthropic)
- Google Antigravity IDE

---

## Repository Structure

```text
AGENTS.md                      # Root agent router & global invariants
.agent-rules/                  # Modular task-specific rule system (00 to 13)
TASKS.md                       # Strict implementation order (T01–T13) with P0/P1 priorities
.env.example                   # Environment configuration template

docs/
├── PRD.md                     # Product Requirements Document
├── ARCHITECTURE.md            # Complete architectural specification & cost discipline
├── THREAT_MODEL.md            # Hostile threat matrix & realistic residual risks
├── EVALS.md                   # Systematic evaluations (E01–E16)
└── DEMO.md                    # 3-minute live demonstration script optimized for judging
```

---

## Status

**Pre-implementation architecture and evaluation baseline.** All specifications, threat models, agent rules, and evaluation criteria are locked and aligned with First Commit Ship It hackathon rules. Ready for T01 implementation upon event window opening.
