# 00 — Task Routing Matrix

> **OBJECTIVE**: Minimize context usage by loading only the minimum sufficient rule files required for each lifecycle phase.

---

## 1. Phased Task Loading Matrix

### Phase A: Implementation (Read ONLY the files listed under Phase A)

| Task Category | Phase A: Implementation Rules | Explicit Exclusions (Do Not Read in Phase A) | Key Invariant / Focus |
| :--- | :--- | :--- | :--- |
| **AWS Infrastructure & CDK** | `01`, `02` | `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `11`, `13` | Architecture boundaries; MVP default-deny unapproved services. |
| **IAM, Secrets & Auth Policy** | `01`, `08` | `02`*, `03`, `04`, `05`, `06`, `07`, `09`, `10`, `11`, `13` | Least-privilege IAM, Secrets Manager storage, short-lived tokens. (*Add `02` only if infra topology changes). |
| **GitHub Webhook Ingestion** | `01`, `03` | `02`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `11`, `13` | Fast ACK SLO; HMAC verification; enqueue to SQS; no sync AI. |
| **GitHub API & Side Effects** | `01`, `03`, `06` | `02`, `04`, `05`, `07`, `08`, `09`, `10`, `11`, `13` | Internal deterministic idempotency record for assignments/comments. |
| **Bedrock Prompts & Schemas** | `01`, `04` | `02`, `03`, `05`, `06`, `07`, `08`, `09`, `10`, `11`, `13` | Strict JSON schema; AI never writes authoritative state. |
| **Repository AST & Verifier** | `01`, `05` | `02`, `03`, `04`, `06`, `07`, `08`, `09`, `10`, `11`, `13` | Deterministic evidence (`SUPPORTED`/`CONTRADICTED`). No code execution. |
| **DynamoDB State & Leases** | `01`, `06` | `02`, `03`, `04`, `05`, `07`, `08`, `09`, `10`, `11`, `13` | Conditional writes & `TransactWriteItems`. No read-check-write. |
| **Step Functions & SQS** | `01`, `07` | `02`, `03`, `04`, `05`, `06`, `08`, `09`, `10`, `11`, `13` | Staged state machine; retry backoff; SQS dispatch & DLQ. |
| **Security & Threat Model** | `01`, `08` | `02`, `03`, `04`, `05`, `06`, `07`, `09`, `10`, `11`, `13` | Threat mitigations, repo isolation, input sanitization. |
| **Observability & Metrics** | `01`, `10` | `02`, `03`, `04`, `05`, `06`, `07`, `08`, `09`, `11`, `13` | Real CloudWatch EMF metrics; structured correlation logging. |
| **PR Diff & Drift Integrity** | `01`, `05`, `11` | `02`, `03`, `04`, `06`, `07`, `08`, `09`, `10`, `12`, `13` | Verified intent vs actual PR diff; `PASS`/`DRIFT`/`REVIEW`. |
| **Scope & Feature Triage** | `01`, `12` | `02`, `03`, `04`, `05`, `06`, `07`, `08`, `09`, `10`, `11` | 3-minute demo value test; reject scope bloat. |

---

## 2. Multi-Boundary Task Loading

When a task spans specific architectural boundaries, load `01-core-invariants.md` plus ONLY the component files whose boundaries are actually crossed:

- **Step Functions task invoking Bedrock**: `01`, `04`, `07`
- **Atomic issue lease acquisition calling GitHub**: `01`, `03`, `06`
- **PR drift verification comparing AST intent**: `01`, `05`, `11`
- **SQS retry handling with idempotency table**: `01`, `03`, `06`, `07`
- **IAM secret permissions adjustment**: `01`, `08` (load `02` only if infra topology changes)

> **Boundary Rule**: Never load a component file simply because it is adjacent to the task.

---

## 3. Lifecycle Loading Protocol

1. **Phase A (Implementation)**: Load `AGENTS.md` + `01-core-invariants.md` + relevant component file(s). **Do NOT load `09` or `13`.**
2. **Phase B (Verification)**: Load `09-testing-red-team.md` **only after code is written** to execute the targeted red-team attack test.
3. **Phase C (Sign-off)**: Load `13-definition-of-done.md` **only when delivering the final completion report**.
