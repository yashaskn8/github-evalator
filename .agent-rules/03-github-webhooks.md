# 03 — GitHub & Webhook Integration Rules

> **DIRECTIVE**: The webhook ingestion boundary must remain lean, fast, and cryptographically verified. All external GitHub side effects must be strictly idempotent via internal records.

---

## 1. Webhook Ingestion Boundary

### Engineering SLO & Protocol Requirements
- **Engineering SLO**: Sub-second webhook acknowledgement during normal operation.
- **Correctness Requirement**: The webhook handler acknowledges quickly and remains well within GitHub's delivery timeout while performing only authentication, minimal normalization, and SQS enqueue work.

### Mandatory Responsibilities
1. **Signature Verification**: Validate `X-Hub-Signature-256` HMAC-SHA256 signature using the secret loaded from AWS Secrets Manager ARN. Reject invalid signatures immediately with `401 Unauthorized`.
2. **Delivery Tracking**: Extract `X-GitHub-Delivery` GUID and pass it as the event correlation token.
3. **Payload Normalization**: Extract essential metadata (`event`, `action`, `repository.id`, `issue.number`, `sender.id`, `installation.id`).
4. **SQS Ingestion**: Enqueue the normalized event to Amazon SQS Standard. Store payload in S3 only if size exceeds the 256 KB SQS message limit (encrypted, repo-scoped, retention-limited).
5. **Fast Acknowledgment**: Return HTTP 202 Accepted.

### Duplicate Delivery Handling
- Webhook duplicate deliveries are a normal property of distributed webhooks.
- The pipeline flow: `Authentic request → ACK (202) → Enqueue → Dispatcher admits via DynamoDB conditional EVENT#<deliveryId> → Safely drop duplicate logical work`.
- **Do NOT** return authentication errors (`401`) for legitimate duplicate deliveries.
- SQS Standard provides burst buffering and retry isolation. Authoritative deduplication is enforced by DynamoDB conditional writes during Dispatcher event admission (T05 / E07).

### Webhook Privacy & Safe Structured Telemetry
- **NEVER log raw or full webhook payloads to CloudWatch.**
- **Allowed structured metadata**:
  `githubDeliveryId`, `eventType`, `action`, `installationId`, `repositoryId`, `issueNumber`, `senderId`, `bodyHash`, `correlationId`, `latency`, `result`, `errorClass`.
- **FORBIDDEN from logs**:
  Full private issue body, full private source code, GitHub private keys, webhook secrets, AWS credentials, raw repository dumps.

### Forbidden in Webhook Lambda
- **NO** synchronous Bedrock invocations.
- **NO** repository cloning or recursive tree fetching.
- **NO** DynamoDB lease mutations or GitHub assignment API calls.

---

## 2. Internal Deterministic Idempotency Records

GitHub API does not natively support a custom generic idempotency header. Every external side effect is governed by an **internal deterministic idempotency record** in DynamoDB.

```text
Determine Side-Effect Identity
              │
              ▼
Consult DynamoDB Idempotency Record
              │
      ┌───────┴───────┐
      ▼               ▼
[ Already Done ]  [ Pending / New ]
      │               │
      │               ▼
Return Cached   Reconcile / Execute GitHub Call
   Result             │
                      ▼
               Persist Completed Record
```

| Operation | Internal Idempotency Key Format | Pre-Flight External Reconciliation |
| :--- | :--- | :--- |
| **Issue Assignment** | `assignment/<repoId>/<issueNumber>/<leaseId>` | Check if issue is already assigned to target contributor on GitHub. |
| **Evaluation Comment** | `comment/<verificationId>/<decisionType>` | Check if comment with matching verification marker exists in issue timeline. |
| **PR Check Run** | `check/<repoId>/<prNumber>/<headSha>/<checkType>` | Check existing Check Run status on GitHub. |

---

## 3. Timeout-After-Success Handling

If a GitHub API call succeeds on GitHub but the network response is lost before reaching Lambda:
1. **Never blind retry** mutating API calls without checking external state.
2. The retry worker checks the GitHub issue/PR state first. If the mutation is already present, it marks the internal idempotency record as completed and proceeds without duplicating side effects.
3. Log all external API retries and reconciliations to CloudWatch with `githubDeliveryId`.
