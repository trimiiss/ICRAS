"""Intake Agent — validates and prepares incoming contract bundles.

This agent will be the first in the pipeline. It receives a raw bundle,
validates its contents, and produces a ContextPacket for downstream agents.

LLM logic will be added in a later user story.
"""

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from schemas.context_packet import ContextPacket
from schemas.document_inventory import (
    DocumentInventory,
    DocumentInventoryItem,
    DocumentType,
)
from utils.run_manager import append_audit_event


class IntakeAgentError(Exception):
    """Raised when intake cannot create required artifacts."""


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
    run_path = _validate_run_dir(run_dir)
    manifest = _require_mapping(bundle_data, "manifest")
    bundle_dir = Path(_require_str(bundle_data, "bundle_dir")).resolve()

    context_packet = _build_context_packet(bundle_data, manifest, run_id)
    document_inventory = _build_document_inventory(bundle_dir, manifest, run_id)

    context_path = run_path / "context_packet.json"
    inventory_path = run_path / "document_inventory.json"

    _write_model_json(context_path, context_packet)
    _write_model_json(inventory_path, document_inventory)

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


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear intake error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise IntakeAgentError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before running intake."
        )
    if not run_path.is_dir():
        raise IntakeAgentError(f"Run path is not a directory: {run_path}")
    return run_path


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


def _build_context_packet(
    bundle_data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    run_id: str,
) -> ContextPacket:
    """Create the validated context packet for downstream agents."""
    return ContextPacket(
        run_id=run_id,
        bundle_name=str(manifest["bundle_name"]),
        contract_type=str(manifest["contract_type"]),
        counterparty=str(manifest["counterparty"]),
        jurisdiction=str(manifest["jurisdiction"]),
        contract_file=str(manifest["contract_file"]),
        playbook=dict(bundle_data.get("playbook", {})),
        approval_policy=dict(bundle_data.get("approval_policy", {})),
        jurisdiction_rules=dict(bundle_data.get("jurisdiction_rules", {})),
    )


def _build_document_inventory(
    bundle_dir: Path,
    manifest: Mapping[str, Any],
    run_id: str,
) -> DocumentInventory:
    """Classify bundle files and return a validated document inventory."""
    bundle_name = str(manifest["bundle_name"])
    contract_file = str(manifest["contract_file"])
    documents: list[DocumentInventoryItem] = []
    primary_contract_id: str | None = None

    for index, path in enumerate(sorted(bundle_dir.iterdir()), start=1):
        if not path.is_file():
            continue

        document_id = f"DOC-{index:03d}"
        relative_path = path.relative_to(bundle_dir).as_posix()
        document_type, included, reason = _classify_file(relative_path, contract_file)
        is_primary = relative_path == contract_file
        if is_primary:
            primary_contract_id = document_id

        documents.append(
            DocumentInventoryItem(
                document_id=document_id,
                file_name=path.name,
                relative_path=relative_path,
                file_extension=path.suffix.lower(),
                document_type=document_type,
                file_size_bytes=path.stat().st_size,
                is_primary=is_primary,
                included=included,
                reason=reason,
            )
        )

    return DocumentInventory(
        run_id=run_id,
        bundle_name=bundle_name,
        primary_contract_id=primary_contract_id,
        documents=documents,
    )


def _classify_file(
    relative_path: str,
    contract_file: str,
) -> tuple[DocumentType, bool, str | None]:
    """Classify one bundle file for intake inventory purposes."""
    file_name = Path(relative_path).name
    extension = Path(relative_path).suffix.lower()

    known_files: dict[str, DocumentType] = {
        "manifest.yaml": DocumentType.MANIFEST,
        contract_file: DocumentType.CONTRACT,
        "vendor_master.csv": DocumentType.VENDOR_MASTER,
        "playbook.yaml": DocumentType.PLAYBOOK,
        "approval_policy.yaml": DocumentType.APPROVAL_POLICY,
        "jurisdiction_rules.yaml": DocumentType.JURISDICTION_RULES,
    }
    if relative_path in known_files:
        return known_files[relative_path], True, "Required bundle file."

    if extension == ".pdf":
        return DocumentType.SUPPORTING_DOCUMENT, True, "Optional supporting PDF."
    if extension in {".yaml", ".yml"}:
        return DocumentType.SUPPORTING_POLICY, True, "Optional supporting policy file."
    if extension == ".csv":
        return DocumentType.SUPPORTING_DATA, True, "Optional supporting data file."

    return (
        DocumentType.UNSUPPORTED,
        False,
        f"Unsupported file type for intake: {file_name}",
    )


def _write_model_json(path: Path, model: ContextPacket | DocumentInventory) -> None:
    """Write a Pydantic model as deterministic, formatted JSON."""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(model.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
        file.write("\n")
