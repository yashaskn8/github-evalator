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
Amazon Bedrock (Extracts Structured, Falsifiable Claims)
    │
    ▼
Deterministic Repository Verifier (Validates Claims Against AST & Git Tree)
    │
    ▼
Policy Evaluation Engine (Verifies Evidence Thresholds)
    │
    ▼
Amazon DynamoDB (Atomic Conditional Lease Grant — Max 1 Active Owner)
    │
    ▼
GitHub API (Automated Issue Assignment & Evidence Breakdown Comment)
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
2. **Evidence-Backed Assignment Gate**: DynamoDB conditional transactions ensure that exactly **one** qualified contributor receives an exclusive, time-bounded issue lease. Unqualified "assign me" spam is rejected.
3. **Proposal → PR Integrity**: When a PR is opened, the system verifies that the actual unified diff implements the specific file scope and architectural approach that earned the contributor the lease (`PASS` vs. `DRIFT`).
4. **Safe Autonomous Escalation**: Human maintainer actions always override automation. Ambiguous proposals, prompt injections, or stale workers gracefully escalate without granting unauthorized access.

---

## Approved AWS Architecture

```text
GitHub Webhooks
   │
   ▼
Amazon API Gateway (HTTP/REST Ingress)
   │
   ▼
AWS Lambda (Webhook Verification & Fast SQS Enqueue — Sub-second ACK SLO)
   │
   ▼
Amazon SQS + Dead-Letter Queue (At-Least-Once Delivery Buffer)
   │
   ▼
AWS Lambda (Dispatcher)
   │
   ▼
AWS Step Functions Standard (Auditable Workflow Orchestration)
   ├── 1. Repository Retrieval (Tree & AST Caching)
   ├── 2. Amazon Bedrock (Structured Claim Extraction)
   ├── 3. Deterministic Verifier (AST & Symbol Ground-Truth Inspection)
   ├── 4. Policy Engine (Evidence Evaluation)
   ├── 5. Amazon DynamoDB (Atomic Conditional Lease Acquisition)
   └── 6. GitHub API (Idempotent Assignment & Comment Side Effects)
```

**Supporting Services**:
- **Amazon S3**: Persistent storage for raw payloads, AST snapshots, and verification evidence.
- **AWS Secrets Manager**: Secure storage for GitHub App private keys and webhook HMAC secrets.
- **Amazon CloudWatch**: Operational logging and Embedded Metric Format (EMF) metrics.

*GitHub serves as the external system of record.*

---

## Strict Trust Boundaries

- **Bedrock NEVER grants issue ownership**: AI reasons over natural language to extract structured hypotheses. Deterministic code verifies those hypotheses against repository code. DynamoDB owns absolute authority over leases and state transitions.
- **Repository content is untrusted data**: Issue bodies, READMEs, and source code are treated as untrusted input and isolated in delimited envelopes. Untrusted instructions cannot alter system policy.
- **Static inspection only**: The MVP performs AST and file tree analysis; it never executes arbitrary contributor code.

---

## 3-Minute Demo Scenario Summary

- **Alice** posts `"assign me please"` → Rejected (no technical proposal).
- **Bob** posts `"I'll fix this in src/client.py"` → Rejected (file exists, but symbol missing / claim `CONTRADICTED`).
- **Charlie** posts a structured plan referencing `src/retry.py` and `tests/test_retry.py` → **VERIFIED** by AST inspection.
- **Race Condition Attack**: 100 simultaneous simulated claims attempt to acquire the lease → **Exactly 1 lease granted** via DynamoDB atomic transaction; 99 rejected.
- **PR Integrity**:
  - Charlie submits a PR modifying `src/retry.py` and adding tests → **PASS**.
  - Malicious / drifted PR touches `billing/stripe.ts` → **DRIFT** (flagged for maintainer review).

---

## Repository Structure

```text
AGENTS.md                      # Root agent router & global invariants
.agent-rules/                  # Modular task-specific rule system (00 to 13)
TASKS.md                       # Strict implementation order (T01–T13)
.env.example                   # Environment configuration template

docs/
├── PRD.md                     # Product Requirements Document
├── ARCHITECTURE.md            # Complete architectural specification & state machines
├── THREAT_MODEL.md            # Hostile threat matrix & mitigations
├── EVALS.md                   # Systematic evaluations (E01–E16)
└── DEMO.md                    # 3-minute live demonstration script
```

---

## Status

**Pre-implementation architecture and evaluation baseline.** All specifications, threat models, agent rules, and evaluation criteria are locked and ready for Step 1 implementation.
