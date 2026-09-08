# Provision hypex/elo/prox binaries — ENV_VERSION partition

**Date:** 2026-09-08
**Agent:** dev-hypex-provision
**Branch:** scion/dev-hypex-provision (targeting origin/DDE)
**Design ref:** hypex-dde-integration.md rev 3.4, §4.2.3

## What was done

Provisioned the three hypex CLI binaries (`hypex`, `elo`, `prox`) into DDE's
tool environment, following the existing install.sh pattern.

### install.sh

- Added `install_hypex()`, `install_elo()`, `install_prox()` functions with
  version constants and download URLs
- Added all three to the binary loop (`for tool in ... hypex elo prox`)
- All three land in `$DDE_TOOLS_HOME/bin` alongside vina, fpocket, rate4site

### doctor.py

- Added `hypex`, `elo`, `prox` to `_PROVISIONED_BINARIES` with purpose strings
  describing their tournament roles and the standard `--binaries-only` remedy

### Controller template (research-operations-controller/agents.md)

- **Capability map:** added row mapping `hypex`, `elo`, `prox` doctor checks to
  blocked skill `hypothesis-exploration`
- **Single-flight resources:** added `hypex-tournament` row with 120-min default
  TTL and 3x30-min extensions (210-minute ceiling), per design §4.2.1
- **Bootstrap mkdir:** added `findings/hypothesis-exploration/` to the findings
  directory list. Verified `raw/hypotheses/` was already present.

## ENV_VERSION partition event

Adding the three binaries to `$DDE_TOOLS_HOME/bin` partitions ENV_VERSION.
This is by design (§4.2.3): the partition is paid once, in a single commit,
and announced in the commit message. All three binaries land in one commit
to produce exactly one partition event.

## Placeholder URLs

The download URLs for all three binaries use `PLACEHOLDER://` scheme. The
`scion-frontiers/hypex` repo has not published GitHub releases, and the
brief instructs use of clearly-marked placeholders rather than invented URLs.
These must be replaced with real release URLs before the binaries can
actually be provisioned.

Versions used:
- hypex: 1.0.0 (no version string found in Go source; design references hypex@1.0)
- elo: 1.0.0 (same repo, same release cadence assumed)
- prox: 0.1.0 (from pyproject.toml `version = "0.1.0"`)

## Verification

- `bash -n install.sh` — syntax OK
- `python3 -c "import ast; ast.parse(...)"` on doctor.py — syntax OK
- Full test suite not available in this environment
