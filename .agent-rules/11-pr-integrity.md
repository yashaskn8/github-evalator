# 11 — PR Intent & Drift Integrity

> **CORE QUESTION**: Did the contributor implement approximately the approach and file scope that earned them ownership of this issue?
> **KILLER FEATURE**: The PR Integrity system tracks the pull request across commits to prove implementation consistency over time.

---

## 1. Intent vs. Diff Comparison

The PR Integrity system is NOT a generic code linter or AI style checker. It evaluates whether the pull request honors the **verified intent** captured during qualification.

```text
Stored Qualification / VerificationRun (Intent)
                    VS
     Actual GitHub Pull Request (Diff)
```

---

## 2. Multi-Commit Same-PR Drift Verification (Fix M)

To prove that implementation consistency is maintained throughout the PR lifecycle, the integrity workflow executes on every commit push (`pull_request.synchronize`):

```text
Charlie's PR #88
   │
   ├── Commit 1 (Head SHA A)
   │     Modifies: src/retry.py, tests/test_retry.py
   │     Integrity Check: PASS (Matches verified intent)
   │
   └── Commit 2 (Head SHA B)
         Additionally Modifies: billing/stripe.ts (unrelated payment code)
         Integrity Check: DRIFT (Scope expanded into unapproved domains)
         Action: Maintainer review flag raised on Live Dashboard
```

This directly proves: the implementation remained consistent with the proposal that earned ownership, and unauthorized scope creep across commits is instantly caught.

---

## 3. Integrity Evaluation Layers

### Layer A: Deterministic File Scope Check
- **Expected Files**: Did the PR modify the files identified in the verified proposal (`affected_files`)?
- **Scope Expansion**: Did the PR modify critical, unmentioned files (e.g. `billing/stripe.ts`, `package.json`, CI workflows, security middleware)?
- **Test Inclusions**: Does the PR include the promised test files or unit tests?

### Layer B: Bedrock Semantic Diff Review
- Bedrock analyzes the unified diff against the original proposal summary.
- Focus: Are the changes conceptually aligned with the verified solution, or is the PR doing something completely different?

---

## 4. Decision Outcomes & Actions

| Verdict | Meaning | Action Taken |
| :--- | :--- | :--- |
| **`PASS`** | PR changes match verified scope and intent. | Post positive check run / green status; update live dashboard to `PASS`. |
| **`DRIFT`** | PR touches unapproved files or diverges from the proposal. | Post warning check run / status comment indicating drifted files; flag for maintainer review on live dashboard. |
| **`REVIEW`** | PR contains ambiguous changes or unexpected complexity. | Post neutral status; flag for explicit human maintainer review. |

---

## 5. Bounded Claims

Never output claims such as `"AI verified the code is bug-free"` or `"Code is mathematically proven correct"`.
Always output grounded language: `"PR scope matches verified qualification intent (2 of 2 promised files modified, tests included)."`
