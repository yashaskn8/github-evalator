# Implementation Tasks — GitHub Evalator

> **Rule**: Do not begin the next task until the current task's relevant eval/red-team gate passes.
> **Priority Guidance**:
> - **P0** — Must work for hackathon submission.
> - **P1** — Should work if P0 is complete. If time is short, **CUT P1**. Never weaken P0.
> - **P2** — Post-hackathon / out of scope.

---

## T01 [P0] — GitHub App + API Gateway + Webhook Ingress

- [ ] IMPLEMENTED — LIVE VERIFICATION PENDING

| Field | Value |
| :--- | :--- |
| **Goal** | Create GitHub App registration, configure webhook events (`issues`, `issue_comment`, `pull_request`), deploy API Gateway HTTP/REST endpoint and minimal Webhook Lambda. |
| **Dependencies** | None (initial ingress task). |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md`, `12-hackathon-scope.md` |
| **Output** | Working GitHub App; API Gateway endpoint routing POST to Lambda; minimal Lambda handler returning HTTP 202; safe structured telemetry logging. |
| **Relevant Evals** | Webhook connectivity check. |
| **Red-Team Gate** | Deliver real signed GitHub webhook; Lambda extracts headers, acknowledges within sub-second SLO, and emits safe structured telemetry. |
| **Definition of Done** | Real GitHub webhook → API Gateway → Lambda → safe structured telemetry visible in CloudWatch. **No raw or private payload logged.** |

---

## T02 [P0] — HMAC Verification + Raw-Body Signature + Delivery Extraction

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement constant-time `X-Hub-Signature-256` HMAC-SHA256 signature verification using secret ARN from AWS Secrets Manager. Extract `X-GitHub-Delivery` GUID and normalize event payload. |
| **Dependencies** | T01 |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md`, `08-security-threat-model.md` |
| **Output** | Cryptographic verification module; extraction of correlation IDs (`githubDeliveryId`); rejection of invalid signatures with HTTP 401. |
| **Relevant Evals** | E-manual (forged webhook rejection). |
| **Red-Team Gate** | Send payload with forged signature → immediate HTTP 401. Valid signature → HTTP 202. |
| **Definition of Done** | Forged signature rejected with 401. Valid signature admitted. Delivery GUID and event metadata normalized. Zero plaintext secrets in code or logs. |

---

## T03 [P0] — DynamoDB Authoritative State Model

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Create single-table DynamoDB schema supporting: Event admission (`EVENT#<deliveryId>`), Issue, Lease, Qualification, VerificationRun, and SideEffect (idempotency) entities. |
| **Dependencies** | T01 |
| **Rules to Consult** | `01-core-invariants.md`, `06-dynamodb-state-leases.md` |
| **Output** | DynamoDB table definition (IaC); single-table partition/sort key schema; entity types and conditional write expressions. |
| **Relevant Evals** | Table contract validation. |
| **Red-Team Gate** | Verify conditional expressions and transactional schemas for all entities without read-check-write flaws. |
| **Definition of Done** | Table deployed. PutItem/GetItem/TransactWriteItems expressions validated for each entity type. Key schema strictly matches `06-dynamodb-state-leases.md`. |

---

## T04 [P0] — Amazon SQS Standard + Dead-Letter Queue (DLQ)

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Provision Amazon SQS Standard queue and Dead-Letter Queue (DLQ). Configure Webhook Lambda to enqueue validated normalized events to SQS Standard. |
| **Dependencies** | T02, T03 |
| **Rules to Consult** | `01-core-invariants.md`, `02-aws-architecture.md`, `03-github-webhooks.md` |
| **Output** | SQS Standard queue + DLQ (`maxReceiveCount = 3`); Webhook Lambda enqueues normalized events with correlation attributes. |
| **Relevant Evals** | E16 (poison message isolation). |
| **Red-Team Gate** | Simulate poison event → retried and isolated into DLQ without blocking main queue. Valid message → enqueued within sub-second latency. |
| **Definition of Done** | Validated webhook → SQS Standard. Poison message moves to DLQ after exhaustion. SQS provides burst buffering; authoritative deduplication explicitly reserved for T05. |

---

