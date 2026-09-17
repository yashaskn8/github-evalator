# 13 — Definition of Done & Sign-Off Template

> **GATE**: A feature is complete ONLY when it survives its adversarial red-team test and fulfills all 10 completion criteria.

---

## 1. The 10 Completion Criteria

1. **Architecture Conformance**: Implementation strictly follows the approved AWS serverless pipeline (`02-aws-architecture.md`).
2. **Authority Preservation**: AI only claims; deterministic verifier inspects; DynamoDB governs state (`01-core-invariants.md`).
3. **Conditional State Authority**: All authoritative, concurrency-sensitive state transitions (leases, ownership, qualifications) use DynamoDB conditional writes or transactions (`06-dynamodb-state-leases.md`).
4. **Idempotency Verified**: Webhook and external side effects survive duplicate replays via internal idempotency records (`03-github-webhooks.md`).
5. **Prompt Injection Hardened**: Untrusted inputs are delimited; verifier independently confirms ground truth (`04-bedrock-ai-boundary.md`).
6. **Zero Code Execution**: Untrusted code is statically inspected via AST/files, never executed (`05-repository-verifier.md`).
7. **Adversarial Gate Passed**: Corresponding red-team attack test from `09-testing-red-team.md` passes without exceptions.
8. **Auditable Provenance**: Decisions fully reconstructible from persisted verification records + correlated CloudWatch logs (`10-observability.md`).
9. **No Fake Product Behavior**: Real APIs, real DynamoDB concurrency, and real verification logic used in the product pipeline. (Test doubles/mocks are permitted in unit tests, but fake demo shortcuts are forbidden).
10. **Zero Decorative Services**: MVP default-deny policy observed; no unapproved AWS services introduced (`12-hackathon-scope.md`).

---

## 2. Mandatory Agent Sign-Off Response Template

Future coding agents MUST conclude formal sign-off reports using this template:

```markdown
### IMPLEMENTED
- <Concise list of features or fixes implemented>

### RULE FILES CONSULTED
- 01-core-invariants.md
- <Specific component rule file(s) loaded>
- 09-testing-red-team.md
- 13-definition-of-done.md

### INVARIANTS CHECKED
<!-- Only list invariants materially relevant to this change. Do NOT list unrelated invariants. -->
- [x] I1 — AI cannot grant ownership
- [x] I2 — Exactly one active lease per issue
- [x] I5 — At-least-once delivery resilience
- [x] I10 — Idempotent side effects

### TESTS RUN
- `npm test <path>` or `pytest <path>` (unit/integration test results)

### RED-TEAM ATTACKS RUN
- Attack: <Name of attack from 09-testing-red-team.md>
- Outcome: PASS (with evidence of verified defense)

### FAILURE CASES VERIFIED
- <Simulated network timeout / retry replay / malformed payload>

### KNOWN LIMITATIONS
- <Honest assessment of any edge cases or pending items>

### NEXT HIGHEST-RISK ITEM
- <The next critical component or vulnerability to address>
```
