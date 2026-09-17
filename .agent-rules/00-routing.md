# 00 — Task Routing Matrix

> **OBJECTIVE**: Minimize agent context usage by loading only the 1–3 rule files strictly required for the immediate task.

---

## 1. Token-Efficient Loading Matrix

| Task Category | Primary Files to READ | Explicitly DO NOT READ (unless crossing boundary) | Key Invariant / Verification Gate |
| :--- | :--- | :--- | :--- |
| **AWS Infrastructure & CDK** | `01`, `02` | `03`, `04`, `05`, `06`, `07`, `10`, `11` | AWS logos are not architecture. No ECS/Aurora/Redis. |
| **GitHub Webhook & Signature** | `01`, `03`, `09` | `02`, `04`, `05`, `06`, `07`, `10`, `11`, `12` | Webhook < 500ms; verify `X-Hub-Signature-256`; no sync AI. |
| **GitHub API & Side Effects** | `01`, `03`, `06`, `09` | `02`, `04`, `05`, `07`, `10`, `11`, `12` | Deterministic deduplication key for comments & assignments. |
| **Bedrock Claims & Prompts** | `01`, `04`, `09` | `02`, `03`, `06`, `07`, `10`, `11`, `12` | Strict JSON schema; AI never transitions state; prompt injection safe. |
| **Repository AST / Verifier** | `01`, `05`, `09` | `02`, `03`, `04`, `06`, `07`, `10`, `11` | Deterministic evidence (`SUPPORTED`/`CONTRADICTED`). No code execution. |
| **DynamoDB State & Leases** | `01`, `06`, `09` | `02`, `03`, `04`, `05`, `07`, `10`, `11`, `12` | Conditional writes / transactions. No read-check-write races. |
| **Step Functions & SQS** | `01`, `07`, `09` | `03`, `04`, `05`, `06`, `10`, `11`, `12` | Staged state machine; retry policies; SQS visibility timeout. |
| **Security, IAM & Secrets** | `01`, `08`, `09` | `02`, `03`, `04`, `05`, `07`, `10`, `11`, `12` | Secrets Manager for keys; least-privilege IAM; repo isolation. |
| **Observability & Metrics** | `01`, `10` | `02`, `03`, `04`, `05`, `06`, `07`, `08`, `11` | Real CloudWatch EMF metrics; no fake/hardcoded demo data. |
| **PR Integrity & Diff Check** | `01`, `05`, `11`, `09` | `02`, `03`, `04`, `06`, `07`, `10`, `12` | Verified intent vs actual PR diff; `PASS`/`DRIFT`/`REVIEW`. |
| **Scope & Feature Triage** | `01`, `12` | `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10` | 3-minute demo test; kill non-essential features (e.g. sandbox). |
| **Task Completion Review** | `01`, `13`, `09` | `02`, `03`, `04`, `05`, `06`, `07`, `10`, `11` | Pass red-team attack matrix; output mandatory completion template. |

---

## 2. Multi-Boundary Task Loading

When a task spans across two components, load `01-core-invariants.md` plus both specific files:

- **Adding a Step Functions task calling Bedrock**: Read `01`, `04`, `07`.
- **Assigning an issue via DynamoDB lease**: Read `01`, `03`, `06`.
- **Verifying PR diff with verifier output**: Read `01`, `05`, `11`.
- **Handling SQS retry with idempotency table**: Read `01`, `03`, `06`, `07`.

---

## 3. Enforcement Rules for Coding Agents

1. **Do not read all files on startup.** Reading all `.agent-rules/` files without justification degrades reasoning and consumes context budget.
2. **Always consult `01-core-invariants.md`.** Invariants apply universally across every task.
3. **Always consult `09-testing-red-team.md` before claiming a task is done.** Every state change or external call must be backed by an adversarial attack test.
