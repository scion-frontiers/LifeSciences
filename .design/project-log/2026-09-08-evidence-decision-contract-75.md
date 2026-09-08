# Issue #75: Evidence Status, Execution Failure, and Scoped Decision Outcomes

**Agent**: dev-evidence-75
**Date**: 2026-09-08
**Branch**: `scion/dev-evidence-75`
**Design**: `tracker73-shared-contracts.md` (B1 revision)

## What was built

### 1. `tools/dde/core/evidence.py` (new module)

Defines the shared type vocabulary for evidence assessments and decision records:

- **Enum sets**: `EVIDENCE_STATUSES` (5 values), `EXECUTION_OUTCOMES` (5 values), `ACTIONS` (5 values) per design SS1.2, SS1.3.
- **`EvidenceReference` dataclass**: preserves evidence type, metric semantics/units, method, context, and artifact provenance. Includes `from_dict`/`to_dict` for serialization.
- **`validate_assessment()`**: validates assessment records against the schema from design Appendix A.2, including the mutual-constraint invariant (execution_outcome != "completed" => evidence_status == "not_assessed").
- **`validate_decision()`**: validates decision records against design Appendix A.3, including:
  - `entity_ref` format validation per the resolution table (concept: IC-NNN[-rN], claim: AR-NNN, program: DEC-NNN, series: hyphenated-lowercase)
  - `human_approval` sub-schema validation
  - **Human-approval Refusal (exit 9)**: the primary enforcement from review finding R1 -- a terminate decision on a concept with `termination_authority == "human"` and no `human_approval` raises `Refusal`, matching the `statemachine.py` check-before-write pattern.

### 2. `controlstore.py` (additive extension)

- Added `"assessment": "assessments"` and `"decision": "decisions"` to `RECORD_TYPES`.
- Registered `validate_assessment` and `validate_decision` in `_VALIDATORS`.
- Extended `next_id()` to support `"assessment"` (AR-NNN) and `"decision"` (DR-NNN) record types.
- No existing entries or logic modified.

### 3. `artifact-templates/program-state/decision-log.md` (template extension)

Added `DR-NNN` cross-reference field to the entry template, linking prose `DEC-NNN` entries to their structured decision records in the control store.

### 4. `templates/scientific-reviewer/agents.md` (reviewer template)

Added section 2b covering:
- Evidence status mapping (how review outcomes map to the structured vocabulary)
- Execution outcome vs. evidence status distinction (tool failure is not insufficient evidence)
- Stage 0/Stage 1 termination guidance per AC6: no automatic target rejection from absent genetic/ligand precedent, a single pocket score, transcript abundance alone, or a count of disputed citations.

### 5. `skills/program-state-management/SKILL.md` (skill extension)

Added assessment/decision integrity check subsection covering:
- Evidence/execution mutual constraint check
- Decision supporting-assessment dangling reference check
- Human-approval critical-finding escalation (supplementary to write-time enforcement)
- Assessment coverage reporting

### 6. `tests/test_evidence.py` (38 tests, all passing)

Covers every acceptance criterion:
- Human-approval Refusal tests (5 tests -- the most important set)
- Evidence/execution mutual constraint (3 tests)
- OOD/uncalibrated mapping (1 test)
- entity_ref format validation for all 4 entity types (8 tests)
- EvidenceReference round-trip and distinguishability (2 tests)
- Control store round-trip and Refusal propagation (3 tests)
- Assessment validation edge cases (6 tests)
- Decision validation edge cases (5 tests)
- Enum set completeness (1 test)
- Design SS7 Step 3 fixture (2 tests)
- Control directory creation (1 test)
- Uses the design SS7 Step 3 assessment record as a fixture.

## Judgment calls

