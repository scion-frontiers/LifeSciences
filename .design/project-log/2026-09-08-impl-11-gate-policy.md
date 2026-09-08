# Implementation: #11 — Applicability-Aware, Versioned Gate Policy

**Date:** 2026-09-08
**Agent:** dev-policy-11
**Branch:** scion/dev-policy-11
**Design ref:** tracker73-shared-contracts.md §2.3, §3.1, §4.1, §4.4, §4.5

## What was built

Implemented the gate policy requirement system per the reviewed design
document, coordinated with sibling issues #74 and #75.

### 1. `applications/DDE/tools/dde/core/policy.py` (new module)

- `REQUIREMENT_TYPES` set: `hard_constraint`, `prioritization_heuristic`,
  `scientific_cutoff`
- Policy record validation (`validate_policy`) per design §2.3 / Appendix A.4
- Individual requirement validation with all required fields, type-specific
  checks (scientific_cutoff requires threshold_set and threshold_key)
- Applicability matching (`requirement_applies`) per §2.3.1: null means
  "applies to all", checks modality, stage, and indication
- Assessment-to-requirement matching (`match_assessment_to_requirement`) per
  §3.1: evidence_type exact match, units/endpoint warnings, method_required
  exclusion
- Freeze logic (`freeze_policy`) per §2.3.2: deep-copies requirements,
  resolves threshold sets to applied values, populates `unresolved` from
  `ThresholdSet.unresolved()` — UNRESOLVED keys appear in `unresolved` list,
  not silently dropped from `applied`
- Snapshot validation (`validate_snapshot`) per Appendix A.5
- Program YAML loader (`load_program_config`) per §4.4.1: program metadata,
  gate_policies stage mapping, human_reserved list, concept_defaults

### 2. `controlstore.py` extension (additive only)

- Added `"policy": "policies"` and `"snapshot": "snapshots"` to `RECORD_TYPES`
- Registered `_validate_policy_record` and `_validate_snapshot_record` in
  `_VALIDATORS` — both delegate to `policy.py` validators via lazy import
- No existing entries modified — diff is purely additive for clean merge

### 3. Gate template extension

- Added `policy_version` (GP-NNN@V format) and `snapshot_ref` (SNAP-NNN)
  fields to `artifact-templates/gates/stage1-target-nomination/target-nomination-package.md`

### 4. Gate-evaluation skill

- Created `skills/stage1-gate-evaluation/SKILL.md` documenting the Stage 1
  gate evaluation procedure: identify concepts, load policy, filter applicable
  requirements, match assessments, evaluate by type, handle UNRESOLVED
  thresholds, freeze and record

### 5. Tests — 52 tests, all passing

`applications/DDE/tests/test_policy.py` covers:
- Applicability matching: modality filter, null-means-all, stage filter,
  indication filter
- Requirement type validation: all three types, scientific_cutoff requires
  threshold_set
- Freeze with UNRESOLVED handling: synthetic threshold set, real hypex set
  (3 UNRESOLVED values), fully-resolved pocket set
- Unit/method compatibility: mismatched units = warning not rejection,
  method_required exclusion, endpoint mismatch warning
- Threshold references never inlined (fields are strings)
- Round-trip validation using design §7 worked example fixtures
- Control store registration and directory creation
- Program YAML loading (missing file, valid file, invalid structure)

## Judgment calls

1. **Lazy imports for validators in controlstore.py.** Used local imports
   inside `_validate_policy_record` / `_validate_snapshot_record` to avoid
   adding a top-level import to `controlstore.py`. This keeps the diff
   minimal and avoids circular import risk, following the pattern where
   `controlstore.py` is the dispatch layer and domain modules own validation
   logic.

2. **Program YAML validation is lightweight.** Only validates top-level
   structure types (program is dict, gate_policies is dict, etc.) rather
   than deep-validating every field. This follows backward compatibility
   (design §3.4) — a program can declare incomplete fields.

3. **No budget/escalation fields.** Per design §2.3 and §4.4.1, these are
   explicitly deferred to #22.

## Verification gates run

- `pytest tests/test_policy.py` — 52/52 passed
- `pytest tests/` (full suite) — all previously-passing tests still pass;
  pre-existing failures in test_hypex, test_program_resume, test_site_*,
  test_pubchem etc. are environment/dependency issues unrelated to this change
- Python import check — all exports from policy.py, controlstore.py
  registration confirmed
