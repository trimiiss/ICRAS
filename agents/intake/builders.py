"""Context packet and document inventory builders for intake."""

from pathlib import Path
from typing import Any, Mapping

from schemas.context_packet import ContextPacket
from schemas.document_inventory import (
    DocumentInventory,
    DocumentInventoryItem,
    DocumentType,
)


def build_context_packet(
    bundle_data: Mapping[str, Any],
    manifest: Mapping[str, Any],
    run_id: str,
) -> ContextPacket:
    """Create the validated context packet for downstream agents."""
    effective_date = manifest.get("effective_date")
    return ContextPacket(
        run_id=run_id,
        bundle_name=str(manifest["bundle_name"]),
        contract_type=str(manifest["contract_type"]),
        counterparty=str(manifest["counterparty"]),
        jurisdiction=str(manifest["jurisdiction"]),
        effective_date=str(effective_date) if effective_date is not None else None,
        contract_file=str(manifest["contract_file"]),
        playbook=dict(bundle_data.get("playbook", {})),
        approval_policy=dict(bundle_data.get("approval_policy", {})),
        jurisdiction_rules=dict(bundle_data.get("jurisdiction_rules", {})),
    )


def build_document_inventory(
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
        document_type, included, reason = classify_file(relative_path, contract_file)
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


def classify_file(
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
