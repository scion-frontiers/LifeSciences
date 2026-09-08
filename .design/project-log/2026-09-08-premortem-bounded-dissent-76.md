# Issue #76: Bounded Pre-Mortem Review and Evidence-Backed Dissent

**Date**: 2026-09-08  
**Author**: dev-premortem-76  
**Branch**: `scion/dev-premortem-76`  
**Status**: Complete

## Summary

Implemented bounded pre-mortem review with scoped failure hypotheses,
four-type objection resolution, dissent preservation in gate documents,
and causal-language guards. Builds on #75's evidence/decision contract
and extends #25 (foundational-claim work order) and #26
(critical-liability checkpoint) without reimplementing them.

## Decision: No new schema fields on #75 decision records

**Decision**: Reuse existing `conditions: list[str]` field for
objection-resolution encoding. No new required or optional fields
added to the decision-record schema.

**Rationale**: The #75 decision-record schema (already merged to DDE)
has a `conditions` field defined as `list[str]` that is well-suited
for carrying structured annotations. Objection resolutions are encoded
as condition strings with the prefix `objection_resolution:` followed
by the resolution type, objection ID, and optional detail:

```
objection_resolution:accepted:OBJ-001
objection_resolution:rebutted:OBJ-002
objection_resolution:accepted_risk:OBJ-003:GP-001
objection_resolution:unresolved:OBJ-004:owner=computational-biologist
```

This encoding:
- Is backward-compatible: existing decision records with no
  `conditions` or with non-resolution conditions are unaffected.
- Is parseable: `decode_resolution_condition()` extracts structured
  data from the condition string.
- Avoids a breaking schema change to #75's already-merged contract.
- Uses `rationale` for the resolution reasoning and
  `supporting_assessments` for evidence backing — all existing fields.

**Alternatives considered**: Adding a new optional `objection_resolutions`
field to the decision record. Rejected because: (a) #75 is already
merged — a new field, even optional, changes the schema surface for all
consumers; (b) the `conditions` field already exists for exactly this
kind of structured annotation; (c) the encoding is simple enough to be
readable in raw JSON.

## Files Changed

### New files
- `tools/dde/core/premortem.py` — Pre-mortem validation module:
  failure-hypothesis validation, pre-mortem record validation,
  objection-resolution validation, condition-string encoding/decoding,
  dissent-preservation checks, gate-document dissent-section generation.
- `artifact-templates/findings/reviews/pre-mortem-template.md` — Template
  for pre-mortem review artifacts with failure-hypothesis entries,
  review-budget declaration, and resolution sections.
- `tests/test_premortem.py` — 42 tests covering all five required
  validation scenarios plus unit tests for all validation functions.

### Extended files
- `artifact-templates/findings/finding-template.md` — Added:
  Recommendation section (supporting evidence, assumptions, confidence
  limits, strongest material counterargument, what would change the
  recommendation).
- `artifact-templates/findings/reviews/review-template.md` — Added:
  Pre-Mortem Objections table for linking reviews to pre-mortem
  failure hypotheses.
- `templates/scientific-reviewer/agents.md` — Added section 3b:
  pre-mortem failure hypothesis formulation, discriminating-check
  requirement, causal-language discipline.
- `templates/science-program-lead/agents.md` — Added: pre-mortem
  review and objection resolution subsection covering review budget
  declaration, objection selection, four resolution types, budget
  exhaustion handling, dissent preservation, no-autonomous-termination
  constraint.
- `artifact-templates/program-state/liability-tracker.md` — Extended
  entry template with `Pre-mortem ref` field and guidance for
  pre-mortem-originated liabilities.
- `artifact-templates/gates/stage1-target-nomination/target-nomination-package.md`
  — Added: Pre-Mortem Dissent Record section with Unresolved Objections
  and Accepted Risks subsections.
- `skills/program-state-management/SKILL.md` — Extended integrity
  check with pre-mortem dissent-preservation rules: unresolved-objection
  tracking, resolution-encoding consistency, speculative-hypothesis
  gating guard, causal-language guard.

## Hard Constraints Verified

1. **No autonomous termination**: Scenario 5 test confirms that a
   terminate decision motivated by a pre-mortem accepted objection
   still hits the #75 Refusal gate (exit 9) when
   `termination_authority == "human"` and `human_approval` is absent.

2. **Speculative objections don't block**: Scenario 2 test confirms
   that a failure hypothesis without a discriminating check is recorded
   but excluded from `find_unresolved_objections()` and does not gate
   progress.

3. **Budget-bounded, not exhaustive**: Scenario 3 test confirms that
   when the review budget is exhausted, remaining hypotheses are
   recorded as `unresolved` with assigned owners — not silently dropped.

4. **Dissent survives gate-document generation**: Scenario 4 test
   confirms that `generate_gate_dissent_section()` includes unresolved
   objections in the output, and that speculative concerns are excluded.

5. **Existing #75 fields reused**: No new fields added to the
   decision-record schema. Resolution types encoded in `conditions`.

## Test Results

- `test_premortem.py`: 42/42 passed
- `test_evidence.py`: 49/49 passed (no regressions)
- `test_concepts.py`: 74/74 passed (no regressions)
- `test_policy.py`: passed (exit 0, no regressions)
- `test_hypothesis.py`: not runnable (pre-existing — requires `click`
  dependency not available in environment)

## Verification Gates Run

- [x] All new tests pass (42/42)
- [x] All existing evidence tests pass (49/49, no regressions)
- [x] All existing concept tests pass (74/74, no regressions)
- [x] Policy tests pass (exit 0)
- [ ] Hypothesis tests — could not run (missing `click` dependency,
  pre-existing environment limitation, not a regression)
