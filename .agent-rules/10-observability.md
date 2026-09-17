# 10 — Observability & Telemetry

> **DIRECTIVE**: Log real operational events. Zero synthetic or hardcoded metrics for demo purposes.

---

## 1. Structured Logging & Webhook Privacy (Fix B)

All Lambda functions, Step Functions tasks, and verifier components must emit JSON structured logs containing safe correlation context.

### Allowed Structured Metadata
`githubDeliveryId`, `eventType`, `action`, `installationId`, `repositoryId`, `issueNumber`, `senderId`, `bodyHash`, `correlationId`, `latency`, `result`, `errorClass`.

### Strictly Forbidden in Logs
- Full private issue bodies or contributor comments.
- Full private repository source files or AST code dumps.
- GitHub App private keys (PEM).
- Webhook HMAC secrets or secret values.
- AWS credentials or IAM session tokens.
- Raw diff payloads beyond file path and symbol summaries.

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
    "supportedClaims": 2,
    "contradictedClaims": 0,
    "verdict": "VERIFIED"
  }
}
```

---

## 2. CloudWatch EMF (Embedded Metric Format)

Emit real runtime operational metrics via CloudWatch EMF:

- **Ingestion & Latency**: `WebhookLatency`, `VerificationLatency`, `BedrockLatency`, `QueueDepth`.
- **System Reliability**: `GitHubApiFailureRate`, `WorkflowRetryCount`, `LeaseConflicts`, `DuplicateAssignmentAttempts`.
- **Domain Outcomes**: `ProposalsVerified`, `ProposalsNeedsRevision`, `HumanEscalations`, `ExpiredLeases`, `ProposalPRDrift`.

### Concurrency Race Proof (Fix O)
Observability instruments all production components (T01–T11). The `LeaseConflicts` EMF metric verifies that the 100-worker concurrency race produced exactly 99 conflicts.

---

## 3. Decision Auditability Gate

1. **Reconstructibility**: Every system decision (lease grant, proposal revision, PR drift flag) MUST be fully reconstructible by combining persisted evidence records (in S3/DynamoDB) with correlated CloudWatch operational logs using `verificationId` or `githubDeliveryId`.
2. **Fail-Closed on Telemetry Failures**: Observability failures must not alter authoritative DynamoDB state transitions.
