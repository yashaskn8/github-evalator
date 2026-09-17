# 05 — Deterministic Repository Verifier & Qualification Policy

> **DIRECTIVE**: The verifier is the authoritative ground-truth gate. It independently inspects repository structure and code symbols without executing untrusted contributor code.
> **BOUNDARY**: The Verifier produces only evidence (`SUPPORTED`, `CONTRADICTED`, `UNKNOWN`). The Policy Engine produces decisions (`VERIFIED`, `NEEDS_REVISION`, `ESCALATED`).

---

## 1. Verifier Scope & MVP Language Support (Fix E)

For the 4-day hackathon window, the MVP analyzer is strictly locked to:
1. **Git Tree & Path Existence**: Language-independent inspection of repository trees at pinned commit SHA.
2. **Python Symbol Inspection**: Python built-in AST (`ast` module) and static parsing to locate functions, classes, and methods.
3. **Python Test Discovery**: Demo repository conventions (`tests/test_*.py`, test directories).
4. **Extensible Analyzer Interface**: Implement an internal analyzer interface (`RepoAnalyzer`) so additional languages (TypeScript, Go, etc.) can be added post-hackathon without altering pipeline architecture.
5. **No Code Execution**: Static analysis only. Contributor code is NEVER executed (`npm test`, `pytest`, etc.).

---

## 2. Structured Evidence Output (Fix N)

The verifier evaluates Bedrock-extracted claims against repository AST and Git tree, outputting granular evidence records. Boolean-only responses and fake line numbers are prohibited.

```json
{
  "verification_id": "ver_9f83a21b",
  "base_commit_sha": "a1b2c3d4e5f6...",
  "claims_evaluated": [
    {
      "claim_id": "CLM-001",
      "target_path": "src/retry.py",
      "target_symbol": "retry_request",
      "status": "SUPPORTED",
      "evidence": [
        {
          "file_found": true,
          "symbol_found": true,
          "matched_symbol": "retry_request",
          "provenance": "retry_request confirmed in src/retry.py commit=a1b2c3d"
        }
      ]
    },
    {
      "claim_id": "CLM-002",
      "target_path": "src/nonexistent/admin_override.py",
      "target_symbol": "forceApproveEverything",
      "status": "CONTRADICTED",
      "evidence": [
        {
          "file_found": false,
          "symbol_found": false,
          "error": "Path not found in Git tree at commit a1b2c3d"
        }
      ]
    }
  ],
  "evidence_summary": {
    "supported_count": 1,
    "contradicted_count": 1,
    "unknown_count": 0
  }
}
```

---

## 3. Qualification Policy Engine (Fix D)

The Policy Engine is a separate deterministic module/state following the Verifier:

### Decision Outputs
- `VERIFIED`
- `NEEDS_REVISION`
- `ESCALATED`

### Conservative Policy Rules
1. **`VERIFIED` ONLY when ALL of the following hold**:
   - Proposal contains a concrete implementation target.
   - Required named file/path claims are `SUPPORTED`.
   - Required named symbol claims are `SUPPORTED` when supplied.
   - NO required implementation claim is `CONTRADICTED`.
   - Enough repository evidence exists to bind implementation intent.
2. **`NEEDS_REVISION` when**:
   - Proposal is vague or lacks repository-grounded detail.
   - Any required implementation claim is `CONTRADICTED`.
   - Proposed files exist but symbols/context do not establish a coherent implementation path (e.g. Bob's proposal).
3. **`ESCALATED` when**:
   - Repository evidence is unavailable (API limits, missing permissions).
   - Interpretation is materially ambiguous.
   - Model or infrastructure failure prevents a safe decision.
   - Maintainer policy demands manual review.

**AI confidence can NEVER produce `VERIFIED` by itself.** Only deterministic evidence satisfying the policy yields `VERIFIED`.
