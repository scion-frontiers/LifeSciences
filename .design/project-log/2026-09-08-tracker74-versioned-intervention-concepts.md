# Implementation: #74 — Versioned Intervention Concepts

**Date**: 2026-09-08
**Agent**: dev-concepts-74
**Branch**: `scion/dev-concepts-74`
**Design**: `tracker73-shared-contracts.md` (B1 revision)

## What was built

### 1. `tools/dde/core/concepts.py` (new module)

Core concept domain logic:

- **Concept ID scheme**: `IC-NNN` regex, `IC-NNN-rN` record key regex,
  matching the existing `WO-NNN` / `RUN-NNN` patterns.
- **State machine**: `CONCEPT_TRANSITIONS` dict following the
  `statemachine.py` transition-table pattern, with 7 states
  (draft, active, under_review, pivoting, parked, terminated, withdrawn).
  Terminal states (terminated, withdrawn) have no outgoing transitions.
- **Record validation**: `validate_concept()` checking all required
  fields, schema string, ID format, revision positivity, state legality,
  disease_context/target_pathway structure, modality type, biomarker
  assumptions, and termination_authority enum.
- **Biomarker validation**: `validate_biomarker()` enforcing the 4
  categories (patient_selection, target_engagement, response,
  companion_diagnostic) and the rationale-when-not-known rule.
- **Revision-trigger logic**: `requires_new_revision()` comparing key
  fields between old and new records. delivery_assumptions triggers only
  when the new value is non-null (per design §2.1).
- **Charter-linkage gate**: `check_charter_linkage()` blocking
  draft -> active without charter_ref.

### 2. Additive registration in `controlstore.py`

- Added `"concept": "concepts"` to `RECORD_TYPES`
- Registered `validate_concept` in `_VALIDATORS`
- Added `_IC_PREFIX_RE` and concept support to `next_id()`
- Imported `CONCEPT_STATES` and `validate_concept` from concepts module

### 3. Additive registration in `statemachine.py`

- Imported `CONCEPT_TRANSITIONS` from concepts module
- Added `"concept": CONCEPT_TRANSITIONS` to `_MACHINES`
- `validate_transition("concept", ...)` now works

### 4. Template extension: `active-series.md`

Added `**Concept ID**` field to both Stage 1 (target evaluation) and
Stage 3 (compound optimization) entry templates, with guidance for
entries predating concept record adoption.

### 5. Migration command: `dde program migrate-concepts`

Added to `commands/program.py`:
- Parses `active-series.md` for entries without concept IDs
- Proposes draft concept records with gaps declared as null
- Supports `--dry-run` for preview
- Supports `--active-series` for custom path
- Writes records and logs `concept.migrated` event

### 6. Skill extension: `program-state-management/SKILL.md`

Added "Concept lifecycle rules" subsection covering:
- State machine usage
- Charter-linkage gate
- Pivot governance
- Revision triggers
- Biomarker assumptions
- Concept integrity checks

### 7. Lead template: `agents.md`

- Added charter-linkage requirement section (§3, before charter
  revision on major pivot)
- Extended major pivot detection section with `pivoting` state
  governance instructions

## Test coverage

65 tests in `tests/test_concepts.py`, all passing:
- Concept ID regex (2 tests)
- State machine transitions (7 tests)
- State machine registration (3 tests)
- Concept validation (13 tests)
- Biomarker validation (11 tests)
- Revision triggers (10 tests)
- Charter-linkage gate (5 tests)
- Control store registration (2 tests)
- Control store integration/round-trip (4 tests)
- next_id generation (2 tests)
- Backward compatibility (3 tests)
- Multi-modality/scoped rejection (2 tests)
- Major revision (1 test)

Fixtures cover: two modalities for the same target, an adopted
singleton, a hypothesis without starting matter (null entity_ref),
a major concept revision, scoped rejection leaving alternative
concept available, and the §7 Step 1 worked example round-trip.

