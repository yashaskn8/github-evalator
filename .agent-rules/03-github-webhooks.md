# 03 — GitHub & Webhook Integration Rules

> **DIRECTIVE**: The webhook ingestion boundary must remain lean, fast (<500ms), and cryptographically verified. All external GitHub side effects must be strictly idempotent.

---

## 1. Webhook Ingestion Boundary

### Mandatory Responsibilities
1. **Signature Verification**: Validate `X-Hub-Signature-256` HMAC-SHA256 signature using the secret loaded from AWS Secrets Manager. Reject invalid requests immediately with `401 Unauthorized`.
2. **Delivery Tracking**: Extract `X-GitHub-Delivery` GUID and pass it as the correlation token.
3. **Payload Normalization**: Extract essential metadata (`event`, `action`, `repository.id`, `issue.number`, `sender.id`, `installation.id`).
4. **SQS Ingestion**: Enqueue the normalized event to SQS. Store full payload in S3 if size exceeds SQS limit (256 KB).
5. **Fast Acknowledgment**: Return HTTP 202 Accepted within 500ms.

### Forbidden in Webhook Lambda
- **NO** synchronous Bedrock invocations.
- **NO** repository cloning or recursive tree fetching.
- **NO** DynamoDB lease mutations or GitHub assignment API calls.

---

## 2. GitHub External Side Effects & Idempotency Keys

Every mutating call to GitHub (comments, assignments, check runs) must be protected by a deterministic idempotency key stored in DynamoDB before execution:

| Operation | Idempotency Key Format | Pre-Flight Check |
| :--- | :--- | :--- |
| **Issue Assignment** | `assignment/<repoId>/<issueNumber>/<leaseId>` | Check if issue is already assigned to target contributor. |
| **Evaluation Comment** | `comment/<verificationId>/<decisionType>` | Check if comment with marker already exists in issue timeline. |
| **PR Check Run** | `pr_check/<repoId>/<prNumber>/<headSha>` | Check existing Check Run status on GitHub. |

---

## 3. Timeout-After-Success Handling

GitHub API calls may time out or fail after GitHub has successfully processed the request:
1. **Never blind retry** mutating API calls without checking external state.
2. If an assignment call fails with a network/timeout error, inspect the GitHub issue state first. If the contributor is assigned, mark the side effect as completed in DynamoDB.
3. Log all external API retries to CloudWatch with the correlation `githubDeliveryId`.
