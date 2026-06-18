"""Run Manager — creates and manages deterministic run folders.

Each pipeline execution gets a unique run folder under ``runs/`` containing:
    - metadata.json  — run ID, bundle path, timestamps, status
    - config.json    — initial pipeline configuration
    - audit_log.jsonl — audit trail (initially empty)
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Default base directory for run outputs (relative to project root).
DEFAULT_RUNS_DIR = Path("runs")


def create_run_id() -> str:
    """Generate a unique, human-readable run ID.

    Format: ``YYYYMMDD_HHMMSS_<short-uuid>``

    The UTC timestamp makes IDs naturally sortable, and the UUID suffix
    guarantees uniqueness even if two runs start in the same second.

    Returns:
        A unique run ID string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{timestamp}_{short_uuid}"


def create_run_folder(
    bundle_path: str,
    runs_dir: Optional[Path] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new run folder with initial metadata, config, and audit log.

    This function:
        1. Generates a unique run_id.
        2. Creates ``runs/<run_id>/`` (never overwrites an existing folder).
        3. Writes ``metadata.json`` with run_id, bundle_path, timestamps, and status.
        4. Writes ``config.json`` with the initial pipeline configuration.
        5. Creates an empty ``audit_log.jsonl``.

    Args:
        bundle_path: Path to the contract bundle that triggered this run.
        runs_dir: Base directory for run outputs. Defaults to ``runs/``
            in the current working directory.
        config: Optional initial pipeline configuration. If ``None``, a
            sensible default configuration is used.

    Returns:
        A dictionary with:
            - run_id: the generated run identifier
            - run_dir: absolute path to the created run folder
            - metadata: the metadata dictionary that was written

    Raises:
        FileExistsError: If the generated run folder already exists
            (extremely unlikely but handled defensively).
    """
    if runs_dir is None:
        runs_dir = DEFAULT_RUNS_DIR

    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)

    run_id = create_run_id()
    run_dir = runs_dir / run_id

    # Defensive: never overwrite an existing run folder
    if run_dir.exists():
        raise FileExistsError(
            f"Run folder already exists (UUID collision): {run_dir}. Please try again."
        )

    run_dir.mkdir(parents=True)

    # Build metadata
    now_utc = datetime.now(timezone.utc).isoformat()
    metadata: Dict[str, Any] = {
        "run_id": run_id,
        "bundle_path": str(Path(bundle_path).resolve()),
        "created_at": now_utc,
        "status": "initialized",
    }

    # Build config
    if config is None:
        config = _default_config(bundle_path)

    # Write metadata.json
    _write_json(run_dir / "metadata.json", metadata)

    # Write config.json
    _write_json(run_dir / "config.json", config)

    # Create empty audit log
    (run_dir / "audit_log.jsonl").touch()

    return {
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "metadata": metadata,
    }


def append_audit_event(run_dir: str | Path, event: Dict[str, Any]) -> None:
    """Append a timestamped audit event to ``audit_log.jsonl`` and ``audit_log.md``.

    Args:
        run_dir: Directory for the current pipeline run.
        event: JSON-serializable event details to append.

    Raises:
        FileNotFoundError: If the run directory does not exist.
    """
    run_path = Path(run_dir)
    if not run_path.is_dir():
        raise FileNotFoundError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before writing audit events."
        )

    event_with_time: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with open(run_path / "audit_log.jsonl", "a", encoding="utf-8") as f:
        json.dump(event_with_time, f, ensure_ascii=False)
        f.write("\n")
    _append_markdown_audit_event(run_path / "audit_log.md", event_with_time)


def update_run_status(
    run_dir: str | Path,
    status: str,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Update the status in a run folder's ``metadata.json``.

    Args:
        run_dir: Directory for the current pipeline run.
        status: New run status, such as ``initialized`` or ``failed``.
        error_message: Optional error message to persist for failed runs.

    Returns:
        The updated metadata dictionary.

    Raises:
        FileNotFoundError: If ``metadata.json`` does not exist.
    """
    metadata_path = Path(run_dir) / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(
            f"Run metadata file does not exist: {metadata_path}. "
            "Create the run folder before updating status."
        )

    with open(metadata_path, "r", encoding="utf-8") as f:
        metadata: Dict[str, Any] = json.load(f)

    metadata["status"] = status
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    if error_message is not None:
        metadata["error_message"] = error_message

    _write_json(metadata_path, metadata)
    return metadata


def _default_config(bundle_path: str) -> Dict[str, Any]:
    """Return a sensible default pipeline configuration.

    Args:
        bundle_path: Path to the bundle, stored in the config for traceability.

    Returns:
        A configuration dictionary.
    """
    return {
        "pipeline_version": "0.1.0",
        "bundle_path": str(Path(bundle_path).resolve()),
        "agents": [
            "intake",
            "evidence_index",
            "extraction",
            "counterparty",
            "validation",
            "risk",
            "compliance",
            "anomaly",
            "orchestrator",
        ],
        "orchestration": "langgraph",
        "pipeline_order": [
            "intake",
            "evidence_index",
            "extraction",
            "counterparty_and_validation",
            "risk",
            "compliance",
            "anomaly",
            "orchestrator",
        ],
        "settings": {
            "manual_review_confidence_threshold": 0.75,
            "require_human_review_above": "HIGH",
        },
    }


def _append_markdown_audit_event(filepath: Path, event: Dict[str, Any]) -> None:
    """Append one audit event in a readable markdown format."""
    if not filepath.exists():
        filepath.write_text("# ICRAS Audit Log\n\n", encoding="utf-8")

    summary = [
        f"## {event.get('event', 'event')}",
        f"- Timestamp: {event.get('timestamp', '')}",
        f"- Agent: {event.get('agent', '')}",
        f"- Message: {event.get('message', '')}",
    ]
    if event.get("error"):
        summary.append(f"- Error: {event['error']}")
    if event.get("artifacts"):
        artifacts = ", ".join(str(artifact) for artifact in event["artifacts"])
        summary.append(f"- Artifacts: {artifacts}")

    with open(filepath, "a", encoding="utf-8") as file:
        file.write("\n".join(summary))
        file.write("\n\n")


def _write_json(filepath: Path, data: Dict[str, Any]) -> None:
    """Write a dictionary as formatted JSON to a file.

    Args:
        filepath: Destination file path.
        data: Dictionary to serialize.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
