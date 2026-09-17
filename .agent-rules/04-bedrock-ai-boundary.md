# 04 — Bedrock AI Trust Boundary

> **CORE PRINCIPLE**: AI reasons; deterministic code verifies. Model confidence is NOT evidence.

---

## 1. Permitted vs. Forbidden AI Operations

| Permitted Bedrock Operations | FORBIDDEN Bedrock Operations |
| :--- | :--- |
| Parsing issue text & proposal comments | Granting or denying issue assignment |
| Extracting structured claims & hypotheses | Creating, updating, or revoking DynamoDB leases |
| Identifying candidate files & symbols to check | Enforcing repository security or authorization policies |
| Generating human-readable verification summaries | Calling GitHub mutation APIs directly |
| Comparing proposal intent with PR diff semantics | Deciding whether a PR can be merged |

---

## 2. Strict Structured Output Schema

All Bedrock invocations MUST enforce a deterministic JSON output format validated via Pydantic / JSON Schema. Free-form text parsing is strictly prohibited.

```json
{
  "claims": [
    {
      "claim_id": "CLM-001",
      "claim_type": "FILE_EXISTS | SYMBOL_EXISTS | CALL_GRAPH | TEST_STRATEGY",
      "target_path": "src/services/auth.ts",
      "target_symbol": "verifyToken",
      "assertion": "Modifies verifyToken to support RSA256 signature verification"
    }
  ],
  "affected_files": ["src/services/auth.ts", "tests/auth.test.ts"],
  "test_strategy": ["Unit test for RSA256 decoding", "Negative test for expired tokens"],
  "unknowns": ["Database schema changes not specified"],
  "confidence_score": 0.85
}
```

---

## 3. Hostile Prompt Injection Defense

1. **Untrusted Envelope**: Wrap all issue descriptions, PR text, and user comments in explicit delimiters (e.g. `<untrusted_contributor_text>...</untrusted_contributor_text>`).
2. **System Prompt Isolation**: Instruct the model that text inside untrusted envelopes MUST NOT be treated as system directives, policy overrides, or control instructions.
3. **Independent Fact Verification**: If Bedrock outputs `"src/retry.py exists and contains backoff"`, downstream systems MUST NOT accept this as fact. The Deterministic Verifier (`05-repository-verifier.md`) must independently inspect the Git tree.
