"""Record I/O for the control plane state store under ``.dde/control/``.

The state store holds one JSON file per record — work orders, context
snapshots, runs, and validation records — plus an append-only event log.
This module provides the CRUD layer with schema validation on every
write.  It never touches the network, never applies thresholds, and
never produces Layer 0 artifacts.

Record files are the source of truth for current state; the event log
(``events.ndjson``) is the supplementary audit trail.  See the design
document (issue-22-design.md §3.1) for the rationale.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import ArtifactError, SchemaError
from .statemachine import RUN_STATES, WORK_ORDER_STATES

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

CONTROL_DIR = ".dde/control"

RECORD_TYPES: dict[str, str] = {
    "work-order": "work-orders",
    "context": "contexts",
    "run": "runs",
    "validation": "validations",
    "lease": "leases",
    "policy": "policies",
    "snapshot": "snapshots",
}

_SUBDIRS = list(RECORD_TYPES.values())
_EVENT_LOG = "events.ndjson"


def ensure_control_dirs(project_root: str | Path) -> None:
    """Idempotently create the control directory tree.

    Creates ``.dde/control/`` and all record subdirectories, plus an
    empty ``events.ndjson`` file (only if it does not already exist).
    """
    root = Path(project_root)
    control = root / CONTROL_DIR
    control.mkdir(parents=True, exist_ok=True)
    for subdir in _SUBDIRS:
        (control / subdir).mkdir(parents=True, exist_ok=True)
    events = control / _EVENT_LOG
    if not events.exists():
        events.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _control_path(project_root: Path, record_type: str) -> Path:
    """Return the directory for *record_type*, raising on unknown types."""
    subdir = RECORD_TYPES.get(record_type)
    if subdir is None:
        raise SchemaError(
            f"unknown record type {record_type!r}",
            detail=f"known types: {', '.join(sorted(RECORD_TYPES))}",
        )
    return project_root / CONTROL_DIR / subdir


_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _record_path(project_root: Path, record_type: str, identifier: str) -> Path:
    """Full path to a single record file.

    Validates that *identifier* contains only safe characters to prevent
    path traversal (e.g. ``../../evil`` writing outside the control dir).
    """
    if not _SAFE_IDENTIFIER_RE.match(identifier):
        raise SchemaError(
            f"invalid record identifier {identifier!r}",
            detail="identifiers must contain only alphanumeric characters, "
            "hyphens, and underscores",
        )
    return _control_path(project_root, record_type) / f"{identifier}.json"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_WO_ID_RE = re.compile(r"^WO-\d{3,}$")
_RUN_ID_RE = re.compile(r"^RUN-\d{3,}$")


def _flatten_entry(entry: Any, key: str) -> str:
    """Extract a string from a structured deliverable entry.

    If *entry* is a dict, return the value of *key* (falling back to
    ``"name"``, ``"class"``, then ``"path"``).  If *entry* is already a
    string, return it unchanged.
    """
    if isinstance(entry, dict):
        for k in (key, "name", "class", "path"):
            if k in entry:
                return str(entry[k])
        # Last resort: use the first string value
        for v in entry.values():
            if isinstance(v, str):
                return v
        return str(entry)
    return str(entry)


def normalize_deliverables(deliverables: dict[str, Any]) -> dict[str, Any]:
    """Normalize deliverable key names to the canonical schema.

    WO authors may use either ``layer_0_classes`` (the canonical name per
    the issue-22 design) or the shorter ``layer_0`` (used by Phase 3
    controllers).  Both refer to the same concept: a list of
    artifact-class names whose Layer 0 artifacts the work order covers.

    Returns a *new* dict with canonical keys.  If both ``layer_0`` and
    ``layer_0_classes`` are present, ``layer_0_classes`` wins (it is the
    documented canonical name) and ``layer_0`` is dropped from the result
    to avoid orphan-key confusion.

    Additionally, structured entries (dicts with a ``class`` or ``name``
    key) are flattened to plain strings so downstream consumers can
    safely sort and compare them.
    """
    normalized = dict(deliverables)

    if "layer_0_classes" not in normalized and "layer_0" in normalized:
        normalized["layer_0_classes"] = normalized.pop("layer_0")
    elif "layer_0_classes" in normalized and "layer_0" in normalized:
        # Canonical key wins; drop the alias to prevent orphan confusion.
        del normalized["layer_0"]

    # Flatten structured entries to plain strings.
    if "layer_0_classes" in normalized and isinstance(normalized["layer_0_classes"], list):
        normalized["layer_0_classes"] = [
            _flatten_entry(e, "class") for e in normalized["layer_0_classes"]
        ]

    if "layer_1" in normalized and isinstance(normalized["layer_1"], list):
        normalized["layer_1"] = [
            _flatten_entry(e, "path") for e in normalized["layer_1"]
        ]

    return normalized


def _validate_work_order(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "id", "revision", "state", "decision_question", "requested_role",
        "stage", "cycle", "context", "dependencies", "capabilities",
        "deliverables", "acceptance_criteria", "alert_policy", "priority",
        "resource_class", "report_to", "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "id" in data and not _WO_ID_RE.match(str(data["id"])):
        errors.append(f"id must match WO-NNN, got {data['id']!r}")

    if "revision" in data:
        rev = data["revision"]
        if not isinstance(rev, int) or rev < 1:
            errors.append(f"revision must be a positive integer, got {rev!r}")

    if "state" in data and data["state"] not in WORK_ORDER_STATES:
        errors.append(
            f"state {data['state']!r} is not a legal work-order state; "
            f"legal states: {', '.join(sorted(WORK_ORDER_STATES))}"
        )

    # Top-level type checks for structured fields (design §3.1: "validates
    # field types").  Deep validation of nested structure is deferred to
    # the command layer (e.g. commit checksums artifact links).
    _FIELD_TYPES: dict[str, type] = {
        "context": dict, "dependencies": list, "capabilities": list,
        "deliverables": dict, "alert_policy": dict,
    }
    for field, expected_type in _FIELD_TYPES.items():
        if field in data and not isinstance(data[field], expected_type):
            errors.append(
                f"{field} must be {expected_type.__name__}, "
                f"got {type(data[field]).__name__}"
            )

    return errors


def _validate_run(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "run_id", "work_order_id", "work_order_revision",
        "state", "attempt", "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "run_id" in data and not _RUN_ID_RE.match(str(data["run_id"])):
        errors.append(f"run_id must match RUN-NNN, got {data['run_id']!r}")

    if "attempt" in data:
        att = data["attempt"]
        if not isinstance(att, int) or att < 1:
            errors.append(f"attempt must be a positive integer, got {att!r}")

    if "state" in data and data["state"] not in RUN_STATES:
        errors.append(
            f"state {data['state']!r} is not a legal run state; "
            f"legal states: {', '.join(sorted(RUN_STATES))}"
        )

    return errors


def _validate_context(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "work_order_id", "revision", "artifact_links",
        "content", "content_sha256", "created_at",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    return errors


def _validate_validation(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "work_order_id", "work_order_revision", "run_id",
        "validated_at", "result", "checks",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    return errors


def _validate_lease(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty if valid)."""
    required = [
        "resource", "state", "work_order_id", "work_order_revision",
        "run_id", "holder_agent", "granted_at", "ttl_minutes", "expires_at",
        "extensions",
    ]
    errors: list[str] = []
    missing = [f for f in required if f not in data]
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")

    if "state" in data and data["state"] not in {"held", "released", "expired"}:
        errors.append(
            f"state {data['state']!r} is not a legal lease state; "
            "legal states: held, released, expired"
        )

    if "extensions" in data:
        ext = data["extensions"]
        if not isinstance(ext, int) or ext < 0:
            errors.append(f"extensions must be a non-negative integer, got {ext!r}")

    if "ttl_minutes" in data:
        ttl = data["ttl_minutes"]
        if not isinstance(ttl, int) or ttl < 1:
            errors.append(f"ttl_minutes must be a positive integer, got {ttl!r}")

    return errors


