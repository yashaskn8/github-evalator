# 10 — Observability & Telemetry

> **DIRECTIVE**: Log real operational events. Zero synthetic or hardcoded metrics for demo purposes.

---

## 1. Structured Logging & Correlation IDs

All Lambda functions, Step Functions tasks, and verifier components must emit JSON structured logs containing the full correlation context:

```json
{
  "timestamp": "2026-09-17T15:04:05Z",
  "level": "INFO",
  "service": "verification-worker",
  "correlation": {
    "githubDeliveryId": "8f3b1a20-4e5c-11ef-93e1-0242ac120002",
    "workflowExecutionArn": "arn:aws:states:us-east-1:123456789012:execution:...",
    "repositoryId": "octocat/hello-world",
    "issueNumber": 42,
    "verificationId": "ver_9f83a21b",
    "qualificationId": "qual_c7a109e2",
    "leaseId": "lease_5b3e1a8",
    "prNumber": null,
    "headSha": "a1b2c3d4e5f6..."
  },
  "message": "Deterministic verification completed",
  "details": {
    "supportedClaims": 3,
    "contradictedClaims": 0,
    "verdict": "ACCEPTED"
  }
}
```

---

## 2. CloudWatch EMF (Embedded Metric Format)

Emit real runtime operational metrics via CloudWatch EMF:

- **Ingestion & Latency**: `WebhookLatency`, `VerificationLatency`, `BedrockLatency`, `QueueDepth`.
- **System Reliability**: `GitHubApiFailureRate`, `WorkflowRetryCount`, `LeaseConflicts`, `DuplicateAssignmentAttempts`.
- **Domain Outcomes**: `ProposalsVerified`, `ProposalsRejected`, `HumanEscalations`, `ExpiredLeases`, `ProposalPRDrift`, `UnsupportedClaimRate`.

---

## 3. Decision Auditability Gate

Every system decision (lease grant, proposal rejection, PR drift flag) MUST be fully reconstructible by querying CloudWatch logs using `verificationId` or `githubDeliveryId`. If a decision cannot be audited, the implementation is defective.