## T05 [P0] — Dispatcher Lambda + Atomic Event Admission

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement Dispatcher Lambda triggered by SQS Standard. Perform atomic event admission in DynamoDB (`attribute_not_exists(PK)` on `EVENT#<deliveryId>`). Safely drop duplicate deliveries and start exactly one Step Functions workflow. |
| **Dependencies** | T03, T04 |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md`, `06-dynamodb-state-leases.md`, `07-step-functions-workflows.md` |
| **Output** | Dispatcher Lambda; atomic delivery admission logic; duplicate drop logic; Step Functions invocation trigger. |
| **Relevant Evals** | E07 (10 duplicate deliveries produce 1 logical execution). |
| **Red-Team Gate** | Send identical delivery ID 10× concurrently → exactly 1 admission succeeds, 9 safely dropped as duplicates. Zero duplicate Step Functions workflows. |
| **Definition of Done** | Delivery deduplication proven via DynamoDB conditional writes. Duplicate webhooks do not fail auth or crash, but safely drop redundant processing. |

---

## T06 [P0] — Bedrock Structured Proposal Parser

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement Bedrock claim extraction task using `bedrock-runtime` with native JSON Schema structured output (Claude Sonnet 4.6 or configurable active model). Wrap untrusted input in delimiters. |
| **Dependencies** | T05 |
| **Rules to Consult** | `01-core-invariants.md`, `04-bedrock-ai-boundary.md` |
| **Output** | Lambda function with T06 preflight (verifies model availability and structured output support); invokes Bedrock Converse/InvokeModel with JSON Schema; validates structured claims. |
| **Relevant Evals** | E04, E05, E06, E15 |
| **Red-Team Gate** | T06 preflight fails visibly if model unavailable. Prompt injection test (E04) extracts technical claims without executing attacker instructions. Malformed JSON caught by schema validation. |
| **Definition of Done** | Real Bedrock call returns typed structured claims (`affected_files`, `target_symbols`, `test_strategy`). Preflight passes. Prompt injections isolated in `<untrusted_contributor_text>`. |

---

## T07 [P0] — Deterministic Repository Verifier + Qualification Policy

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement two separate pipeline components: (1) Deterministic Repository Verifier (Python AST static parser, language-independent Git tree inspector), and (2) Qualification Policy Engine. |
| **Dependencies** | T06 |
| **Rules to Consult** | `01-core-invariants.md`, `05-repository-verifier.md` |
| **Output** | Verifier module producing evidence records (`SUPPORTED`, `CONTRADICTED`, `UNKNOWN`) with pinned commit SHA; Policy engine module producing decision (`VERIFIED`, `NEEDS_REVISION`, `ESCALATED`). |
| **Relevant Evals** | E01, E02, E03, E06 |
| **Red-Team Gate** | Nonexistent file → `CONTRADICTED`. Real file, missing symbol → `CONTRADICTED`. Real file and symbol → `SUPPORTED`. Conservative policy: `VERIFIED` only if required claims supported and target bound. AI confidence alone never yields `VERIFIED`. |
| **Definition of Done** | Independent static analysis against real repository tree/AST. Granular evidence records stored with commit provenance. Policy engine strictly gates qualification. Zero contributor code execution. |

---

## T08 [P0] — Atomic Qualification + Lease Transaction

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement DynamoDB `TransactWriteItems` for atomic lease acquisition: verify qualification is `VERIFIED` & unconsumed → mark consumed → create Lease record → update Issue `activeLeaseId` and increment `version` in a single transaction. Enforce maintainer fencing. |
| **Dependencies** | T03, T07 |
| **Rules to Consult** | `01-core-invariants.md`, `06-dynamodb-state-leases.md` |
| **Output** | Atomic transaction logic; conditional check expressions; stale-worker fencing; maintainer version check. |
| **Relevant Evals** | E08 (100 concurrent claims), E09 (stale worker fencing), E13 (maintainer override). |
| **Red-Team Gate** | 100 concurrent workers on fresh Issue #43 attempt lease acquisition → exactly 1 succeeds, 99 fail with `TransactionCanceledException`. Stale worker fails conditional write. |
| **Definition of Done** | 100-worker concurrency proof: 100 transactions, 1 success, 99 conditional failures, exactly 1 active lease. Zero double-assignments. Fencing preserves maintainer authority. |

---

## T09 [P0] — GitHub Assignment Side Effect (Idempotent)

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement idempotent GitHub issue assignment and evidence breakdown comment posting. Protect all external calls with internal DynamoDB idempotency records. Handle timeout-after-success via external state reconciliation. |
| **Dependencies** | T08 |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md` |
| **Output** | Lambda calling GitHub REST API; pre-flight external check on retry; evidence markdown formatter; idempotency record persistence. |
| **Relevant Evals** | E10 (timeout-after-success reconciliation). |
| **Red-Team Gate** | Simulate network timeout after successful GitHub call → retry reconciles without creating duplicate comments or duplicate assignments. |
| **Definition of Done** | Real GitHub issue assigned. Real structured evidence comment posted. Timeout retry produces zero duplicate side effects. |

