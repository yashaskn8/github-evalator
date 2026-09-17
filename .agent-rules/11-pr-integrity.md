# 11 — PR Intent & Drift Integrity

> **CORE QUESTION**: Did the contributor implement approximately the approach and file scope that earned them ownership of this issue?

---

## 1. Intent vs. Diff Comparison

The PR Integrity system is NOT a generic code linter or AI style checker. It evaluates whether the pull request honors the **verified intent** captured during qualification.

```text
Stored Qualification / VerificationRun (Intent)
                    VS
     Actual GitHub Pull Request (Diff)
```

---

## 2. Integrity Evaluation Layers

### Layer A: Deterministic File Scope Check
- **Expected Files**: Did the PR modify the files identified in the verified proposal (`affected_files`)?
- **Scope Expansion**: Did the PR modify critical, unmentioned files (e.g. `package.json`, CI workflows, security middleware, unrelated business domains)?
- **Test Inclusions**: Does the PR include the promised test files or unit tests?

### Layer B: Bedrock Semantic Diff Review
- Bedrock analyzes the unified diff against the original proposal summary.
- Focus: Are the changes conceptually aligned with the verified solution, or is the PR doing something completely different?

---

## 3. Decision Outcomes & Actions

| Verdict | Meaning | Action Taken |
| :--- | :--- | :--- |
| **`PASS`** | PR changes match verified scope and intent. | Post positive check run / green status; notify maintainers that qualification intent is honored. |
| **`DRIFT`** | PR touches completely unapproved files or diverges from the proposal. | Post warning check run / yellow status; leave detailed comment indicating scope drift. |
| **`REVIEW`** | PR contains ambiguous changes or significant unexpected complexity. | Post neutral status; flag for explicit human maintainer review. |

---

## 4. Bounded Claims

Never output claims such as `"AI verified the code is bug-free"` or `"Code is mathematically proven correct"`.
Always output grounded language: `"PR scope matches verified qualification intent (3 of 3 promised files modified, tests included)."`
