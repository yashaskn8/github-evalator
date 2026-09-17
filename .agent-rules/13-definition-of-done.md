# 13 — Definition of Done & Sign-Off Template

> **GATE**: A feature is complete ONLY when it survives its adversarial red-team test and fulfills all 10 completion criteria.

---

## 1. The 10 Completion Criteria

1. **Architecture Conformance**: Implementation strictly follows the AWS serverless pipeline (`02-aws-architecture.md`).
2. **Authority Preservation**: AI only claims; deterministic verifier inspects; DynamoDB governs state (`01-core-invariants.md`).
3. **No Read-Check-Write**: All state changes use DynamoDB conditional writes or transactions (`06-dynamodb-state-leases.md`).
4. **Idempotency Verified**: Webhook and external side effects survive 10x duplicate replays (`03-github-webhooks.md`).
5. **Prompt Injection Hardened**: Untrusted inputs are delimited; verifier confirms ground truth (`04-bedrock-ai-boundary.md`).
6. **Zero Code Execution**: Untrusted code is statically inspected via AST/files, never executed (`05-repository-verifier.md`).
7. **Adversarial Gate Passed**: Corresponding red-team attack test from `09-testing-red-team.md` passes without exceptions.
8. **Auditable Provenance**: Complete decision trail reconstructible in CloudWatch logs (`10-observability.md`).
9. **No Fake / Mocked Logic**: Real APIs and deterministic verification logic used (no hardcoded "demo" shortcuts).
10. **Zero Decorative Services**: No unapproved AWS services introduced (`12-hackathon-scope.md`).

---

## 2. Mandatory Agent Response Template

Future coding agents MUST conclude their response with this structured report:

```markdown
### IMPLEMENTED
- <Concise list of features or fixes implemented>

### RULE FILES CONSULTED
- 01-core-invariants.md
- <Specific rule file(s) loaded>
- 09-testing-red-team.md

### INVARIANTS CHECKED
- [x] Invariant 1: AI cannot grant ownership
- [x] Invariant 2: Exactly one active lease per issue
- [x] Invariant 4: Application-enforced lease expiry
- [x] Invariant 10: Idempotent side effects

### TESTS RUN
- `npm test <path>` or `pytest <path>` (unit/integration results)

### RED-TEAM ATTACKS RUN
- Attack: <Name of attack from 09-testing-red-team.md>
- Outcome: PASS (with details on verified defense)

### FAILURE CASES VERIFIED
- <Simulated network timeout / retry replay / malformed payload>

### KNOWN LIMITATIONS
- <Honest assessment of any edge cases or pending items>

### NEXT HIGHEST-RISK ITEM
- <The next critical component or vulnerability to address>
```
