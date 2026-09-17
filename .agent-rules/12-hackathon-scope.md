# 12 — Hackathon Scope & Anti-Overengineering

> **GOLDEN RULE**: Any scope expansion must demonstrably improve the core 3-minute live demonstration more than hardening the verification pipeline.
> **ONE WORKING FEATURE BEATS FIVE INCOMPLETE ONES.**

---

## 1. First Commit Hackathon Constitution

These constraints are permanent project rules for the **First Commit — Ship It** track:

### Event Validity
- The project implementation must be new during the First Commit hackathon window.
- Planning, learning, architecture design, and practice are allowed beforehand, but implementation submitted for judging must be hackathon work.
- Preserve Git history.
- **Do not**: squash away event history, rewrite timestamps unnecessarily, import an old completed project, or present pre-event implementation as new work.
- Third-party libraries, boilerplate, starter templates, and public APIs are allowed when properly credited and licensed.

### Mandatory AWS Rule
- The project must genuinely use AWS. AWS cannot be decorative.
- The demo video must visibly demonstrate AWS-powered behavior.
- **Do not count**: AWS mentioned only in README, architecture slide with AWS logos, hardcoded AWS-looking output, or mocked production responses as evidence of AWS implementation.

### Ship It Target & Live URL Contract
- This project targets **FIRST COMMIT — SHIP IT**.
- Ship It requires a live AWS deployment that judges can access through a URL.
- Architecture and cost decisions are part of the work.
- The final architecture includes **one** justified judge-facing surface:
  ```text
  Maintainer / Judge
         │
         ▼
  Amazon Amplify Hosting
         │
         ▼
  Minimal React Maintainer / Evidence Dashboard
         │
         ▼
  Read-Only Evidence / Status API
  ```
- **Amplify is justified specifically because Ship It needs a live URL.**
- Do NOT turn this into a large frontend project. The product remains GitHub-native. The dashboard only visualizes important system evidence (proposal status, SUPPORTED/CONTRADICTED claims, active lease owner + expiry, PR integrity status PASS/DRIFT, and real operational counters).

### Judging Criteria Alignment
Every major implementation choice must help at least one of:
1. **Idea & Impact**: Eliminates open-source PR flooding before duplicate PRs happen.
2. **Built on AWS**: Real serverless architecture (API Gateway, Lambda, SQS Standard + DLQ, Step Functions, Bedrock, DynamoDB, S3, CloudWatch, Amplify).
3. **Learning**: Genuine distributed systems learning documented in README.
4. **Execution**: One feature that genuinely works is more valuable than five incomplete features. Working core > optional feature count. Real behavior > architectural ambition. Clear demo > invisible complexity.
5. **Demo Video**: ≤3-minute video showing real, working product and visible AWS backing.

### Submission Contract
Final submission requires:
1. Public GitHub repository with clean Git history.
2. Demo video ≤ 3 minutes.
3. Short writeup (problem, what was built, where AWS fits).
4. Ship It live deployment URL.
Judges evaluate what is submitted; there is no live Q&A call. If a capability is not visible or accessible, it receives zero judging credit.

---

## 2. Scope Prioritization Matrix

### MUST BUILD (P0 — Must Work for Submission)
- Real GitHub App integration with HMAC-SHA256 signature verification.
- Fast Webhook Lambda ingesting to Amazon SQS Standard + DLQ (sub-second ACK SLO).
- Atomic event admission (`EVENT#<deliveryId>`) in DynamoDB to eliminate duplicate deliveries safely.
- Real Bedrock structured claim extraction via `bedrock-runtime` with JSON Schema (configurable model, e.g. Claude Sonnet 4.6).
- Real Deterministic Repository Verifier (Python AST / Git tree inspection) producing `SUPPORTED` / `CONTRADICTED` / `UNKNOWN`.
- Real Policy Engine producing `VERIFIED` / `NEEDS_REVISION` / `ESCALATED`.
- Real DynamoDB atomic conditional lease transaction (`TransactWriteItems`) with stale-worker fencing.
- Real GitHub API side effects protected by internal idempotency records.
- Real PR drift integrity loop comparing unified diff against qualified intent (`PASS` vs `DRIFT`).
- Real 100-worker concurrency race demonstration on a fresh issue.
- Minimal judge-facing live React dashboard on Amazon Amplify Hosting (Ship It live URL).
- Concise ≤3-minute demo script showing real product behavior and AWS backing.

*If time is short: CUT P1. Never weaken P0.*

### SHOULD BUILD (P1 — Only if All P0 Tasks Complete)
- GitHub Check Run status integration for PRs.
- Clean evidence breakdown markdown visualization in issue comments.
- Maintainer override webhook handler (`issues.assigned`/`issues.unassigned`).
- CloudWatch operational dashboard & EMF metrics alarms (`LeaseConflicts`, etc.).
- Proposal revision loop for `NEEDS_REVISION`.

### FORBIDDEN / SCOPE CUTS (P2 — Post-Hackathon / DO NOT BUILD)
- **NO** arbitrary contributor code execution / execution sandboxes (e.g. Fargate / Docker / Firecracker).
- **NO** custom ML model training or fine-tuning.
- **NO** contributor social graph / reputation scoring systems.
- **NO** Slack, Discord, or IDE extensions.
- **NO** decorative microservices (Aurora, Redis, OpenSearch, ECS, Kinesis, MSK).
- **NO** generic conversational chatbots or open-ended PR reviewers.
- **NO** complex authentication, social features, or animations-heavy landing page on the dashboard.
- **NO** fake or mocked behavior in the end-to-end product demo (test doubles are permitted for unit tests only).
