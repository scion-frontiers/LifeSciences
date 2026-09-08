# Implementation: #38 — Bounded Structure Screening

**Date**: 2026-09-08  
**Agent**: dev-structure-screening-38  
**Branch**: `scion/dev-structure-screening-38`  
**Issue**: #38 (Inexpensive Structure Screening Without Target-Level Overclaims)

## Summary

Implemented the bounded structure screening component for Stage 0
pre-commitment fast-fail filtering. The implementation wraps the
existing `pocket-druggability` contract (SKILL.md and pocket.py)
without modifying either, and produces `dde.evidence-assessment.v1`
records (#75 schema) with relay codes and scoping preserved.

## What was built

### 1. Library module: `tools/dde/commands/structure_screening.py`

Core screening logic with functions for:
- **Modality applicability checking**: pocket geometry is relevant for
  small molecules and molecular glues; other modalities get a
  `not_yet_applicable` assessment rather than a forced pocket score.
- **Structure source classification**: distinguishes retrieval
  (AlphaFold DB, PDB) from new predictions (AF3); new predictions
  are out of scope for the bounded screen.
- **Site relevance assessment**: a high-scoring cavity elsewhere in
  the structure is not counted as a pass.
- **Assessment record construction**: produces `dde.evidence-assessment.v1`
  records with relay codes carried through from the pocket analysis.
- **Budget checking**: screens respect a declared budget (max structures,
  max wall-clock time).
- **Screening orchestration**: `screen_structures()` function that runs
  the complete screening workflow over a list of candidates.

Key data structures: `ScreenBudget`, `StructureCandidate`, `PocketResult`.

### 2. Skill: `skills/structure-screening/SKILL.md`

Orchestration skill that documents the workflow, preconditions,
relationship to pocket-druggability, mandatory relay preservation,
evidence status mapping, and hard constraints. This is a new skill
(not an extension of pocket-druggability) because it has distinct
Stage 0 budget/retrieval concerns.

### 3. Tests: `tests/test_structure_screening.py`

38 tests covering all four required validation scenarios:

1. **Low score on one conformation**: correctly scoped to `insufficient`
   (not `contradicted`), `fpocket.single_conformation` relay preserved
   with CDK2 calibration numbers, next-conformation suggestion included.

2. **Favorable model score on non-experimental structure**:
   `fpocket.conformation_dependent` relay preserved, confidence set to
   `low`, rationale notes both-directions constraint (a high score on
   a predicted structure is not evidence for druggability).

3. **Irrelevant high-scoring site**: correctly rejected as `insufficient`
   (not `supported`), rationale explains why a cavity elsewhere doesn't
   count.

4. **Inapplicable modality (antibody/biologic)**: produces
   `not_yet_applicable` without forcing a pocket score; pocket analysis
   is not even invoked.

Additional tests cover:
- Budget respect (structure count and wall-clock limits)
- Retrieval vs. new-prediction distinction
- All three relay codes preserved through the screening layer
- Evidence status mapping for all verdict types
- Assessment record schema validation (passes `validate_assessment()`)
- No cross-target ranking fields in assessment records
- Empty candidates, tool failures, mixed candidate types

## Hard constraints preserved

- `pocket-druggability/SKILL.md` and `pocket.py` are unmodified.
- No new AF3 prediction campaigns — retrieval only.
- No cross-target ranking by raw pocket score.
- All three relay codes (`fpocket.single_conformation`,
  `fpocket.conformation_dependent`, `fpocket.druggability_is_not_affinity`)
  are carried through without loss.
- A sub-cutoff score produces `insufficient` (not `contradicted`),
  matching the CDK2 calibration evidence.

## Verification

- All 38 new tests pass.
- Existing test suite: no regressions in tests that can run in this
  environment (`test_concepts.py` 74/74, `test_eval_metrics.py` 26/26,
  `test_evidence.py` 49/49, `test_policy.py` passes). Tests requiring
  `click` are pre-existing environment limitations.
- Syntax verification: all three new files parse without error.

## Files changed

| File | Change |
|---|---|
| `tools/dde/commands/structure_screening.py` | New — screening logic |
| `skills/structure-screening/SKILL.md` | New — orchestration skill |
| `tests/test_structure_screening.py` | New — 38 tests |
| `.design/project-log/2026-09-08-impl-38-structure-screening.md` | New — this entry |
