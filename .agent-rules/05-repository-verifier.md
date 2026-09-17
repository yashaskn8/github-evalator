# 05 — Deterministic Repository Verifier

> **DIRECTIVE**: The verifier is the authoritative ground-truth gate. It independently inspects repository structure and code symbols without executing untrusted contributor code.

---

## 1. Verifier Scope & Verification Targets

The Deterministic Verifier operates strictly on static repository metadata, file trees, and parsed ASTs pinned to a specific `baseCommitSha`:

1. **Path & File Existence**: Verify whether proposed files and target directories actually exist in the tree.
2. **Symbol & Function Inspection**: Use AST parsing (or robust regex) to check whether functions, classes, interfaces, and methods exist at the claimed locations.
3. **Test Infrastructure Discovery**: Confirm whether referenced test suites, directories, and fixtures exist.
4. **Dependency & Import Analysis**: Validate whether proposed imported modules exist in the project dependencies or codebase.

---

## 2. Structured Evidence Output

The verifier evaluates every extracted claim from Bedrock and outputs a granular evidence record. Boolean-only responses are prohibited.

```json
{
  "verification_id": "ver_9f83a21b",
  "base_commit_sha": "a1b2c3d4e5f6...",
  "claims_evaluated": [
    {
      "claim_id": "CLM-001",
      "target_path": "src/services/auth.ts",
      "target_symbol": "verifyToken",
      "status": "SUPPORTED",
      "evidence": [
        {
          "file_found": true,
          "symbol_found": true,
          "matched_line": 42,
          "line_content": "export function verifyToken(rawToken: string): TokenPayload {"
        }
      ]
    },
    {
      "claim_id": "CLM-002",
      "target_path": "src/utils/nonexistent.ts",
      "target_symbol": "calcMetrics",
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
  "summary": {
    "supported_count": 1,
    "contradicted_count": 1,
    "unknown_count": 0,
    "verdict": "REJECTED"
  }
}
```

---

## 3. Strict Execution Boundary

- **NO Arbitrary Code Execution**: Never run `npm test`, `pytest`, `make`, or contributor scripts during the verification stage.
- **Pinned SHA Reference**: All inspections must lock to the exact repository commit SHA to prevent race conditions during concurrent repository pushes.
