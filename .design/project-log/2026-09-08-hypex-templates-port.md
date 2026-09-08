# Project Log: Port hypex templates to DDE

**Agent:** dev-hypex-templates
**Date:** 2026-09-08
**Task:** Port 4 hypex agent templates to DDE's `templates/` directory

## What was done

Ported four hypex agent templates from the hypex reference into DDE's
`templates/` directory, rewriting all `lit` calls to `dde` CLI equivalents,
adopting DDE conventions, and implementing the pacing pre-flight obligation
in the supervisor template.

### Templates created

1. **`templates/hypex-supervisor/`** — Orchestrates a B1 single-epoch
   hypothesis tournament. Implements all six boundary contract obligations
   from the design doc (§4.2.2):
   - Obligation 1: One accountable agent
   - Obligation 2: Datastore as audit trail
   - Obligation 3: Roster (`meta/roster.ndjson`)
   - Obligation 4: Progress (`meta/progress.json`)
   - Obligation 5: Declared termination (`meta/termination.json`)
   - Obligation 6: Pacing pre-flight (`meta/pacing.json`) — verifies shared
     pace tier before starting any network-touching sub-agent; refusal gate

2. **`templates/hypex-generation/`** — Literature exploration and hypothesis
   generation. All `lit` calls rewritten to DDE equivalents.

3. **`templates/hypex-reflection/`** — Hypothesis review and scoring on six
   axes plus biosafety screening. Citation verification uses DDE tools.

4. **`templates/hypex-tournament/`** — Pairwise tournament match execution
   (ported from `hypex-ranking`, renamed per DDE convention).

### DDE conventions adopted in all templates

- `source /scion-volumes/tools/env.sh` at session start (not raw PATH export)
- `dde doctor --json` as first health check
- Artifact paths under `raw/`
- `{source: <path> $.<jsonpath>}` citation syntax documented
- `scion message --broadcast` stays forbidden

### `lit` to `dde` rewrites (in hypex-generation and hypex-reflection)

| Original (`lit`) | DDE equivalent |
|---|---|
| `lit multi "<query>"` | Individual `dde pubmed search`, `dde preprint search` calls |
| `lit pubmed search` | `dde pubmed search` |
| `lit arxiv search` | `dde preprint search --source arxiv` |
| `lit biorxiv search` | `dde preprint search --source biorxiv` |
| `lit verify` | `dde cite verify` |
| `lit resolve` | `dde litref resolve` |
| `lit pubmed related <pmid>` | No DDE equivalent (capability gap noted) |
| `lit pubmed cites <pmid>` | No DDE equivalent (capability gap noted) |

### Capability gaps noted

- **No `lit multi` equivalent** — agents must run individual search commands
  instead of a single fan-out. Noted in hypex-generation template.
- **No `lit pubmed related` / `lit pubmed cites` equivalent** — citation
  chain following is not available in DDE. Agents use keyword-based searches
  as a workaround. Noted in hypex-generation template.

### B1 single-epoch constraint

- `MAX_EPOCHS = 1` hardcoded in supervisor
- Evolution (EVOLVE), evolved review (REVIEW_EVOLVED), rematch
  (TOURNAMENT_REMATCH), and steering memo (META) phases are omitted
- Supervisor handles ingest/analyze directly (no meta-review agent in B1)
- After single epoch, supervisor writes termination and exits

### Pacing pre-flight (P8, Obligation 6)

The supervisor template implements the pacing pre-flight as a mandatory
refusal gate before starting any network-touching sub-agent:

- Checks `dde doctor --json` output for pace tier and resolved path
- Requires `shared` tier and identical resolved path across all sub-agents
- On failure: writes `meta/pacing.json` with `status: "fail"`, writes
  `meta/termination.json` with `reason: "error"`, fires
  `hypex.pacing_uncoordinated` relay, and terminates
- Does NOT degrade to smaller roster or warn-and-continue

## Boundaries respected

- Did NOT create `commands/hypex.py` (dev-hypex-commands)
- Did NOT modify `install.sh` or `doctor.py` (dev-hypex-provision)
- Did NOT create skills (dev-hypex-skill)
- Did NOT create B2 templates (hypex-evolution, hypex-meta-review, hypex-proximity)
- Did NOT modify existing DDE templates

## Verification

Verification commands (`dde doctor`, build, tests) could not be run in this
environment — the DDE CLI is not installed in this workspace. Verification
was limited to:
- Structural review: all templates follow the existing DDE template pattern
  (agents.md + scion-agent.yaml + system-prompt.md)
- Content review: all `lit` references removed, all DDE conventions adopted,
  all six boundary obligations implemented, B1 constraint enforced
