"""Intake Agent public entry point."""

from pathlib import Path
from typing import Any, Dict, Mapping

from agents.intake.builders import build_context_packet, build_document_inventory
from agents.intake.errors import IntakeAgentError
from utils.artifacts import validate_run_dir, write_model_json
from utils.run_manager import append_audit_event


def run_intake(
    bundle_data: Dict[str, Any],
    run_id: str,
    run_dir: str | Path,
) -> Dict[str, Any]:
    """Process an incoming bundle and produce an initial context packet.

    Args:
        bundle_data: Validated bundle data from the bundle loader.
        run_id: Unique run identifier.
        run_dir: Directory where run artifacts must be written.

    Returns:
        A dictionary containing validated intake artifacts and artifact paths.
    """
    run_path = validate_run_dir(
        run_dir,
        error_type=IntakeAgentError,
        before_action="running intake",
    )
    manifest = _require_mapping(bundle_data, "manifest")
    bundle_dir = Path(_require_str(bundle_data, "bundle_dir")).resolve()

    context_packet = build_context_packet(bundle_data, manifest, run_id)
    document_inventory = build_document_inventory(bundle_dir, manifest, run_id)

    context_path = run_path / "context_packet.json"
    inventory_path = run_path / "document_inventory.json"

    write_model_json(context_path, context_packet)
    write_model_json(inventory_path, document_inventory)

    unsupported_count = sum(
        1 for item in document_inventory.documents if not item.included
    )
    append_audit_event(
        run_path,
        {
            "event": "intake_completed",
            "agent": "intake_agent",
            "message": "Intake Agent created context and document inventory artifacts.",
            "artifacts": [context_path.name, inventory_path.name],
            "document_count": len(document_inventory.documents),
            "unsupported_document_count": unsupported_count,
        },
    )

    return {
        "context_packet": context_packet.model_dump(mode="json"),
        "document_inventory": document_inventory.model_dump(mode="json"),
        "artifact_paths": {
            "context_packet": str(context_path),
            "document_inventory": str(inventory_path),
        },
    }


def _require_mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Read a mapping value from bundle data with a developer-friendly error."""
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise IntakeAgentError(
            f"Expected bundle_data['{key}'] to be a mapping. "
            "Load the bundle with utils.bundle_loader.load_bundle first."
        )
    return value


def _require_str(data: Mapping[str, Any], key: str) -> str:
    """Read a string value from bundle data with a developer-friendly error."""
    value = data.get(key)
    if not isinstance(value, str):
        raise IntakeAgentError(
            f"Expected bundle_data['{key}'] to be a string. "
            "Load the bundle with utils.bundle_loader.load_bundle first."
        )
    return value