def _validate_policy_record(data: dict[str, Any]) -> list[str]:
    """Delegate to policy module's validator."""
    from .policy import validate_policy
    return validate_policy(data)


def _validate_snapshot_record(data: dict[str, Any]) -> list[str]:
    """Delegate to policy module's validator."""
    from .policy import validate_snapshot
    return validate_snapshot(data)


_VALIDATORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "work-order": _validate_work_order,
    "context": _validate_context,
    "run": _validate_run,
    "validation": _validate_validation,
    "lease": _validate_lease,
    "policy": _validate_policy_record,
    "snapshot": _validate_snapshot_record,
}


# ---------------------------------------------------------------------------
# Record I/O
# ---------------------------------------------------------------------------


def read_record(project_root: str | Path, record_type: str, identifier: str) -> dict[str, Any]:
    """Read a JSON record from the state store.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of ``"work-order"``, ``"context"``, ``"run"``, ``"validation"``.
    identifier:
        Filename stem, e.g. ``"WO-001-r1"`` or ``"RUN-003"``.

    Returns
    -------
    dict
        The parsed record.

    Raises
    ------
    ArtifactError
        If the file is missing or not valid JSON.
    """
    root = Path(project_root)
    path = _record_path(root, record_type, identifier)
    if not path.is_file():
        raise ArtifactError(
            f"{record_type} record not found: {identifier}",
            detail=f"expected at {path}",
            remedy=f"create the {record_type} first",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            f"{record_type} record is not valid JSON: {identifier}",
            detail=str(exc),
        )


