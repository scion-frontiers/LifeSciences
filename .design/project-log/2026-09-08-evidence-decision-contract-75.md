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
