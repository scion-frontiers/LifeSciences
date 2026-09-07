# Legacy CLI — retained for reference, not for use

`dde_cli.py` is the pre-artifact-contract Co-Scientist CLI. It is kept
here so its extraction logic can be consulted while the remaining
subcommands are ported. **It must not be invoked in the pilot.**

## Why it was withdrawn

It is broken against every real tournament export we hold, and it fails
in the specific way `docs/tool-design-guidance.md` §8 forbids: silently,
with exit code 0.

It binds to minified JavaScript keys by literal name — `gs` for the idea
list, `eOa` for the executive report, plus the attribute `Gene Symbol`.
Both exports in the scratchpad use `Ur`, `BVa`, and `Target Gene`. These
names are minifier output; they change between Co-Scientist builds and
carry no meaning.

Verified against
`.attachments/_webchat/b53acbf9-.../co-scientistOutput.json`
on 2026-08-18:

| command | behaviour | exit |
|---|---|---|
| `overview` | prints title, goal and stats, then **silently omits the entire top-ideas table** — an absent `gs` reads as an empty list, so the render loop never runs | 0 |
| `categories` | prints `Error: No ideas found in this file.` | 0 |
| `ideas-table` | prints `Error: No ideas found in this file.` | 0 |
| `claims --rank 1` | prints `Error: No ideas found in this file.` | 0 |

`overview` is the dangerous one. It produces a plausible, well-formatted
report of a tournament with no ideas in it, and reports success. Nothing
downstream could detect the loss.

## Replacement

`dde coscientist` resolves each section by its *shape* — the idea list
is "the top-level list of objects carrying `eloRating` and `ranking`" —
and raises `SchemaError` when it cannot find one. The resolved key name
is recorded in the provenance sidecar so a reviewer can confirm which
key was used.

| legacy | replacement |
|---|---|
| `overview`, `ideas-table`, `categories` | `dde coscientist analyze` (`--json` for the full record) |
| `idea` | `dde coscientist show --rank N \| --gene SYM` |
| `claims` | claim counts in `analyze`; text via `show --section verification-summary` |
| `list-files` | dropped — use the shell |
| `references`, `knowledge`, `report`, `compare` | **not yet ported** |

The unported subcommands are the reason this file is retained rather
than deleted. Port from it; do not run it.
