# 04 — Bedrock AI Trust Boundary

> **CORE PRINCIPLE**: AI reasons; deterministic code verifies. Model confidence is NOT evidence. AI output may influence explanation and analysis, but never authority.

---

## 1. Authoritative State vs. Non-Authoritative Output

### FORBIDDEN: Direct Authoritative Control
Bedrock MUST NEVER directly control or transition:
- Issue ownership or GitHub assignment status.
- DynamoDB lease creation, renewal, or revocation.
- Qualification records or authorization decisions.
- System security policies or repository isolation boundaries.
- GitHub permissions or maintainer overrides.

### PERMITTED: Non-Authoritative Reasoning & Explanations
Bedrock is approved to produce non-authoritative analytical data:
- Parsing and structuring proposal text into formal claims.
- Generating semantic hypotheses and candidate file suggestions.
- Producing human-readable explanations and revision feedback for contributors.
- Performing semantic comparison between verified proposal intent and PR diffs.

---

## 2. Strict Structured Output Schema

All Bedrock invocations MUST enforce a deterministic JSON output format validated via Pydantic / JSON Schema before downstream consumption. Free-form text parsing is strictly prohibited.

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
