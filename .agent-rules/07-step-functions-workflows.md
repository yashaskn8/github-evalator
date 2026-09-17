# 07 — Step Functions Workflows & Dispatch

> **DIRECTIVE**: Orchestrate through explicit Step Functions states. Never collapse multi-stage business pipelines into a monolithic "god-Lambda".

---

## 1. Proposal Evaluation State Machine

```text
       [ SQS Event Ingestion ]
                  │
                  ▼
          [ LOAD_CONTEXT ] (Fetch Issue & Repo metadata)
                  │
                  ▼
         [ PARSE_PROPOSAL ] (Extract text & contributor claims)
                  │
                  ▼
        [ RETRIEVE_EVIDENCE ] (Fetch AST & Git tree snapshot)
                  │
                  ▼
         [ EXTRACT_CLAIMS ] (Bedrock structured claim extraction)
                  │
                  ▼
         [ VERIFY_CLAIMS ] (Deterministic Verifier checks ground truth)
                  │
                  ▼
       [ POLICY_EVALUATION ] (Evaluate verification thresholds)
                  │
          ┌───────┴───────┐
          ▼               ▼
     [ REJECTED ]    [ ACCEPTED ]
          │               │
          │               ▼
          │       [ ACQUIRE_LEASE ] (DynamoDB atomic transaction)
          │               │
          └───────┬───────┘
                  ▼
     [ GITHUB_SIDE_EFFECT ] (Post comment / assign issue via idempotency key)
```

---

## 2. PR Integrity Verification State Machine

```text
       [ PR Opened / Synchronized ]
                  │
                  ▼
       [ LOAD_VERIFIED_INTENT ] (Fetch Qualification & VerificationRun)
                  │
                  ▼
           [ LOAD_PR_DIFF ] (Retrieve GitHub PR file diff)
                  │
                  ▼
      [ DETERMINISTIC_CHECKS ] (Verify touched files vs. claimed files)
                  │
                  ▼
     [ SEMANTIC_COMPARISON ] (Bedrock compares diff semantics to proposal)
                  │
                  ▼
         [ EVALUATE_POLICY ] ────► Outcome: PASS | DRIFT | REVIEW
                  │
                  ▼
      [ POST_CHECK_STATUS ] (GitHub Check Run & PR Comment)
```

---

## 3. Workflow Resilience & Fail-Closed Invariants

1. **Fail-Closed on Bedrock Outage**: If Bedrock throttles or errors, catch the error, record `ERROR_BEDROCK_UNAVAILABLE`, and transition to human escalation. **NEVER** fall back to granting a lease.
2. **Retry Policies**: Configure Step Functions `Retry` with exponential backoff (`IntervalSeconds: 2`, `BackoffRate: 2.0`, `MaxAttempts: 3`) for network/transient errors only.
3. **Dead-Letter Routing**: Permanent task failures must emit to a DLQ and alert via CloudWatch metrics.
