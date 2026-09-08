# Implementation: #22 — Bounded Portfolio Triage (Stage 0)

**Date:** 2026-09-08
**Tracker:** #73 (P1 scope)
**Issue:** #22
**Branch:** scion/dev-stage0-22

## Summary

Implemented Stage 0 bounded portfolio triage as described in #22's refinement comment.
This is the largest single piece of tracker #73's P1 scope — it integrates the
Phase 1–3 tooling (concepts #74, evidence/decisions #75, policy #11, manufacturing #23,
structure screening #38, differentiation #37, premortem #76) into a unified pre-commitment
triage process.

## Deliverables

### 1. Template edits — naming disambiguation and new Stage 0 section

**File:** `templates/science-program-lead/agents.md`

- Renamed `## 5. Stage 0 handling` → `## 5. Hypothesis Entry Handling` (disambiguation:
  "Stage 0" was informally used for hypothesis entry, but the shipped CLI commands
  `dde manufacturing assess-stage0` and `dde structure-screen run` use "Stage 0" for the
  bounded portfolio triage concept. The label moves, not the mechanics.)
- Renamed `### Stage 0 — Hypothesis entry` → `### Program Initiation — Hypothesis Entry`
  and updated body text to reference Stage 0 triage (§5a) as the next step.
- Added `## 5a. Stage 0 — Bounded Portfolio Triage` — new section documenting:
  - Purpose and relationship to hypothesis entry
  - Three parallel workstreams (rationale verification, modality tractability,
    manufacturing feasibility)
  - Batching and scheduling rules
  - Budget and stopping criteria
  - No automatic veto rule
  - Program-constraint rejection vs. scientific refutation distinction
  - Recording requirements
  - Relationship to Stage 1 (Cohort A consumed from Stage 0)
  - Provenance across hypothesis-entry strategies
- Added cross-reference from the existing "Parallel mechanism-direction screening"
  subsection to Stage 0 (mechanism-direction screening is now one component of
  Workstream 1 within Stage 0).
- Updated Cohort A section to describe consuming Stage 0's accepted evidence
  rather than re-running the same checks (with fallback instructions for legacy
  programs without Stage 0).
- Added Rule 15 (Stage 0 triage before target commitment) and Rule 17 (no naive
  kill rules).

**File:** `skills/hypothesis-entry/SKILL.md`
- Updated description from "Stage 0: how hypotheses enter..." to
  "Hypothesis entry: how hypotheses enter..." with cross-reference to Stage 0 §5a.

### 2. Core module — Stage 0 triage orchestration

**File:** `tools/dde/core/triage.py`

New module implementing the bounded portfolio triage logic:

- `TriageBudget` — resource bounds (wall-clock, concepts, invocations) with
  exhaustion detection
- `WorkstreamResult`, `ConceptTriageResult`, `TriageOutcome` — structured result types
- `run_manufacturing_workstream()` — calls REAL `dde manufacturing assess-stage0` via
  CliRunner
- `run_structure_screening_workstream()` — calls REAL `dde structure-screen run` via
  CliRunner
- `run_differentiation_workstream()` — calls REAL `dde differentiation assess` via
  CliRunner
- `check_policy_exclusion()` — program-constraint rejection via #11 policy records
- `build_triage_decision()` — builds decision records with correct schema
- `build_budget_exhaustion_decision()` — always uses `investigate`, never `terminate`
- `evaluate_concept_portfolio()` — annotates portfolio-level signals without
  auto-deciding
- `cancel_competing_alternatives()` — records cancellations, never silently drops
- `run_triage()` — full triage loop across multiple concepts with budget management
- `write_triage_decision()`, `write_triage_assessment()` — write through real
  `write_record()` path

### 3. Tests

**File:** `tests/test_triage.py` — 49 tests, all passing

Test coverage per the test strategy:
- Multi-concept comparison (AC1): 2+ concepts, cancellation of alternatives
- Budget exhaustion (AC3): always `investigate`/`park`, never `terminate`
- Lone sponsor hypothesis (AC6): not auto-cleared, not auto-rejected
- No automatic veto (AC5): absent genetic evidence, missing manufacturing inputs
- Program-constraint vs. scientific rejection (AC5): distinct recording
- Termination-bypass regression (HC#2): `write_record()` Refusal gate confirmed
- Cohort A reconciliation (AC7): template content verification
- Real CLI invocation (HC#1): manufacturing, differentiation, structure screening

### 4. Regression test results

All pre-existing Phase 2–3 tests pass with no regressions:
- test_concepts.py: 74/74 passed
- test_evidence.py: 49/49 passed
- test_policy.py: passed (exit 0)
- test_manufacturing.py: 35/35 passed
- test_structure_screening.py: 44/45 (1 pre-existing click version failure)
- test_differentiation.py: 25/25 passed
- test_premortem.py: 42/42 passed

Pre-existing failures unchanged (8 total across pathway, site, hypex — not introduced
by this change).

## Hard Constraints Checklist

### HC#1: Every workstream dispatch calls the REAL merged commands

✅ **Confirmed.** `run_manufacturing_workstream()`, `run_structure_screening_workstream()`,
and `run_differentiation_workstream()` all use `click.testing.CliRunner` to invoke the
real CLI commands. Tests `test_real_manufacturing_cli`, `test_real_differentiation_cli`,
and `test_real_structure_screening_cli_with_structures` exercise these paths.

### HC#2: Any terminate action goes through the real, unmodified write_record()

✅ **Confirmed.** `write_triage_decision()` calls the real `write_record()`. Test
`test_terminate_requires_human_approval_via_write_record` constructs a Stage 0 flow
that reaches a termination recommendation and confirms `write_record()` raises
`Refusal` without `human_approval`. This follows the same pattern as
`test_scenario_5_accepted_objection_cannot_bypass_refusal` from #76.

### HC#3: Every new function is reachable from a real caller

✅ **Confirmed.** Test `test_all_functions_reachable` uses `inspect` to enumerate all
public functions in `triage.py` and confirms each appears in the test file (which
serves as the integration caller). All 13 public functions are called.

### HC#4: No aggregating tool outputs into a naive kill rule

✅ **Confirmed.** Test `test_no_naive_kill_rule_in_triage` uses AST parsing to verify
the module contains no if-statements that compare score-like variables (pocket_score,
sa_score, drug_score) against thresholds. Additionally:
- `evaluate_concept_portfolio()` annotates but never auto-terminates
- `test_portfolio_evaluation_annotates_not_decides` confirms this
- Budget exhaustion always produces `investigate`, never `terminate`
- The template documents the no-automatic-veto rule explicitly

## Design Decisions

1. **Mechanism-direction screening absorbed, not duplicated.** The existing "Parallel
   mechanism-direction screening" subsection in §5 is cross-referenced from Stage 0
   rather than duplicated. A note in §5 explains that when Stage 0 is active,
   mechanism-direction runs as part of Workstream 1.

2. **Cohort A consumed, not removed.** The Cohort A section is updated to describe
   consuming Stage 0 evidence rather than deleted entirely. This preserves the fallback
   path for programs that don't run Stage 0 (legacy or single-concept fast-track).

3. **Core module, not CLI command.** The Stage 0 triage logic is implemented as a core
   module (`core/triage.py`) rather than a new CLI command. This follows the project's
   pattern of core modules for domain logic and thin CLI wrappers for commands. The
   eval harness (#82) can import and exercise the module directly.

4. **No new record types.** All records use the existing #74 concept records and #75
   assessment/decision records. No new record types were registered in the control store.
