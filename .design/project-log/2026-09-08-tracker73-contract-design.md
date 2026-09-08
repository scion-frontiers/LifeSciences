# Unified Contract Schema Design for #74/#75/#11

**Date:** 2026-09-08
**Agent:** dev-contract-design
**Branch:** scion/dde-maint-tracker73-em (targeting origin/DDE)
**Design ref:** tracker73-shared-contracts.md (in scratchpad)

## What was done

Designed the foundational contract schema that three tracker #73 implementation
issues (#74, #75, #11) will build on. The design document is at
`/scion-volumes/scratchpad/projects/dde-maint/design/tracker73-shared-contracts.md`.

### Codebase study

Read and analyzed all files listed in the task brief before designing:

- `tools/dde/core/thresholds.py` — UNRESOLVED sentinel, named/versioned threshold
  sets, three-level resolution, provenance strings
- `tools/dde/core/statemachine.py` — transition tables, Refusal on illegal
  transitions, terminal state detection
- `tools/dde/core/controlstore.py` — JSON records under `.dde/control/`,
  record types with subdirectories, schema validation on write, append-only
  event log, safe identifier regex
- `tools/dde/core/provenance.py` — sidecar pattern, relay codes, `written_by`
  identity, analysis record structure
- `tools/dde/core/errors.py` — error hierarchy and exit codes
- `tools/dde/commands/program.py` — cross-phase state continuity, markdown WO
  parsing, ID reconciliation
- `tools/dde/commands/hypothesis.py` — adoption pattern, vendor-neutral
  assessment, verbatim-plus-normalised artifacts
- `skills/program-state-management/SKILL.md` — Layer 2 documents, pre-routing
  integrity check, repair authority
- `skills/hypothesis-entry/SKILL.md` — four strategies, no-silent-substitution
  rule
- `skills/pocket-druggability/SKILL.md` — interpretation contract pattern,
  mandatory relays, verdict naming
- `artifact-templates/program-state/*` — active-series, decision-log,
  liability-tracker, open-questions templates
- `artifact-templates/gates/stage1-target-nomination/` — gate document template
- `artifact-templates/findings/finding-template.md` — finding structure
- `templates/science-program-lead/agents.md` — lead template with stage gates,
  unratified draft values, major pivot detection
- `templates/scientific-reviewer/agents.md` — reviewer template with phase
  sequence

Also read `tracker73-sequencing-plan.md` and `hypex-dde-integration.md` for
coordination context.

### Design decisions

1. **ID schemes follow existing patterns.** `IC-NNN` for concepts, `AR-NNN` for
   assessments, `DR-NNN` for decisions, `GP-NNN` for gate policies, `SNAP-NNN`
   for snapshots. All follow the `WO-NNN`/`RUN-NNN`/`DEC-NNN` convention in
   `controlstore.py`.

2. **Concept revision semantics mirror work orders.** Record keys are
   `IC-NNN-rN`, each revision is immutable, latest revision is current state.
   Concept lifecycle states follow the `statemachine.py` transition-table
   pattern.

3. **Evidence status and execution outcome are separated.** Five evidence
   statuses (supported, contradicted, insufficient, not_assessed,
   not_yet_applicable) are distinct from five execution outcomes (completed,
   tool_unavailable, tool_failed, data_unavailable, blocked). The constraint
   that `execution_outcome != "completed"` implies `evidence_status ==
   "not_assessed"` prevents tool failures from being silently recorded as
   evidence gaps.

4. **Policy requirements reference threshold sets by name.** Following the
   existing convention that tool thresholds are cited by name, never by value.
   Requirements carry `threshold_set` and `threshold_key` references rather
   than inlined cutoff values.

5. **Freeze snapshots capture resolved threshold values.** When a gate decision
   is made, the snapshot records `ThresholdSet.applied()` output — the actual
   values after three-level resolution. This makes gate decisions re-auditable
   against the exact policy in force.

6. **Backward compatibility via opt-in adoption.** No existing file is modified
   automatically. Programs can partially adopt concept records while keeping
   informal `active-series.md` entries. Absence of policy records means the
   gate operates in its existing judgment-based mode.

7. **New modules, not bloated existing ones.** Three new core modules
   (`concepts.py`, `evidence.py`, `policy.py`) rather than expanding
   `controlstore.py` past 1000 lines. Validators are registered in
   `controlstore.py` by import, keeping the single dispatch pattern.

8. **Human-gated termination.** Concept records carry
   `termination_authority` from the charter. The schema records the gap when
   a termination lacks human approval but does not block the lead — the
   integrity check surfaces it as a warning.

### Placement decisions

- **`tools/dde/core/`**: New modules for shared types (concepts, evidence,
  policy)
- **`controlstore.py`**: Extended `RECORD_TYPES` and `_VALIDATORS` (additive
  only)
- **`statemachine.py`**: Concept transitions registered in `_MACHINES`
  (additive only)
- **`.dde/control/`**: Five new subdirectories (concepts, assessments,
  decisions, policies, snapshots)
- **`.dde/program.yaml`**: Schema extended for gate policy references and
  concept defaults
- **`artifact-templates/`**: Existing templates extended with concept/decision
  reference fields
- **`skills/`**: `program-state-management` extended with concept lifecycle
  rules

### What was NOT done

- No implementation code was written. This is a design document only.
- No existing files were modified. The design is additive and will be
  implemented in Phase 2.
- No threshold values were invented. All numeric references point to existing
  named threshold sets.

## Verification

- Read all 16 listed codebase files before designing
- Every ID pattern follows existing `controlstore.py` conventions
- Every threshold reference uses name@version, never inline values
- Evidence reference schema preserves type, metric, units, method, context
- Concept state machine follows `statemachine.py` transition-table pattern
- Human-reserved decisions are preserved per charter constraints
- Backward compatibility path defined for existing programs
- Build/test commands: not applicable (design document only, no code changes)

## Risks and open questions

1. **Assessment record granularity** — proposed one-per-claim-evidence-pair;
   may need adjustment based on implementation experience
2. **Decision record namespace** — proposed `DR-NNN` separate from `DEC-NNN`;
   alternative is extending `DEC-NNN` but that mixes prose and structured
   records
3. **Policy definition authority** — proposed program lead with user
   confirmation for hard constraints; needs confirmation during implementation