## Verification gates

- **Concept tests**: 65/65 pass
- **Existing tests**: `test_eval_metrics.py` (26 tests) passes —
  no regressions. Other test files require `click` module which is
  not installed in this environment (pre-existing condition, not
  caused by this change). The concept tests themselves were written
  to test core logic directly without requiring click.
- **Import verification**: All three registrations (RECORD_TYPES,
  _VALIDATORS, _MACHINES) confirmed via import check.

## Judgment calls

1. **`delivery_assumptions` null-to-null**: The design says
   delivery_assumptions triggers "when non-null." I interpreted this
   as: the *new* value must be non-null for the trigger. Setting
   delivery_assumptions from non-null back to null does not trigger
   a revision (it's clearing, not changing, the assumption).

2. **Migration command state**: Migrated records are created in
   `draft` state, not `active`, since they lack charter_ref and
   other required context. The operator fills gaps and transitions
   to active manually.

3. **Biomarker rationale whitespace**: Empty or whitespace-only
   rationale strings are rejected, not just null. The design says
   "rationale required" — a whitespace-only string is not a rationale.

4. **`gene` as target_pathway migration default**: When migrating
   from active-series.md, the series heading text is used as the
   `gene` field in `target_pathway`. This is a best-effort mapping;
   the operator can edit the resulting record.

## Files changed

- `tools/dde/core/concepts.py` (new)
- `tools/dde/core/controlstore.py` (additive)
- `tools/dde/core/statemachine.py` (additive)
- `tools/dde/commands/program.py` (additive)
- `artifact-templates/program-state/active-series.md` (extended)
- `skills/program-state-management/SKILL.md` (extended)
- `templates/science-program-lead/agents.md` (extended)
- `tests/test_concepts.py` (new)

---

## Post-review fix (2026-09-08): charter-linkage gate wired into real write path

**Finding**: The EM review identified that `check_charter_linkage()` was
defined but never called from `validate_concept()` — the function
registered in `controlstore._VALIDATORS["concept"]`. A caller could
write a concept with `state: "active"` and `charter_ref: null` through
`write_record()` with no enforcement. Same class of gap as the sibling
#75 human-approval finding.

**Fix (3 changes)**:

1. **Wired `check_charter_linkage` into `validate_concept()`** with
   `Refusal` semantics (exit 9). When `data["state"] == "active"` and
   `charter_ref` is missing/empty, `validate_concept()` now raises
   `Refusal` directly — not a soft `SchemaError`. This matches the
   design's intent: a missing charter reference is a governance
   prerequisite (same class as the human-approval gate on terminate
   decisions), not a data-quality problem. The `Refusal` propagates
   naturally through `write_record()` since `controlstore.py` does
   not catch it.

2. **Documented `requires_new_revision()` deferral.** This function
   is intentionally not wired into any write path because no CLI
   command yet updates an existing concept's key fields in place.
   `migrate-concepts` creates new r1 records from scratch; there is
   no `dde program update-concept` command yet. The deferral is noted
   in the function's docstring with the specific wiring point for when
   such a command is added. This is an intentional scope boundary, not
   an oversight.

3. **Added 7 through-`write_record()` tests** (72 total, up from 65):
   - `validate_active_no_charter_refusal`: `validate_concept()` raises `Refusal` for active + no charter
   - `validate_active_with_charter_ok`: active + charter passes
   - `validate_draft_no_charter_ok`: draft + no charter passes (gate only on active)
   - `write_record_active_no_charter_refusal`: **the critical integration test** — real `write_record()` path raises `Refusal` (exit 9), not `SchemaError`
   - `write_record_active_with_charter_ok`: real path succeeds with charter set
   - `write_record_active_empty_charter_refusal`: empty string charter raises `Refusal` through real path
   - `write_record_draft_no_charter_ok`: draft without charter writes successfully
