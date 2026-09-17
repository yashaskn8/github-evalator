# Implementation Tasks — GitHub Evalator

> **Rule**: Do not begin the next task until the current task's relevant eval/red-team gate passes.

---

## T01 — GitHub App + Webhook Ingress

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Create a GitHub App, configure webhook events (`issues`, `issue_comment`, `pull_request`), and deploy an API Gateway + Lambda endpoint to receive webhooks. |
| **Dependencies** | None (first task). |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md` |
| **Output** | Working GitHub App; API Gateway endpoint receiving webhook POST requests; Lambda function logging payloads. |
| **Relevant Evals** | — |
| **Red-Team Gate** | Lambda receives and logs a real webhook from GitHub. |
| **Definition of Done** | Real GitHub webhook delivered to API Gateway → Lambda invoked → payload logged in CloudWatch. |

---

## T02 — Webhook HMAC Verification + Delivery Identity

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Validate `X-Hub-Signature-256` HMAC-SHA256 signature. Extract `X-GitHub-Delivery` GUID. Store delivery ID for idempotency. Reject invalid/replayed requests. |
| **Dependencies** | T01 |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md`, `08-security-threat-model.md` |
| **Output** | HMAC verification logic; delivery ID idempotency guard in DynamoDB; invalid webhooks return `401`. |
| **Relevant Evals** | E07 |
| **Red-Team Gate** | Send forged payload → `401`. Send same delivery ID 10× → exactly 1 admitted. |
| **Definition of Done** | Forged webhook rejected. Duplicate delivery ID deduplicated. Real webhook admitted and logged. |

---

## T03 — DynamoDB Base Domain Model

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Create single-table DynamoDB schema supporting: Issue, Lease, Qualification, VerificationRun, SideEffect (idempotency) entities. |
| **Dependencies** | T01 |
| **Rules to Consult** | `01-core-invariants.md`, `06-dynamodb-state-leases.md` |
| **Output** | DynamoDB table definition (CDK or SAM); entity key schema documented; seed test scripts. |
| **Relevant Evals** | — |
| **Red-Team Gate** | Schema supports all entity types; conditional write expressions parse correctly. |
| **Definition of Done** | Table deployed. PutItem/GetItem for each entity type succeeds. Key schema matches `06-dynamodb-state-leases.md`. |

---

## T04 — SQS Queue + Dead-Letter Queue

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Create SQS queue (with DLQ) for webhook event buffering. Wire Webhook Lambda to enqueue events. |
| **Dependencies** | T02 |
| **Rules to Consult** | `01-core-invariants.md`, `02-aws-architecture.md` |
| **Output** | SQS queue + DLQ; Webhook Lambda enqueues validated events; DLQ alarm configured. |
| **Relevant Evals** | E16 |
| **Red-Team Gate** | Poison message → DLQ after retry exhaustion. Healthy message → processed. |
| **Definition of Done** | Validated webhook → SQS. Poison message → DLQ. CloudWatch alarm fires on DLQ depth. |

---

## T05 — Step Functions Skeleton

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Create Step Functions Standard state machine with placeholder states matching the proposal verification workflow stages. Wire Dispatcher Lambda to trigger workflow from SQS. |
| **Dependencies** | T04 |
| **Rules to Consult** | `01-core-invariants.md`, `07-step-functions-workflows.md` |
| **Output** | State machine definition with stages: LOAD_CONTEXT → PARSE_PROPOSAL → RETRIEVE_EVIDENCE → EXTRACT_CLAIMS → VERIFY_CLAIMS → POLICY_EVALUATION → DECISION → OPTIONAL_LEASE → GITHUB_SIDE_EFFECT. Dispatcher Lambda triggered by SQS. |
| **Relevant Evals** | — |
| **Red-Team Gate** | SQS message triggers Step Functions execution. All stages visited in order. Execution visible in console. |
| **Definition of Done** | End-to-end flow: Webhook → SQS → Dispatcher → Step Functions execution started and completed with placeholder Pass states. |

---

## T06 — Bedrock Structured Proposal Parser

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement the EXTRACT_CLAIMS Step Functions task. Invoke Bedrock with structured prompt; parse JSON output via schema validation; extract typed claims. |
| **Dependencies** | T05 |
| **Rules to Consult** | `01-core-invariants.md`, `04-bedrock-ai-boundary.md` |
| **Output** | Lambda invoking Bedrock with untrusted text delimiters; Pydantic/JSON Schema validation; structured claims array. |
| **Relevant Evals** | E04, E05, E06, E15 |
| **Red-Team Gate** | Prompt injection in issue → claims extracted but no policy override. Malformed JSON → validation error, no state transition. |
| **Definition of Done** | Real Bedrock call returns structured claims. Schema validation catches malformed output. Prompt injection text parsed without altering system behavior. |

---

