"""Project root resolution and artifact directory layout.

Agents are unreliable about working directory, so the CLI never trusts
CWD for output placement. See docs/tool-design-guidance.md §4.

Resolution order:
  1. $DDE_PROJECT
  2. walk up from CWD looking for a `.dde/` marker directory
  3. fail loudly — never silently write into CWD

Additionally, the dde repo itself is refused as a project root. In
development /workspace *is* the dde repo, and writing raw/ into it
would pollute the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ProjectRootError

# Marker placed at the root of the dde *source repo*. Its presence
# means "this directory is the toolchain, not a drug program".
REPO_MARKER = ".dde-repo"

# Marker directory identifying a dde *program* directory.
PROJECT_MARKER = ".dde"

# One directory per artifact class (§4). Keys are artifact classes used
# by subcommands; values are paths relative to the project root.
ARTIFACT_DIRS: dict[str, str] = {
    "admet": "raw/admet",
    "analogs": "raw/analogs",
    "assays": "raw/assays",
    "bioactivity": "raw/bioactivity",
    "compound": "raw/compounds",
    "compounds": "raw/compounds",
    "descriptors": "raw/descriptors",
    "docking": "raw/docking",
    "expression": "raw/expression",
    "genetics": "raw/genomics",
    "genomics": "raw/genomics",
    "gtex": "raw/gtex",
    "hypotheses": "raw/hypotheses",
    "literature": "raw/literature",
    "mmp": "raw/mmp",
    "mpo": "raw/mpo",
    "pk": "raw/pk",
    "pocket": "raw/pocket",
    "regulatory": "raw/regulatory",
    "safety": "raw/safety",
    "sar": "raw/sar",
    "screening": "raw/screening",
    "single-cell": "raw/single-cell",
    "structures": "raw/structures",
    "tox": "raw/tox",
    "transcriptomics": "raw/transcriptomics",
}


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project root plus how we found it."""

    root: Path
    source: str  # "DDE_PROJECT" | ".dde walk-up"

    def artifact_dir(self, artifact_class: str, override: str | os.PathLike | None = None) -> Path:
        """Return (and create) the output directory for an artifact class.

        `override` corresponds to a subcommand's --out flag. A relative
        override resolves against the project root, never against CWD.
        """
        if override is not None:
            path = Path(override)
            target = path if path.is_absolute() else self.root / path
        else:
            try:
                rel = ARTIFACT_DIRS[artifact_class]
            except KeyError:
                raise ProjectRootError(
                    f"unknown artifact class {artifact_class!r}",
                    detail=f"known classes: {', '.join(sorted(ARTIFACT_DIRS))}",
                )
            target = self.root / rel
        target.mkdir(parents=True, exist_ok=True)
        return target

    def relative(self, path: Path) -> str:
        """Render a path relative to the project root when possible."""
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(path)


def _looks_like_dde_repo(path: Path) -> bool:
    """Heuristic backstop for the explicit repo marker."""
    if (path / REPO_MARKER).exists():
        return True
    return (
        (path / "docs" / "tool-design-guidance.md").is_file()
        and (path / "tools").is_dir()
        and not (path / PROJECT_MARKER).is_dir()
    )


def resolve_project(explicit: str | os.PathLike | None = None) -> ProjectContext:
    """Resolve the dde project root, or raise ProjectRootError."""
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        source = "--project"
        if not root.is_dir():
            raise ProjectRootError(
                f"--project path does not exist: {root}",
                remedy="create the directory, or point --project at an existing program directory",
            )
        return _validate(root, source)

    env = os.environ.get("DDE_PROJECT")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise ProjectRootError(
                f"DDE_PROJECT points at a non-existent directory: {root}",
                remedy="create it, or unset DDE_PROJECT to use .dde/ discovery",
            )
        return _validate(root, "DDE_PROJECT")

    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / PROJECT_MARKER).is_dir():
            return _validate(candidate, f"{PROJECT_MARKER}/ walk-up")

    raise ProjectRootError(
        "could not resolve the dde project root",
        detail=f"no {PROJECT_MARKER}/ directory found in {here} or any parent, "
        "and DDE_PROJECT is not set",
        remedy=(
            "set DDE_PROJECT to your program directory, or run "
            "`dde init <dir>` to create one"
        ),
    )


def _validate(root: Path, source: str) -> ProjectContext:
    """Refuse the dde source repo; refuse unwritable roots."""
    if _looks_like_dde_repo(root):
        raise ProjectRootError(
            f"refusing to use the dde source repo as a project root: {root}",
            detail=(
                "writing raw/ here would pollute the repository. This is the "
                "development case called out in tool-design-guidance.md §4."
            ),
            remedy=(
                "set DDE_PROJECT to the program directory, e.g.\n"
                "           export DDE_PROJECT=/workspace/program-hr-mbc\n"
                "  or create a new one:\n"
                "           dde init /workspace/my-program\n"
                "           export DDE_PROJECT=/workspace/my-program"
            ),
        )
    if not os.access(root, os.W_OK):
        raise ProjectRootError(
            f"project root is not writable: {root}",
            detail=f"resolved from {source}",
        )
    return ProjectContext(root=root, source=source)


def init_project(path: str | os.PathLike) -> Path:
    """Create a program directory with a `.dde/` marker."""
    root = Path(path).expanduser().resolve()
    if _looks_like_dde_repo(root):
        raise ProjectRootError(
            f"refusing to initialise a program inside the dde source repo: {root}",
            remedy="choose a directory outside the repo",
        )
    (root / PROJECT_MARKER).mkdir(parents=True, exist_ok=True)
    for rel in ARTIFACT_DIRS.values():
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "findings").mkdir(parents=True, exist_ok=True)

    # Control plane directories (issue #22).
    from .controlstore import ensure_control_dirs
    ensure_control_dirs(root)

    return root
