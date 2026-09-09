"""`dde artifact` — artifact management commands.

Provides ``dde artifact register`` for assigning provenance sidecars to
files produced outside the DDE tool surface (e.g. files fetched from
external APIs).  The sidecar is machine-generated with
``type: "registration"`` so it is clearly distinguishable from
production sidecars written by DDE tools.

This is NOT a science tool and does NOT modify the registered file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

from ..common import (
    AppState,
    DDEGroup,
    emitter,
    out_option,
    output_options,
    pass_state,
)
from ..core.errors import ArtifactError, Refusal
from ..core.provenance import Sidecar


@click.group(cls=DDEGroup)
def artifact() -> None:
    """Artifact management commands."""


@artifact.command("register")
@click.argument("paths", nargs=-1, required=True)
@click.option("--source", required=True, help="Source description (e.g. 'EBI ClustalO via WebFetch').")
@click.option("--description", "description", default=None, help="Optional longer description.")
@output_options
@pass_state
def register_cmd(
    state: AppState,
    paths: tuple[str, ...],
    source: str,
    description: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Register external files with provenance sidecars.

    Writes a ``.meta.json`` sidecar with ``type: "registration"`` for
    each given path.  Refuses to overwrite an existing sidecar.

    The sidecar records the file's SHA-256 digest, size, the provided
    source description, and the current ``$DDE_WORK_ORDER_ID`` and
    ``$SCION_AGENT_SLUG`` (if set).
    """
    emit = emitter(as_json, quiet)
    project = state.project()

    for raw_path in paths:
        file_path = Path(raw_path)
        # Resolve against project root for relative paths.
        if not file_path.is_absolute():
            file_path = project.root / file_path
        file_path = file_path.resolve()

        # Verify file exists.
        if not file_path.is_file():
            raise ArtifactError(
                f"file not found: {raw_path}",
                detail=f"resolved to {file_path}",
                remedy="check the path and try again",
            )

        # Refuse paths outside the project root.
        if not file_path.is_relative_to(project.root.resolve()):
            raise Refusal(
                f"path is outside the project root: {raw_path}",
                detail=f"project root is {project.root}",
                remedy="provide a path within the project directory",
            )

        # Determine the sidecar path.
        sidecar_path = file_path.parent / f"{file_path.name}.meta.json"

        # Refuse to overwrite an existing sidecar.
        if sidecar_path.exists():
            raise Refusal(
                f"sidecar already exists: {sidecar_path.name}",
                detail=f"a .meta.json already covers {file_path.name}",
                remedy="remove the existing sidecar first if re-registration is intended",
            )

        # Build the sidecar.
        sc = Sidecar(tool="external-registration", subcommand="register")
        sc.add_output(file_path)
        sc.note("type", "registration")
        sc.note("source", source)
        if description is not None:
            sc.note("description", description)
        sc.note("registered_by", os.environ.get("SCION_AGENT_SLUG") or "unattributed")

        sc.write(sidecar_path)

        rel = str(sidecar_path.relative_to(project.root))
        if as_json:
            emit.data("sidecar", rel)
            emit.data("registered", str(file_path.relative_to(project.root)))
        else:
            emit.line(f"Registered: {rel}")
        emit.path(sidecar_path, role="sidecar")

    emit.flush()