1. **`termination_authority` on the decision record**: The validator accepts `termination_authority` directly on the decision record as well as via a `concept_loader` callback. The direct-field approach allows the validator to work without needing to read concept records, making it usable both in the control store (where concept records may not exist yet, given the parallel #74 implementation) and in standalone validation. The concept_loader path is available for full integration.

2. **Series name regex**: Used `^[a-z0-9]+(-[a-z0-9]+)*$` for series entity_ref validation. This matches the design's "hyphenated lowercase" specification and existing examples like "cdk4-series", "alk-scaffold-3". Underscores are intentionally excluded to enforce the hyphen convention from `active-series.md`.

3. **Schema string validation**: Made schema string check non-required (only validated when present). This preserves backward compatibility -- existing programs can write records without schema strings, and the field becomes required only when explicitly set to the wrong value.

## Verification

- **Full test suite**: 38/38 tests pass in `test_evidence.py`.
- **Existing tests**: `test_eval_metrics.py` (26/26) passes; all other existing tests fail on `click` import (pre-existing environment constraint, not a regression).
- **Import verification**: All new types and validators importable; `RECORD_TYPES` and `_VALIDATORS` correctly extended.
- **Syntax check**: All changed files parse without errors.

## Gates not run (environment limitation)

- CLI integration tests requiring `click` could not be run (click not installed in this container). This is the pre-existing state of the test environment.
- No regressions detected in the tests that could run.

---

## Fix: Human-approval Refusal wiring gap (post-review)

**Finding**: The engineering manager reviewed the initial implementation and identified that the human-approval Refusal never fired in the real `write_record()` path. The validator checked `termination_authority` on the decision record (where it doesn't belong per the schema — it's a concept field) and accepted an optional `concept_loader` kwarg that `write_record()` never passed. Tests passed because they faked `termination_authority` directly on the decision dict — an unrealistic input that no real caller would construct.

**Root cause**: The validator was correct in isolation; the wiring between `write_record()` and `validate_decision()` was the gap. `write_record()` called `validator(data)` with no concept loader, so the gate had nothing to check against.

### Fix applied

1. **`controlstore.py`**: Added `_default_concept_loader(project_root)` that reads concept records from `.dde/control/concepts/` (handles both `IC-NNN.json` and `IC-NNN-rN.json` revision files). `write_record()` now takes an optional `concept_loader` kwarg and, for decision records, automatically supplies the default loader when none is provided.

2. **`evidence.py`**: Rewrote the human-approval gate to:
   - Only check termination authority for `entity_type == "concept"` (non-concept entities like series, program, claim are not subject to the concept-level gate).
   - Use the concept_loader to look up `termination_authority` from the real concept record.
   - **Safe-failure default**: When `termination_authority` cannot be determined (concept record missing, lookup failed, field absent), treat it as `"human"` — require approval. Removed the fallback to `data.get("termination_authority")` which checked a field that doesn't belong on the decision record.

3. **Tests**: Replaced all tests that faked `termination_authority` on the decision record with tests that write real concept records to disk and go through the unmodified `write_record()` call path. Added tests for the safe-failure default (unknown concept still requires approval). Test count increased from 38 to 43.

### Safe-failure reasoning

When the concept record does not exist or `termination_authority` is absent, the validator treats the authority as `"human"` (require approval). This is the safe default because:

- **Design principle #4**: "must not enable autonomous program termination."
- **`program.yaml` default** (§4.4.1): `termination_authority: "human"`.
- **The manager's explicit guidance**: "if the system cannot determine termination_authority, err toward requiring human_approval, not toward skipping the check."

An unknown authority defaulting to permissive would reintroduce the exact gap that review finding R1 identified — it would silently allow autonomous termination when the concept record is missing or hasn't been created yet.

### Verification after fix

- 43/43 tests pass in `test_evidence.py` (up from 38, covering the real write path).
- `test_eval_metrics.py` (26/26) passes — no regressions.
- The critical test (`REAL write_record: terminate + human concept + no approval => Refusal(9)`) writes a real concept record to disk, then calls `write_record("decision", ...)` with no special arguments, and confirms Refusal fires.

---

## Fix: Program termination authorization bypass (security audit)

**Finding**: A security audit found that `entity_type == "program"` terminate decisions bypassed the human-approval gate entirely. The gate only checked `entity_type == "concept"`, so `write_record(root, "decision", "DR-002", {action: "terminate", affected_entity: {entity_type: "program", entity_ref: "DEC-001"}, ...})` wrote successfully with no approval. This directly contradicts design principle #4 ("must not enable autonomous program termination") and `program.yaml`'s `human_reserved` list which names `"program_termination"` explicitly.

### Fixes applied (3 items from the audit)

1. **Program termination gate** (Medium severity): Extended the human-approval gate in `evidence.py` to cover `entity_type == "program"`. Program termination is unconditionally human-reserved — no concept lookup needed, approval is always required. Added as an `elif` branch alongside the existing concept gate.

   **Judgment on series/claim**: Not gated. Series termination (deprioritizing a compound series) is within the program lead's normal authority and is not named in `human_reserved`. Claim termination (ceasing to pursue a specific claim) is an operational decision, not a program-level gate. The audit flagged only `"program"` as Medium severity; `"series"`/`"claim"` were not identified as gaps. This keeps the change minimal and aligned with what the design actually reserves for humans.

2. **`schema` field required** (Low severity): Added `"schema"` to both `_ASSESSMENT_REQUIRED_FIELDS` and `_DECISION_REQUIRED_FIELDS`. This matches design Appendix A.2 and A.3, which list `schema` as required on every record type, and is consistent with the concept validator's behavior in #74.

3. **Concept loader format check** (Low severity): Added `re.match(r"^IC-\d{3,}$", concept_id)` guard at the top of `_default_concept_loader`'s `_load()` function. Defense-in-depth: upstream `entity_ref` validation already blocks malformed input, but this prevents filesystem access from unexpected callers.

### Verification after fix

- 49/49 tests pass in `test_evidence.py` (up from 43).
- `test_eval_metrics.py` (26/26) passes — no regressions.
- Key new tests:
  - `REAL write_record: terminate program + no approval => Refusal(9)` — reproduces the exact audit bypass and confirms it now fails.
  - `REAL write_record: terminate program + approval => success` — confirms the happy path works.
  - `assessment/decision schema field required` — confirms omitting `schema` is now a validation error.
  - `concept_loader rejects malformed IDs` — confirms defense-in-depth guard works.
