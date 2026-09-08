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
| `tools/dde/commands/structure_screening.py` | New — screening logic, CLI command, real pocket_runner |
| `tools/dde/cli.py` | Modified — registered `structure-screen` command group |
| `skills/structure-screening/SKILL.md` | New — orchestration skill with tool invocations |
| `tests/test_structure_screening.py` | New — 45 tests (including integration tests) |
| `.design/project-log/2026-09-08-impl-38-structure-screening.md` | New — this entry |

---

## Revision: Real integration layer (reviewer feedback)

**What was missing**: the initial implementation had no real CLI command,
no real `pocket_runner` bridging to the actual `dde pocket` tool, and
all test pocket runners were hand-crafted mocks. The screening logic
was correct in isolation but nothing proved it could actually screen a
real structure.

### What was added

#### 1. Real `pocket_runner` via `make_pocket_runner()`

`make_pocket_runner(project_dir, near)` returns a callable
`(StructureCandidate) -> PocketResult` that:

- Uses `click.testing.CliRunner` to invoke `dde pocket run <STRUCTURE>`
  (phase 1: detect pockets, write `.pockets.json`)
- Then invokes `dde pocket analyze <POCKETS_RECORD>` with optional
  `--near` (phase 2: apply thresholds, emit verdict and relays)
- Parses the resulting `.pocket.analysis.json` via `_parse_pocket_analysis()`
  into a `PocketResult` for the screening layer

This matches the pattern established in `eval/harness.py` — programmatic
CLI invocation via CliRunner.

The `pocket_runner` parameter on `screen_structures()` is preserved for
testability (dependency injection), but the CLI command wires the real
runner as the default.

#### 2. Click CLI command: `dde structure-screen run`

Registered in `cli.py` as `structure-screen` (matching the kebab-case
naming convention). The `run` subcommand takes structure file paths,
`--concept-ref`, `--modality`, optional `--near`, budget controls, and
standard output options.

The CLI is conditionally defined (guarded by `_HAS_CLICK`) so the core
library functions remain importable without click installed — existing
tests that import the screening logic directly continue to work.

#### 3. `_parse_pocket_analysis()` — real output parser

Parses `dde pocket analyze` output JSON into `PocketResult`. Handles:
- Global verdicts (best pocket score, rank)
- Site-specific verdicts (`--near` results, site pocket score)
- No-hit site queries (site_relevant=False)
- Relay code extraction from `mandatory_relays`
- Threshold set and applied thresholds

#### 4. Structure retrieval scoping decision

Structure retrieval (fetching from AlphaFold DB or PDB) is explicitly
**out of scope** for this module. The module operates on
`StructureCandidate` objects that are caller-supplied. Retrieval is a
separate concern:

- The caller (future #22 dispatcher or a human) retrieves structures
  using `dde alphafold fetch` or PDB retrieval, then passes them to
  the screening layer.
- The screening layer validates the source classification (retrieval
  vs. new prediction) and refuses out-of-budget sources.
- The skill doc documents this: "Retrieve candidate structures within
  budget" is a workflow step, not a function this module implements.

This separation is deliberate: retrieval may involve network calls,
authentication, and caching — concerns that belong in the retrieval
commands, not the screening logic.

#### 5. Integration tests added

Seven new tests (38 → 45):

- `_parse_pocket_analysis`: three tests parsing druggable, site-specific,
  and no-hit analysis files matching the real `dde pocket analyze` output
  format.
- End-to-end integration: `_parse_pocket_analysis` → `build_assessment_record`
  with realistic pocket analysis JSON, verifying the assessment is correct
  and schema-valid.
- `make_pocket_runner` import path validation (gracefully handles missing
  click).
- CLI command registration verification (click.Group with run subcommand).
- `screen_structures` with `_parse_pocket_analysis`-based pocket_runner
  (no hand-crafted PocketResult fixtures).

#### 6. Skill doc updated

Added §3 "Tool invocations" matching the `pocket-druggability/SKILL.md`
style, with the `dde structure-screen run` command, options, and output
description. Renumbered subsequent sections.

### Verification

- All 45 tests pass (7 new integration tests + 38 original).
- Existing test suites: no regressions (`test_concepts.py` 74/74,
  `test_eval_metrics.py` 26/26, `test_evidence.py` 49/49,
  `test_policy.py` passes).
- Syntax verification: all modified files parse without error.
- Environment note: `click` is not installed in this container, so
  the CliRunner-based live integration test (invoking fpocket against
  a real structure file) cannot run. The test validates the import
  path and reports the environment limitation. The `_parse_pocket_analysis`
  tests exercise the real parsing path against realistic pocket analysis
  JSON matching the actual `dde pocket analyze` output format.
