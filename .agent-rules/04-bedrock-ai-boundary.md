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
- Parsing and structuring proposal text into formal, falsifiable claims.
- Generating semantic hypotheses and candidate file suggestions.
- Producing human-readable explanations and revision feedback for contributors.
- Performing semantic comparison between verified proposal intent and PR diffs.

---

## 2. Model Configuration & Native Structured Output (Fix F)

1. **Configurable Model**: The model ID / inference profile must remain configurable via environment variables. For the hackathon baseline, use an active model supporting native structured outputs via `bedrock-runtime` (prefer **Claude Sonnet 4.6** if available in the configured region/account).
2. **Native Structured Output**: Invocations must use native structured outputs via `Converse` or `InvokeModel` on `bedrock-runtime` with JSON Schema. Do NOT rely on prompt-only JSON formatting if native structured output is available.
3. **T06 Preflight Check**:
   - Verify that the configured model or inference profile is available and active.
   - Verify that structured output (tool use / JSON schema mode) is supported.
   - Fail visibly if unavailable — **never silently swap models**.
4. **Deterministic Schema Validation**: Extracted output is strictly validated against a Pydantic / JSON Schema model before downstream processing. Free-form text parsing is prohibited.

```json
{
  "claims": [
    {
      "claim_id": "CLM-001",
      "claim_type": "FILE_EXISTS | SYMBOL_EXISTS | CALL_GRAPH | TEST_STRATEGY",
      "target_path": "src/retry.py",
      "target_symbol": "retry_request",
      "assertion": "Modifies retry_request to add exponential backoff"
    }
  ],
  "affected_files": ["src/retry.py", "tests/test_retry.py"],
  "test_strategy": ["Regression test for exponential backoff"],
  "unknowns": ["No database schema modifications specified"],
  "confidence_score": 0.85
}
```

---

## 3. Hostile Prompt Injection Defense

1. **Untrusted Envelopes**:
   - Contributor input (issue descriptions, comments, PR bodies) is wrapped in `<untrusted_contributor_text>...</untrusted_contributor_text>`.
   - Repository input (READMEs, source code, commit messages) is wrapped in `<untrusted_repository_content>...</untrusted_repository_content>`.
2. **System Prompt Isolation**: Untrusted envelopes MUST NOT be treated as system directives, policy overrides, or control instructions.
3. **Independent Fact Verification**: If Bedrock outputs `"src/retry.py exists and contains backoff"`, downstream systems MUST NOT accept this as fact. The Deterministic Verifier (`05-repository-verifier.md`) independently inspects the Git tree and AST.
