# Product Requirements Document — GitHub Evalator

> **Qualify implementation intent against repository reality before granting issue ownership.**

---

## Problem Statement

High-traffic open-source repositories frequently encounter an operational bottleneck when valuable issues are published: issues attract numerous generic "assign me" comments without implementation details. Maintainers have limited time to evaluate whether a contributor understands the codebase before granting ownership, which can lead to overlapping, competing pull requests, wasted effort, and high review overhead.

**GitHub Evalator evaluates proposed implementation intent against actual repository code before granting an exclusive issue lease.**

---

## Target Personas

| Role | Operational Context |
| :--- | :--- |
| **Open-Source Maintainer** (Primary) | Manages issue triage and assignment; seeks to reduce unvetted "assign me" claims and prevent duplicate or conflicting pull requests. |
| **Contributor** (Secondary) | Submits implementation plans for issues; benefits from clear ownership signals and early validation of proposed approaches. |
| **Project Observer / Reviewer** | Seeks transparency into system decisions, verification evidence, and operational telemetry via an accessible web dashboard. |

---

## Product Goal

Coordinate issue ownership by validating implementation proposals against actual repository structure before granting exclusive, time-bounded leases, with end-to-end evidence visualized on a read-only dashboard.

---

## Core Workflow

```text
1. Issue opened on GitHub
2. Contributors submit implementation proposals (issue comments)
3. Amazon Bedrock extracts structured claims from each proposal (via JSON Schema)
4. Deterministic Verifier inspects repository AST & Git tree for evidence (SUPPORTED / CONTRADICTED / UNKNOWN)
5. Policy Engine evaluates verification results against thresholds (VERIFIED / NEEDS_REVISION / ESCALATED)
6. DynamoDB atomic conditional transaction grants exclusive lease (max 1 active lease per issue via TransactWriteItems)
7. GitHub API assigns the issue to the qualified contributor and posts evidence breakdown (idempotent)
8. Contributor opens a Pull Request
9. PR Integrity Workflow compares unified diff against verified proposal intent (head SHA A PASS → head SHA B DRIFT)
10. Read-only Evidence Dashboard on Amazon Amplify visualizes the evaluation lifecycle in real time
```

---

## Core Capabilities

1. **Repository-Grounded Proposal Verification** — Amazon Bedrock extracts structured claims; the deterministic verifier independently checks file existence, symbol presence, and test infrastructure against the Git tree.
2. **Evidence-Backed Assignment** — DynamoDB conditional transactions enforce exactly one active lease per issue. Proposals without technical substance are rejected or flagged for revision.
3. **Proposal-to-PR Integrity Loop** — When a pull request is opened, the system compares the actual diff against the verified implementation intent. Scope drift across commits is flagged for maintainer review.
4. **Human Escalation on Uncertainty** — Ambiguous proposals, prompt injections, and stale workers escalate to human maintainers rather than making unauthorized state modifications. Maintainer actions on GitHub take precedence and reconcile cleanly.

---

## Non-Goals

The following are explicitly **out of scope**:

- Generic AI-powered PR reviewer or code quality checker.
- Code-generation agent or AI pair programmer.
- Contributor leaderboard or reputation scoring system.
- Arbitrary contributor code execution or sandbox environments.
- Social graph or cross-repository contributor analytics.
- IDE extensions, Slack/Discord integrations.
- Generic conversational chatbot.
- Automatic merge agent.
- ML model training or fine-tuning.

---

## Verification & Acceptance Criteria

| Area | Verification Criteria |
| :--- | :--- |
| Inaccurate claims rejected | Eval E02, E03, E06: Verifier marks fabricated or missing claims `CONTRADICTED`. |
| Single-lease guarantee | Eval E08: 100 concurrent claims on fresh Issue #43 → exactly 1 lease granted. |
| Duplicate event resilience | Eval E07: Same delivery ID ×10 → 1 logical workflow. |
| Stale worker fencing | Eval E09: Expired worker fails DynamoDB fencing condition. |
| Drift detection across commits | Eval E12: Same PR commit B adding unrelated file → `DRIFT`. |
| Maintainer precedence | Eval E13: Human reassignment persists; stale automation halts. |
| Prompt injection defense | Eval E04, E05: Adversarial input parsed as data; authoritative state unaffected. |
| Idempotent external side effects | Eval E10: Retry reconciles without duplicate assignment. |
| Live dashboard accessibility | Read-only Amplify URL displays real evidence and operational counters. |

---

## Trust Boundaries & System Authority

- **Bedrock does not grant issue ownership.** It parses and structures claims from natural language text.
- **Deterministic code verifies claims** against repository code and structure.
- **DynamoDB is authoritative for internal state transitions** via conditional writes, while GitHub maintainer actions take precedence and reconcile upon detection.
- **Repository content is untrusted data**, never system instructions.
- **PR integrity means matching verified intent**, not proving complete code correctness.
