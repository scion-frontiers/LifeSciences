# Unified Contract Schema — B1 Revision (Review Fixes)

**Date:** 2026-09-08
**Agent:** dev-contract-design
**Branch:** scion/dev-contract-design
**Design ref:** tracker73-shared-contracts.md (in scratchpad)
**Review ref:** tracker73-contract-design-review.md (in scratchpad)

## What was done

Revised the unified contract schema design document to address all findings
from the B0 review (verdict: REQUEST CHANGES). All 4 Required fixes, all 5
Recommended items, and all applicable minor fixes were addressed in a single
revision pass.

### Required fixes (R1-R4)

1. **R1 — Human-approval enforcement → Refusal.** Changed the enforcement
   mechanism for human-gated termination decisions from a validation warning
   to a write-time `Refusal` (exit 9) in the decision-record validator.
   Updated §1.1 (concept lifecycle), §2.2 (decision record schema), and §3.2
   (pre-routing integrity check). The integrity check remains as a
   supplementary defense for records that bypass the write path, but the
   primary enforcement is now at write time — matching the existing
   `statemachine.py` pattern of "check before write, refuse if illegal."
   This was the primary finding: the warning approach contradicted #73's
   "preserve existing charter-defined human approvals," #75 AC7, and #76's
   "review cannot autonomously terminate."

2. **R2 — OOD/uncalibrated prediction taxonomy.** Added explicit mapping to
   §1.2: `execution_outcome == "completed"` + `evidence_status ==
   "insufficient"` + a mandatory relay code identifying the OOD/uncalibrated
   condition. This extends the existing relay mechanism in `provenance.py`
   (e.g., `alphagenome.band_modality_mismatch`) rather than creating new enum
   values. Explained why new enum values would be the wrong approach (every
   consumer must handle them; the tool-specific distinction belongs in the
   relay code).

3. **R3 — Freeze snapshot `unresolved` field.** Added `unresolved: list[str]`
   field to each threshold set entry in the freeze schema (§2.3.2). Populated
   from `ThresholdSet.unresolved()`. This makes the distinction between "was
   0.5" (in `applied`), "was UNRESOLVED" (in `unresolved`), and "didn't exist
   yet" (absent from both) explicit and auditable. Updated the worked example
   in §7 and the JSON Schema in Appendix A.5.

4. **R4 — `entity_ref` resolution table.** Added a resolution table to §2.2
   specifying the reference format for each `entity_type`: `concept` → `IC-NNN`
   or `IC-NNN-rN`, `claim` → `AR-NNN` (the assessment containing the claim),
   `program` → `DEC-NNN` (charter decision), `series` → hyphenated lowercase
   name from `active-series.md`. Each entry has a source convention reference.

### Recommended items (O1-O5)

5. **O1 — `entity_ref` in concept schema.** Added optional `entity_ref` field
   to `CONCEPT_OPTIONAL_FIELDS` for #74 AC1's entity/sequence/construct
   reference. Null until matter exists.

6. **O2 — Stage 0 budget/escalation-policy.** Explicitly deferred to issue
   #22's implementation. Added deferral notes to §2.3 (policy schema) and
   §4.4.1 (program.yaml).

7. **O3 — Biomarker assumption structure.** Specified `BIOMARKER_SCHEMA` with
   four categories (patient_selection, target_engagement, response,
   companion_diagnostic), three statuses (known, unknown, not_applicable),
   and `rationale` required when status != "known". Updated the JSON Schema
   in Appendix A.1.

8. **O4 — Unit/method compatibility matching.** Added matching rules to §3.1:
   `evidence_type` must match exactly, `units` must be compatible when both
   specified, `method` is informational (not gating), `endpoint` should match
   when specified. Mismatches produce warnings in the integrity check.

9. **O5 — Evidence type registry.** Added non-authoritative registry of 10
   current evidence type names to §1.5, with source tool/skill and example
   metrics. Specified naming convention (lowercase, underscored, domain-
   descriptive).

### Minor fixes (F1-F3)

10. **F1** — Corrected `controlstore.py` line count from 567 to 566.
11. **F2** — Corrected `DEC-NNN` attribution from `controlstore.py` to
    `decision-log.md` prose convention.
12. **F3** — Resolved lazy/eager directory creation inconsistency: directories
    are created eagerly by `ensure_control_dirs` (matching existing behavior).

### Structural additions

- Added Revision Log table at top of document tracking all changes by
  finding ID.
- Updated document status from "Proposed — awaiting review" to "B1
  Revision — addressing review findings."
- Added test cases to §8.3 for human-approval Refusal, freeze `unresolved`
  field, biomarker validation, and OOD relay codes.

## What was NOT changed

- The core architecture (shared types, module decomposition, freeze
  semantics, backward compatibility, cross-reference conventions) was
  confirmed sound by the reviewer and left unchanged.
- No rearchitecting was performed — this is a targeted revision pass.
- F4 (termination guidance template updates) was not addressed — the
  reviewer correctly noted this is template/skill content, not schema,
  and is deferred to implementation.

## Verification

- All 4 Required findings addressed with specific text changes
- All 5 Recommended items addressed (O1-O5)
- All applicable minor fixes applied (F1, F2, F3)
- Revision Log added per deliverable requirements
- JSON Schemas in Appendix A updated to match body changes
- Worked example (§7) updated with `unresolved` field
- Build/test commands: not applicable (design document only, no code changes)

## Gates run

| Gate | Status | Notes |
|---|---|---|
| Code compilation / syntax | N/A | Design document, not code |
| Unit tests | N/A | No code to test |
| Consistency check | Ran | Verified Refusal, unresolved, entity_ref references consistent across sections |
| Review finding coverage | Ran | All R1-R4 addressed, all O1-O5 addressed, F1/F2/F3 applied |
