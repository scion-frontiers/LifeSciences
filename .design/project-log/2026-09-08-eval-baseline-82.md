# Evaluation Baseline for Issue #82

**Date**: 2026-09-08  
**Author**: dev-baseline-eval agent  
**Issue**: #82 (Evaluate workflow cost, evidence quality, and mistaken early rejection)  
**Branch**: scion/dde-maint-tracker73-em

## What Was Built

### Evaluation Harness (`applications/DDE/eval/`)

A lightweight, reproducible evaluation harness that runs the current DDE
workflow against a defined fixture set and collects process-quality
measurements.  Built BEFORE any workflow changes land (tracker #73) to
establish a baseline for comparison.

**Files created**:

| File | Purpose |
|------|---------|
| `eval/__init__.py` | Package init with module docstring |
| `eval/__main__.py` | Package entry point |
| `eval/harness.py` | Core evaluation runner — replays fixtures through CLI and control plane |
| `eval/metrics.py` | Measurement collection with explicit denominators; JSON and markdown reporting |
| `eval/fixtures/__init__.py` | Fixtures package |
| `eval/fixtures/definitions.py` | 8 fixture definitions with provenance documentation |
| `eval/run_baseline.py` | CLI entry point: runs harness and writes reports |
| `eval/baseline/baseline-report.json` | Machine-readable baseline report |
| `eval/baseline/baseline-report.md` | Human-readable baseline summary |
| `eval/baseline/run-manifest.json` | Reproduction manifest |

### Fixture Set (8 fixtures, all synthetic)

All fixtures are clearly labeled synthetic.  No real program data is used.

| ID | Category | What It Tests |
|----|----------|---------------|
| EVAL-001 | no_genetic_support | Hypothesis adoption + WO with no genetics artifacts; validation catches missing deliverables |
| EVAL-002 | negative_pocket_conformation | Unfavorable pocket druggability (score 0.12); fpocket.single_conformation relay fires |
| EVAL-003 | positive_model_geometry | Favorable AlphaFold structure (pLDDT 91.7 in binding region) |
| EVAL-004 | modality_mismatch | Context says antibody, deliverables say small-molecule; no gate catches this |
| EVAL-005 | absent_entity_inputs | Empty entity identifiers (SMILES, InChIKey, CID); control plane accepts them |
| EVAL-006 | tool_failure | Run fails with transient_infrastructure; recovery path verified (new run created) |
| EVAL-007 | disputed_citation | Hypothesis references retracted DOI; relays carry forward through analysis |
| EVAL-008 | bounded_review_exhaustion | 3 validation-failure recovery cycles; state machine allows unlimited cycling |

Two fixtures (EVAL-001, EVAL-002) are included in the declined-candidate
sample per issue #82 requirements for selection-bias review.

### Baseline Findings

| Metric | Value | Denominator | Notes |
|--------|-------|-------------|-------|
| Fixtures completed | 8 | 8 | 100% completion |
| CLI invocations succeeded | 4 | 4 | hypothesis adopt, validate check, hypothesis analyze |
| State transitions | 48 | — | All legal per state machine |
| Relay codes fired | 4 | — | adopted_not_generated, single_conformation, unranked_set |
| Artifacts produced | 8 | — | Hypothesis sets, sidecars, analyses |
| Repeated operations | 0 | 4 | No duplicate work |
| Wall clock (total) | 0.04s | — | Well under 60s budget |

**Metrics recorded as N/A** (require full LLM agent workflow execution):
- Unsupported claims accepted
- Mistaken rejections
- Decision reversals
- Expensive work avoided/cancelled

### Key Observations

1. **No modality-consistency gate**: The current workflow accepts a work
   order with context.modality="antibody" and deliverables targeting
   small-molecule artifact classes without flagging the mismatch.

2. **No context-content validation**: The control plane accepts work
   orders with empty entity identifiers (no SMILES, no InChIKey, no
   target name).  Errors surface only when tools attempt to use the
   missing values.

3. **No circuit breaker for review cycles**: The state machine allows
   unlimited validation_failed → in_progress → submitted cycles.
   There is no built-in limit.

4. **No retraction-check gate**: Hypothesis references to retracted
   publications pass through unchanged.  Relay codes propagate but no
   gate blocks the disputed citation.

5. **Validation catches missing deliverables**: The `deliverables_exist`
   mechanical check correctly fails when no Layer 0 artifacts exist for
   the declared artifact class.

6. **Recovery path works**: After a run failure (transient_infrastructure),
   the state machine allows creating a new run (attempt 2) for the same
   work order.

### Hard Constraints Met

- [x] No fabricated gold labels — fixtures measure process quality, not decision correctness
- [x] Explicit denominators on every rate metric
- [x] Provenance for all fixtures (all synthetic, labeled)
- [x] Run manifest retained for reproducibility
- [x] No workflow changes — existing tools, templates, and skills untouched

### Verification

- **Gates run**: The evaluation harness itself (`PYTHONPATH=tools python3 -m eval.run_baseline`)
  runs to completion with 8/8 fixtures passing.
- **Gates NOT run**: Full build/test suite was not run because the
  evaluation does not modify any existing code.  The harness lives in a
  new `eval/` directory alongside existing `tests/`.
- **Syntax check**: All new Python files parse without error.

### Reproduction

```bash
cd applications/DDE
PYTHONPATH=tools python3 -m eval.run_baseline
```

Outputs land in `eval/baseline/`.
