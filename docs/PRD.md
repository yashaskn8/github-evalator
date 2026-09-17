# Product Requirements Document — GitHub Evalator

> **Prevent the PR flood by qualifying implementation intent before granting issue ownership.**

---

## Problem Statement

Popular open-source repositories face a systemic operational problem: when a valuable issue is opened, it attracts a flood of low-context "assign me" comments and competing implementations. Maintainers lack a scalable mechanism to evaluate whether a contributor actually understands the codebase before granting ownership. The result is duplicate PRs, wasted contributor effort, and unsustainable review overhead.

**GitHub Evalator solves this by requiring contributors to demonstrate repository-grounded implementation intent before receiving exclusive issue ownership.**

---

## Users

| Role | Pain Points |
| :--- | :--- |
| **Open-Source Maintainer** (Primary) | Too many "assign me" comments; duplicate competing PRs; low-context contributions; unclear ownership; unsustainable review load. |
| **Contributor** (Secondary) | Working on issues someone else also implements; unclear ownership signals; good proposals lost among low-effort comments; wasted implementation time. |

---

## Product Goal

Prevent unnecessary PR floods by validating proposed implementation intent against actual repository structure before granting exclusive, time-bounded issue ownership.

---

## Core Workflow

```text
1. Issue opened on GitHub
2. Contributors submit implementation proposals (issue comments)
3. Bedrock extracts structured, falsifiable claims from each proposal
4. Deterministic Verifier inspects repository AST & Git tree for evidence
5. Policy Engine evaluates verification results against thresholds
6. DynamoDB atomic conditional transaction grants exclusive lease (max 1 per issue)
7. GitHub API assigns the issue to the qualified contributor (idempotent)
8. Contributor opens a Pull Request
9. PR Integrity Workflow compares unified diff against verified proposal intent
10. Outcome: PASS | DRIFT | MAINTAINER REVIEW
```

---

## The 4 Killer Features

1. **Repository-Grounded Proposal Verification** — Bedrock extracts structured claims; the deterministic verifier independently checks file existence, symbol presence, and test infrastructure against the actual Git tree.
2. **Evidence-Backed Assignment Gate** — DynamoDB conditional transactions enforce exactly one active lease per issue. Generic "assign me" requests without technical substance are rejected.
3. **Proposal → PR Integrity** — When a PR is opened, the system compares the actual diff against the verified implementation intent. Scope drift is flagged, not silently accepted.
4. **Safe Autonomous Escalation** — Ambiguous proposals, prompt injections, Bedrock failures, and stale workers escalate to human maintainers rather than granting unauthorized access. Maintainer authority always wins.

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

## Success Criteria

| Criterion | Measurable Proof |
| :--- | :--- |
| False repository claims are rejected | Eval E02, E03, E06: Verifier marks fabricated claims `CONTRADICTED`. |
| One active lease maximum per issue | Eval E08: 100 concurrent claims → exactly 1 lease granted. |
| Duplicate webhooks do not duplicate work | Eval E07: Same delivery ID ×10 → 1 logical workflow. |
| Stale workers cannot mutate new leases | Eval E09: Expired worker fails DynamoDB fencing condition. |
| Unrelated PRs flagged as DRIFT | Eval E12: PR diverging from verified intent → `DRIFT`. |
| Maintainer override always wins | Eval E13: Human reassignment persists; stale automation halts. |
| Prompt injection does not affect authority | Eval E04, E05: Adversarial input parsed but state unaffected. |
| GitHub timeout-after-success handled | Eval E10: Retry reconciles without duplicate assignment. |

---

## Trust Boundary

- **Bedrock does not grant issue ownership.** It extracts and interprets claims.
- **Deterministic code verifies claims** against real repository evidence.
- **DynamoDB performs authoritative state transitions** via conditional writes.
- **Repository content is untrusted data**, never system instructions.
- **PR integrity means matching verified intent**, not proving code correctness.