## T07 — Deterministic Repository Verifier

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement the VERIFY_CLAIMS Step Functions task. Independently inspect Git tree for file existence, symbol presence, test infrastructure. Output granular evidence records. |
| **Dependencies** | T06 |
| **Rules to Consult** | `01-core-invariants.md`, `05-repository-verifier.md` |
| **Output** | Lambda that checks Git tree/AST at pinned commit SHA; produces per-claim `SUPPORTED`/`CONTRADICTED`/`UNKNOWN` evidence records with provenance. |
| **Relevant Evals** | E01, E02, E03, E06 |
| **Red-Team Gate** | Nonexistent file → `CONTRADICTED`. Real file, wrong symbol → `CONTRADICTED`. Real file, real symbol → `SUPPORTED`. Bedrock hallucination overridden. |
| **Definition of Done** | Verifier independently confirms/denies every Bedrock claim against actual repository state. Evidence records include commit SHA, file path, symbol match. |

---

## T08 — Atomic Qualification + Lease Transaction

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement DynamoDB `TransactWriteItems` for atomic lease acquisition: verify qualification → consume qualification → create lease → update issue → all in one transaction. |
| **Dependencies** | T03, T07 |
| **Rules to Consult** | `01-core-invariants.md`, `06-dynamodb-state-leases.md` |
| **Output** | Atomic transaction logic with conditional expressions; stale-worker fencing; version incrementing. |
| **Relevant Evals** | E08, E09 |
| **Red-Team Gate** | 100 concurrent workers → exactly 1 lease. Stale worker → conditional write fails. No read-check-write pattern. |
| **Definition of Done** | 100-worker concurrency test passes. Stale-worker fencing verified. Zero double-assignments. |

---

## T09 — GitHub Assignment Side Effect

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement idempotent GitHub issue assignment and evaluation comment posting. Handle timeout-after-success via external state reconciliation. |
| **Dependencies** | T08 |
| **Rules to Consult** | `01-core-invariants.md`, `03-github-webhooks.md` |
| **Output** | Lambda calling GitHub API with internal idempotency records; pre-flight external state check on retry. |
| **Relevant Evals** | E10 |
| **Red-Team Gate** | Timeout-after-success → retry reconciles without duplicate. Idempotency key prevents duplicate comment. |
| **Definition of Done** | Real GitHub assignment. Real evaluation comment. Timeout retry produces zero duplicates. |

---

## T10 — PR Integrity Workflow

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement PR verification state machine: load verified intent → load PR diff → deterministic file check → Bedrock semantic comparison → policy → PASS/DRIFT/REVIEW. |
| **Dependencies** | T07, T09 |
| **Rules to Consult** | `01-core-invariants.md`, `05-repository-verifier.md`, `11-pr-integrity.md` |
| **Output** | Step Functions PR workflow; deterministic file scope check; Bedrock semantic diff comparison; GitHub check run / comment posting. |
| **Relevant Evals** | E11, E12 |
| **Red-Team Gate** | Matching PR → `PASS`. Divergent PR → `DRIFT`. Ambiguous → `REVIEW`. |
| **Definition of Done** | Real PR triggers integrity workflow. Matching scope → PASS. Unrelated changes → DRIFT with warning comment. |

---

## T11 — Maintainer Override

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Detect and defer to human maintainer actions. When a maintainer manually reassigns or closes an issue, automated workflows must fail their fencing condition gracefully. |
| **Dependencies** | T08 |
| **Rules to Consult** | `01-core-invariants.md`, `06-dynamodb-state-leases.md`, `08-security-threat-model.md` |
| **Output** | Webhook handler for `issues.assigned` / `issues.unassigned` maintainer events; version increment logic; stale workflow detection. |
| **Relevant Evals** | E13 |
| **Red-Team Gate** | Maintainer reassigns → old workflow fails conditional write → maintainer state preserved. |
| **Definition of Done** | Maintainer override persists. No automation overwrite. Stale workflow halts cleanly. |

---

## T12 — CloudWatch Observability

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Implement structured JSON logging with correlation IDs across all Lambdas. Emit CloudWatch EMF metrics for operational visibility. |
| **Dependencies** | T09 |
| **Rules to Consult** | `01-core-invariants.md`, `10-observability.md` |
| **Output** | Structured logs with `githubDeliveryId`, `verificationId`, `leaseId`, etc. EMF metrics: `WebhookLatency`, `LeaseConflicts`, `ProposalsVerified`, etc. |
| **Relevant Evals** | — |
| **Red-Team Gate** | Pick any random `verificationId` → reconstruct full decision from logs + evidence store. |
| **Definition of Done** | All correlation IDs propagated. Real EMF metrics emitted. Decision auditability verified. No private source code in logs. |

---

## T13 — Full End-to-End Hostile Demo Validation

- [ ] NOT STARTED

| Field | Value |
| :--- | :--- |
| **Goal** | Execute the complete 3-minute demo scenario (docs/DEMO.md) with real GitHub events, real Bedrock calls, real DynamoDB races, and real CloudWatch metrics. Run all 16 evals. |
| **Dependencies** | T01–T12 |
| **Rules to Consult** | `01-core-invariants.md`, `09-testing-red-team.md`, `13-definition-of-done.md` |
| **Output** | Complete end-to-end run. All evals passing. Demo script executable with real infrastructure. |
| **Relevant Evals** | E01–E16 |
| **Red-Team Gate** | Full adversarial attack matrix from `09-testing-red-team.md`. |
| **Definition of Done** | All 16 evals pass. 3-minute demo reproducible with real data. Zero fake behavior. |
