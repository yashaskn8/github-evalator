# 3-Minute Demo Script — GitHub Evalator

> **DO NOT FAKE**: GitHub events, Bedrock responses, DynamoDB lease races, GitHub assignments, PR diffs, or CloudWatch values. Every demo action must be real.

---

## Demo Timeline

### 0:00–0:20 — The Problem

> *"Open-source maintainers face a PR flood. A single popular issue attracts dozens of 'assign me' comments and competing PRs from contributors who haven't read the codebase. Maintainers waste hours reviewing duplicate, off-target work."*

> *"GitHub Evalator prevents the flood before it happens — by qualifying implementation intent against actual repository code before anyone gets ownership."*

---

### 0:20–1:00 — Repository-Grounded Proposal Verification (Live)

**Show GitHub Issue #42**: `"Add exponential backoff to retry logic"`

**Three proposals appear as issue comments:**

| Contributor | Proposal | Outcome |
| :--- | :--- | :--- |
| **Alice** | `"assign me please"` | ❌ **Rejected** — No technical proposal. No claims to verify. |
| **Bob** | `"I'll fix this in src/client.py"` | ❌ **Rejected** — File exists but proposed symbol not found. Claim `CONTRADICTED`. |
| **Charlie** | `"I'll modify retry_request in src/retry.py to add exponential backoff with jitter, and add a regression test in tests/test_retry.py"` | ✅ **VERIFIED** — Both files exist, `retry_request` symbol confirmed at line 42, test directory confirmed. |

**Live evidence on screen:**
- Bedrock's structured JSON claim extraction.
- Deterministic Verifier output showing `SUPPORTED` / `CONTRADICTED` per claim.
- VerificationRun record with commit SHA, file paths, and symbol matches.

---

### 1:00–1:35 — Evidence-Backed Atomic Assignment (Live)

**Show DynamoDB Transaction:**
- Charlie's `VERIFIED` qualification consumed atomically.
- Lease record created with expiration timestamp and generation ID.
- Issue #42 `activeLeaseId` set in the same transaction.
- GitHub API assigns Charlie to Issue #42 (real API call).
- Structured evaluation comment posted on the issue with evidence breakdown.

**On screen:** DynamoDB item view showing exactly one active lease.

---

### 1:35–2:05 — Race-Condition Attack (Live)

**Launch 100 simultaneous simulated workers** all attempting to acquire the same issue lease.

**Show results in real-time:**
- ✅ **Exactly 1 lease granted** (DynamoDB `TransactWriteItems` succeeded once).
- ❌ **99 rejected** (`TransactionCanceledException`).
- CloudWatch metric: `LeaseConflicts = 99`, `DuplicateAssignmentAttempts = 0`.

> *"This is not simulated. These are real concurrent DynamoDB transactions. The database enforces exactly-one-owner."*

---

### 2:05–2:35 — PR Integrity Check (Live)

**Scenario A — Matching PR:**
- Charlie opens PR modifying `src/retry.py` and `tests/test_retry.py`.
- PR Integrity Workflow compares diff against stored verified intent.
- Result: **PASS** ✅ — Files and scope match the qualified proposal.

**Scenario B — Drifted PR:**
- A different PR modifies `billing/stripe.ts` (completely unrelated).
- Result: **DRIFT** ⚠️ — Expected files not touched; unexpected files changed.
- Warning comment posted on the PR. Maintainer review triggered.

---

### 2:35–2:50 — AWS Architecture Overview

**Show Step Functions execution graph** (real execution, not a diagram):

```text
LOAD_CONTEXT → PARSE_PROPOSAL → RETRIEVE_EVIDENCE → EXTRACT_CLAIMS
→ VERIFY_CLAIMS → POLICY_EVALUATION → ACQUIRE_LEASE → GITHUB_SIDE_EFFECT
```

> *"Built entirely on serverless AWS: API Gateway, Lambda, SQS, Step Functions, Bedrock, DynamoDB, S3, CloudWatch. No containers, no clusters, no heavy frameworks."*

---

### 2:50–3:00 — Real Measured Outcomes

**Show actual CloudWatch metrics dashboard:**

| Metric | Value |
| :--- | :--- |
| Proposals verified | Real count |
| Proposals rejected | Real count |
| Lease conflicts (concurrent race) | 99 |
| Duplicate assignments | 0 |
| Webhook duplicates dropped | Real count |
| Average verification latency | Real ms |

> *"Every number is real. Every API call is live. This is evidence-backed issue assignment — not a chatbot, not a leaderboard, not a generic PR reviewer."*
