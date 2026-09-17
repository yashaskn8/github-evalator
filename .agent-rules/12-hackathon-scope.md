# 12 — Hackathon Scope & Anti-Overengineering

> **GOLDEN RULE**: Any scope expansion must demonstrably improve the core 3-minute live demonstration more than hardening the verification pipeline.

---

## 1. Scope Prioritization Matrix

### MUST BUILD (P0 — Core MVP)
- Real GitHub App integration with HMAC-SHA256 signature verification.
- Fast Webhook Lambda ingesting to SQS + DLQ.
- Step Functions Standard state machine orchestration.
- Real Bedrock structured claim extraction with schema validation.
- Real Deterministic Repository Verifier (AST / file / symbol inspection).
- Real DynamoDB conditional / transactional lease acquisition with stale-worker fencing.
- Real GitHub API side effects (issue assignment, structured evaluation comment).
- Real PR drift check comparing unified diff to verified qualification.
- Real 100-worker concurrency race-condition demonstration test.
- Real CloudWatch EMF metrics and structured correlation logging.

### SHOULD BUILD (P1 — Enhancements if P0 is Complete)
- GitHub Check Run status integration for PRs.
- Clean evidence breakdown markdown visualization in issue comments.
- Maintainer CLI / lightweight UI for lease revocations and manual overrides.
- Proposal revision loop (allowing contributors to submit updated proposals).
- CloudWatch operational dashboard.

### FORBIDDEN / SCOPE CUTS (P2 — DO NOT BUILD)
- **NO** arbitrary contributor code execution / execution sandboxes (e.g. Fargate / Docker / Firecracker).
- **NO** custom ML model training or fine-tuning.
- **NO** contributor social graph / reputation scoring systems.
- **NO** Slack, Discord, or IDE extensions.
- **NO** decorative microservices (Aurora, Redis, OpenSearch, ECS).
- **NO** generic conversational chatbots or open-ended PR reviewers.
