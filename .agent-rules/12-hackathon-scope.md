# 12 — Hackathon Scope & Implementation Priorities

> **Scope principle**: Prioritize a complete working vertical slice over incomplete feature breadth. Any scope expansion must demonstrably improve the core verification and assignment pipeline.

---

## 1. First Commit Event Constraints

These constraints apply to the **First Commit — Ship It** track:

### Event Validity
- Project implementation occurs during the First Commit hackathon window.
- Planning, architecture design, and pre-code documentation are established prior to coding.
- Public Git history is preserved without squashing or synthetic timestamp alterations.
- Third-party libraries, starter templates, and public APIs are allowed when properly credited and licensed.

### Mandatory AWS Usage
- The project genuinely uses AWS serverless primitives; AWS services are functional, not decorative.
- The demonstration video visibly demonstrates AWS-powered behavior.

### Ship It Target & Live URL Contract
- This project targets **FIRST COMMIT — SHIP IT**.
- Ship It requires a live AWS deployment accessible through a public URL.
- Architecture and cost decisions are part of the evaluation.
- The final architecture includes a public read-only dashboard:
  ```text
  Maintainer / Viewer
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
- Amplify hosts the read-only dashboard, visualizing proposal status, supported/contradicted claims, active lease owner and expiry, PR integrity status (PASS/DRIFT), and operational counters.

### Judging Criteria Alignment
Implementation decisions align with the event evaluation criteria:
1. **Idea & Impact**: Coordinates open-source issue assignment before duplicate pull requests happen.
2. **Built on AWS**: Real serverless architecture (API Gateway, Lambda, SQS Standard + DLQ, Step Functions, Bedrock, DynamoDB, S3, CloudWatch, Amplify).
3. **Learning**: Genuine distributed systems findings documented in README as implementation progresses.
4. **Execution**: Working core features prioritized over incomplete feature breadth.
5. **Demo Video**: ≤3-minute video showing the working product and underlying AWS coordination.

### Submission Contract
Final submission requires:
1. Public GitHub repository with clean Git history.
2. Demo video ≤ 3 minutes.
3. Short writeup (problem, build, AWS usage).
4. Deployed live URL.
The submitted video should demonstrate every capability that is material to the project's evaluation.

---

## 2. Scope Prioritization Matrix

### Core Scope (P0)
- GitHub App integration with HMAC-SHA256 signature verification.
- Webhook Lambda ingesting to Amazon SQS Standard + DLQ (sub-second ACK SLO).
- Atomic event admission (`EVENT#<deliveryId>`) in DynamoDB to eliminate duplicate deliveries safely.
- Bedrock structured claim extraction via `bedrock-runtime` with JSON Schema (configurable model, e.g. Claude Sonnet 4.6).
- Deterministic Repository Verifier (Python AST / Git tree inspection) producing `SUPPORTED` / `CONTRADICTED` / `UNKNOWN`.
- Policy Engine producing `VERIFIED` / `NEEDS_REVISION` / `ESCALATED`.
- DynamoDB atomic conditional lease transaction (`TransactWriteItems`) with stale-worker fencing.
- GitHub API side effects protected by internal idempotency records.
- PR drift integrity loop comparing unified diff against qualified intent (`PASS` vs `DRIFT`).
- 100-worker concurrency race verification on a fresh issue.
- Read-only React evidence dashboard on Amazon Amplify Hosting (fulfills live deployment requirement).
- Concise ≤3-minute demonstration script showing product behavior and AWS backing.

*Priority note: If time is limited, secondary scope items are deferred to preserve the integrity of the P0 core.*

### Secondary Scope (P1 — Enhancements if P0 is Complete)
- GitHub Check Run status integration for PRs.
- Structured evidence breakdown markdown visualization in issue comments.
- Maintainer override webhook handler (`issues.assigned` / `issues.unassigned`).
- CloudWatch operational dashboard & EMF metrics alarms (`LeaseConflicts`, etc.).
- Proposal revision loop for `NEEDS_REVISION`.

### Deferred Beyond MVP (P2)
- Arbitrary contributor code execution or execution sandboxes (Fargate, Docker, Firecracker).
- Custom ML model training or fine-tuning.
- Contributor social graph or reputation scoring systems.
- Slack, Discord, or IDE extensions.
- Auxiliary microservices (Aurora, Redis, OpenSearch, ECS, Kinesis, MSK).
- Conversational chatbots or unconstrained PR reviewers.
- Complex authentication, user management, or animation-heavy landing pages on the dashboard.
- Mocked or synthetic product behavior in the production demo pipeline.