---

## T10 [P0] — PR Integrity Workflow

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement PR Integrity Step Functions workflow triggered by `pull_request` events. Load verified proposal intent from DynamoDB, inspect unified diff against qualified scope, and evaluate consistency (`PASS` vs `DRIFT`). |
| **Dependencies** | T07, T09 |
| **Rules to Consult** | `01-core-invariants.md`, `05-repository-verifier.md`, `11-pr-integrity.md` |
| **Output** | Step Functions PR workflow; deterministic diff scope inspector; semantic diff evaluation; PR comment / check run emitter. |
| **Relevant Evals** | E11, E12 (Same PR head SHA A `PASS` → head SHA B `DRIFT`). |
| **Red-Team Gate** | Charlie's PR at commit A (matching files) → `PASS`. Charlie pushes commit B touching unrelated file (`billing/stripe.ts`) → `DRIFT` and maintainer review. |
| **Definition of Done** | Real PR workflow validates unified diff against original proposal intent. Same-PR drift detected and posted to GitHub. Closes the evaluation loop. |

---

## T11 [P0] — Read-Only Evidence Dashboard (Amplify Hosting)

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Deploy minimal React maintainer/evidence dashboard to Amazon Amplify Hosting with a read-only API Gateway endpoint. Provides public visibility into system state and fulfills the live deployment URL requirement. |
| **Dependencies** | T03, T08, T10 |
| **Rules to Consult** | `01-core-invariants.md`, `02-aws-architecture.md`, `12-hackathon-scope.md` |
| **Output** | Static React application hosted on Amplify Hosting; read-only API query Lambda; evidence visualizer (proposal status, claims, active lease, PR integrity, operational counters). |
| **Relevant Evals** | Live URL accessibility check. |
| **Red-Team Gate** | Unauthenticated read-only access operates smoothly; dashboard renders real DynamoDB state and evidence without authentication roadblocks. |
| **Definition of Done** | Live URL accessible. Shows real proposal status, supported/contradicted evidence, active lease owner, PR integrity status, and demo operational counters. Zero vanity bloat. |

---

## T12 [P1] — CloudWatch Observability & Operational Alarms

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Instrument all production components (T01–T11) with structured JSON logging and correlation IDs (`githubDeliveryId`, `verificationId`, `leaseId`). Emit CloudWatch EMF metrics for operations and alarm on DLQ depth. |
| **Dependencies** | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11 (all production components) |
| **Rules to Consult** | `01-core-invariants.md`, `02-aws-architecture.md` (§6) |
| **Output** | Correlation propagation across all Lambdas; CloudWatch EMF metrics (`WebhookLatency`, `LeaseConflicts`, `ProposalsVerified`, `IntegrityDrifts`); DLQ depth alarm. |
| **Relevant Evals** | Proves `LeaseConflicts` metric matches actual observed concurrency test value (99). |
| **Red-Team Gate** | Random audit of `verificationId` reconstructs entire execution trace. Trace confirms zero private repository code or credentials logged. |
| **Definition of Done** | All production components instrumented. CloudWatch EMF metrics emitted. `LeaseConflicts` metric matches actual observed race test. |

---

## T13 [P0] — Full End-to-End Integration Validation & Submission Checklist

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Execute the complete ≤3-minute demonstration scenario (docs/DEMO.md) on deployed AWS infrastructure and verify all 16 evaluations (E01–E16). Complete final submission checklist. |
| **Dependencies** | T01–T11 (P0 core), T12 (P1 if implemented) |
| **Rules to Consult** | `01-core-invariants.md`, `docs/EVALS.md`, `12-hackathon-scope.md` |
| **Output** | Live demo verification; all evals passing; ≤3-minute demo video recorded; final submission checklist completed. |
| **Relevant Evals** | E01–E16 complete sweep. |
| **Red-Team Gate** | Adversarial attack suite passes. Zero fake metrics, zero hardcoded responses, zero mocked production paths. |
| **Definition of Done** | Working vertical slice verified end-to-end. Video recorded under 3 minutes. All checklist items checked. |

### Final T13 Submission Checklist
- [ ] repository public
- [ ] Git history preserved
- [ ] no secrets committed
- [ ] live deployed URL accessible without authentication roadblocks
- [ ] AWS functionality visibly demonstrated
- [ ] <=3 minute video recorded
- [ ] problem clearly explained
- [ ] AWS role clearly explained
- [ ] writeup completed
- [ ] AI coding tools disclosed
- [ ] third-party dependencies credited/licensed
- [ ] important features visible in video