def write_record(
    project_root: str | Path,
    record_type: str,
    identifier: str,
    data: dict[str, Any],
) -> Path:
    """Validate schema and write a record to the state store.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of ``"work-order"``, ``"context"``, ``"run"``, ``"validation"``.
    identifier:
        Filename stem, e.g. ``"WO-001-r1"`` or ``"RUN-003"``.
    data:
        The record dict.  Validated against the schema for *record_type*
        before writing.

    Returns
    -------
    Path
        The path that was written.

    Raises
    ------
    SchemaError
        If the data fails schema validation.
    """
    root = Path(project_root)
    validator = _VALIDATORS.get(record_type)
    if validator is None:
        raise SchemaError(
            f"unknown record type {record_type!r}",
            detail=f"known types: {', '.join(sorted(RECORD_TYPES))}",
        )

    errors = validator(data)
    if errors:
        raise SchemaError(
            f"invalid {record_type} record: {'; '.join(errors)}",
            detail=f"identifier: {identifier}",
        )

    path = _record_path(root, record_type, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def list_records(
    project_root: str | Path,
    record_type: str,
    filter_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Read all JSON records of a given type, optionally filtered.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        One of ``"work-order"``, ``"context"``, ``"run"``, ``"validation"``.
    filter_fn:
        Optional predicate applied to each parsed record.  Only records
        for which it returns ``True`` are included.

    Returns
    -------
    list[dict]
        The matching records, sorted by filename for determinism.
    """
    root = Path(project_root)
    directory = _control_path(root, record_type)
    if not directory.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # skip unreadable files silently during listing
        if not isinstance(record, dict):
            continue
        if filter_fn is None or filter_fn(record):
            results.append(record)
    return results


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def append_event(project_root: str | Path, event: dict[str, Any]) -> None:
    """Append a single event to ``events.ndjson``.

    The event dict should contain: ``timestamp`` (ISO 8601), ``type``,
    ``subject_id``, ``revision`` (int or None), ``from_state``,
    ``to_state``, ``actor`` (str or None), and optionally ``detail``.

    The event is serialised as a single JSON line and appended.  The
    log is never edited, only appended.
    """
    root = Path(project_root)
    events_path = root / CONTROL_DIR / _EVENT_LOG
    events_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure a timestamp is present.
    if "timestamp" not in event:
        event = dict(event, timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    line = json.dumps(event, separators=(",", ":")) + "\n"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Publish state
# ---------------------------------------------------------------------------

_PUBLISH_STATE_FILE = "publish-state.json"


def read_publish_state(project_root: str | Path) -> dict[str, Any] | None:
    """Read ``publish-state.json`` from ``.dde/control/``.

    Returns the parsed dict, or ``None`` if the file does not exist.

    Raises
    ------
    ArtifactError
        If the file exists but is not valid JSON.
    """
    root = Path(project_root)
    path = root / CONTROL_DIR / _PUBLISH_STATE_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            "publish-state.json is not valid JSON",
            detail=str(exc),
        )


def write_publish_state(project_root: str | Path, data: dict[str, Any]) -> Path:
    """Write ``publish-state.json`` to ``.dde/control/``.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    data:
        The publish-state dict.

    Returns
    -------
    Path
        The path that was written.
    """
    root = Path(project_root)
    path = root / CONTROL_DIR / _PUBLISH_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

_WO_PREFIX_RE = re.compile(r"^WO-(\d{3,})")
_RUN_PREFIX_RE = re.compile(r"^RUN-(\d{3,})")


def next_id(project_root: str | Path, record_type: str) -> str:
    """Generate the next sequential ID for a record type.

    For ``"work-order"``: scans ``work-orders/`` for the highest
    ``WO-NNN`` and returns the next.  Defaults to ``WO-001``.

    For ``"run"``: scans ``runs/`` for the highest ``RUN-NNN`` and
    returns the next.  Defaults to ``RUN-001``.

    Parameters
    ----------
    project_root:
        Path to the dde project root.
    record_type:
        ``"work-order"`` or ``"run"``.

    Returns
    -------
    str
        The next ID, e.g. ``"WO-003"`` or ``"RUN-001"``.
    """
    root = Path(project_root)

    if record_type == "work-order":
        directory = _control_path(root, "work-order")
        pattern = _WO_PREFIX_RE
        prefix = "WO"
    elif record_type == "run":
        directory = _control_path(root, "run")
        pattern = _RUN_PREFIX_RE
        prefix = "RUN"
    else:
        raise SchemaError(
            f"next_id does not support record type {record_type!r}",
            detail="supported types: work-order, run",
        )

    if not directory.is_dir():
        return f"{prefix}-001"

    max_num = 0
    for path in directory.glob("*.json"):
        m = pattern.match(path.stem)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return f"{prefix}-{max_num + 1:03d}"
