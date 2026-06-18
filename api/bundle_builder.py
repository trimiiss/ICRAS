"""Create runtime contract bundles from API uploads."""

import csv
import re
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from schemas.api_contract_review import ContractReviewMetadata, UploadedFilePayload
from utils.run_manager import DEFAULT_RUNS_DIR


class ContractUploadError(Exception):
    """Raised when uploaded files cannot form a valid contract bundle."""


SUPPORTING_FILE_TARGETS: Mapping[str, str] = {
    "vendor_master.csv": "vendor_master.csv",
    "playbook.yaml": "playbook.yaml",
    "playbook.yml": "playbook.yaml",
    "approval_policy.yaml": "approval_policy.yaml",
    "approval_policy.yml": "approval_policy.yaml",
    "jurisdiction_rules.yaml": "jurisdiction_rules.yaml",
    "jurisdiction_rules.yml": "jurisdiction_rules.yaml",
}

REQUIRED_SUPPORTING_FILES: tuple[str, ...] = (
    "vendor_master.csv",
    "playbook.yaml",
    "approval_policy.yaml",
    "jurisdiction_rules.yaml",
)

DEFAULT_PLAYBOOK: Mapping[str, object] = {
    "contract_type": "Uploaded Contract",
    "required_clauses": [
        {
            "clause_type": "governing_law",
            "description": "Governing law required.",
            "severity_if_missing": "HIGH",
        },
        {
            "clause_type": "termination",
            "description": "Termination terms required.",
            "severity_if_missing": "MEDIUM",
        },
        {
            "clause_type": "payment_terms",
            "description": "Payment terms required when fees are referenced.",
            "severity_if_missing": "HIGH",
        },
        {
            "clause_type": "liability_cap",
            "description": "Liability cap required.",
            "severity_if_missing": "HIGH",
        },
        {
            "clause_type": "data_protection",
            "description": "Data protection terms required when personal data is referenced.",
            "severity_if_missing": "HIGH",
        },
    ],
    "prohibited_clauses": [],
}

DEFAULT_APPROVAL_POLICY: Mapping[str, object] = {
    "approval_thresholds": {
        "LOW": {"auto_approve": True, "required_approvers": []},
        "MEDIUM": {"auto_approve": False, "required_approvers": []},
        "HIGH": {"auto_approve": False, "required_approvers": ["legal_counsel"]},
        "CRITICAL": {
            "auto_approve": False,
            "required_approvers": ["legal_counsel", "compliance_officer"],
        },
    },
    "exception_routing": {
        "auto_approve": {
            "category": "AUTO_APPROVE",
            "reason": "No routed exceptions were detected.",
            "next_action": "No human approval required.",
        },
        "rules": [
            {
                "name": "payment_terms",
                "category": "FINANCE",
                "approver": "finance_manager",
                "reason": "Payment-term issues require finance approval.",
                "next_action": "Finance must approve payment terms or request revision.",
                "match_issue_types": [
                    "payment_terms_policy_violation",
                    "payment_terms_exceed_standard",
                    "calculation_error",
                    "contradictory_payment_terms",
                ],
                "match_field_names": ["payment_terms"],
            },
            {
                "name": "compliance",
                "category": "COMPLIANCE",
                "approver": "compliance_officer",
                "reason": "Compliance findings require compliance review.",
                "next_action": "Compliance must approve or request mitigation.",
                "match_categories": ["compliance"],
            },
            {
                "name": "counterparty",
                "category": "MANUAL_REVIEW",
                "approver": "procurement_manager",
                "reason": "Counterparty identity requires manual review.",
                "next_action": "Procurement must confirm counterparty identity.",
                "match_issue_types": ["counterparty_resolution_review"],
                "match_field_names": ["counterparty"],
            },
            {
                "name": "default_legal_review",
                "category": "LEGAL",
                "approver": "legal_counsel",
                "reason": "Contract findings require legal review.",
                "next_action": "Legal must review the finding and approve or request remediation.",
                "match_categories": [
                    "contract_validation",
                    "clause_risk",
                    "contract_anomaly",
                    "counterparty",
                ],
            },
        ],
    },
    "approved_payment_terms": {
        "terms": ["net-30"],
        "severity_if_unapproved": "HIGH",
    },
    "liability_cap_requirements": {
        "required": True,
        "minimum_cap": "fees_paid_12_months",
        "severity_if_missing": "HIGH",
    },
    "auto_renewal_rules": {
        "allowed": False,
        "minimum_notice_days": 30,
        "severity_if_unapproved": "MEDIUM",
    },
    "high_risk_jurisdictions": ["Russia", "Iran", "North Korea", "Syria"],
    "gdpr_requirements": {
        "applies_when_personal_data": True,
        "required_clauses": [
            "data_processing_terms",
            "cross_border_transfer_controls",
            "data_subject_rights",
        ],
        "severity_if_missing": "CRITICAL",
    },
    "manual_review_confidence_threshold": 0.75,
    "risk_tolerance_thresholds": {
        "minor_missing_required_ratio": 0.25,
        "material_missing_required_ratio": 0.5,
    },
}


def create_contract_bundle(
    contract_file: UploadedFilePayload,
    supporting_files: Sequence[UploadedFilePayload],
    metadata: ContractReviewMetadata,
    runs_dir: Path | None = None,
) -> Path:
    """Create a valid runtime bundle from uploaded files."""
    _validate_contract_pdf(contract_file)
    upload_id = _new_upload_id()
    bundle_dir = Path(runs_dir or DEFAULT_RUNS_DIR) / upload_id / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=False)

    contract_filename = "contract.pdf"
    (bundle_dir / contract_filename).write_bytes(contract_file.content)
    _write_default_supporting_files(bundle_dir, metadata)
    _write_uploaded_supporting_files(bundle_dir, supporting_files)
    _write_manifest(bundle_dir, contract_filename, metadata, upload_id)
    return bundle_dir.resolve()


def _validate_contract_pdf(contract_file: UploadedFilePayload) -> None:
    """Validate that the primary upload is a PDF."""
    suffix = Path(contract_file.filename).suffix.lower()
    if suffix != ".pdf":
        raise ContractUploadError(
            "contract_file must be a PDF upload with a .pdf extension."
        )
    if not contract_file.content.startswith(b"%PDF"):
        raise ContractUploadError(
            "contract_file must contain PDF bytes starting with a %PDF header."
        )


def _write_default_supporting_files(
    bundle_dir: Path,
    metadata: ContractReviewMetadata,
) -> None:
    """Write default bundle support files."""
    playbook = dict(DEFAULT_PLAYBOOK)
    playbook["contract_type"] = metadata.contract_type
    _write_yaml(bundle_dir / "playbook.yaml", playbook)
    _write_yaml(bundle_dir / "approval_policy.yaml", DEFAULT_APPROVAL_POLICY)
    _write_yaml(
        bundle_dir / "jurisdiction_rules.yaml",
        {
            "jurisdiction": metadata.jurisdiction,
            "governing_law": {"jurisdiction": metadata.jurisdiction},
            "regulatory_requirements": [],
            "enforceability_notes": ["Generated by the contract review API."],
        },
    )
    _write_vendor_master(bundle_dir / "vendor_master.csv", metadata.counterparty)


def _write_uploaded_supporting_files(
    bundle_dir: Path,
    supporting_files: Sequence[UploadedFilePayload],
) -> None:
    """Validate and write optional uploaded support files."""
    seen_targets: set[str] = set()
    for supporting_file in supporting_files:
        target_name = _supporting_file_target(supporting_file.filename)
        if target_name in seen_targets:
            raise ContractUploadError(
                f"Duplicate supporting file target '{target_name}' was uploaded."
            )
        seen_targets.add(target_name)
        if not supporting_file.content:
            raise ContractUploadError(
                f"Supporting file '{supporting_file.filename}' is empty."
            )
        (bundle_dir / target_name).write_bytes(supporting_file.content)

    missing = [
        filename
        for filename in REQUIRED_SUPPORTING_FILES
        if not (bundle_dir / filename).is_file()
    ]
    if missing:
        raise ContractUploadError(
            "Generated bundle is missing required support files: "
            + ", ".join(missing)
        )


def _supporting_file_target(filename: str) -> str:
    """Return the canonical bundle target name for an uploaded support file."""
    original_name = Path(filename).name.lower()
    target_name = SUPPORTING_FILE_TARGETS.get(original_name)
    if target_name is None:
        allowed = ", ".join(sorted(SUPPORTING_FILE_TARGETS))
        raise ContractUploadError(
            f"Unsupported supporting file '{filename}'. Allowed files: {allowed}."
        )
    return target_name


def _write_manifest(
    bundle_dir: Path,
    contract_filename: str,
    metadata: ContractReviewMetadata,
    upload_id: str,
) -> None:
    """Write manifest.yaml for the generated bundle."""
    manifest: dict[str, object] = {
        "bundle_name": metadata.bundle_name or _slug(upload_id),
        "contract_file": contract_filename,
        "contract_type": metadata.contract_type,
        "counterparty": metadata.counterparty,
        "jurisdiction": metadata.jurisdiction,
        "description": "Generated by the contract review API.",
    }
    if metadata.effective_date is not None:
        manifest["effective_date"] = metadata.effective_date
    _write_yaml(bundle_dir / "manifest.yaml", manifest)


def _write_yaml(path: Path, data: Mapping[str, object]) -> None:
    """Write deterministic YAML."""
    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(dict(data), file, sort_keys=False)


def _write_vendor_master(path: Path, counterparty: str) -> None:
    """Write a minimal vendor master CSV for the uploaded counterparty."""
    output = StringIO()
    fieldnames = [
        "vendor_id",
        "vendor_name",
        "contact_email",
        "country",
        "risk_tier",
        "active",
        "annual_spend_usd",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerow(
        {
            "vendor_id": "API-001",
            "vendor_name": counterparty,
            "contact_email": "",
            "country": "",
            "risk_tier": "low",
            "active": "true",
            "annual_spend_usd": "0",
        }
    )
    path.write_text(output.getvalue(), encoding="utf-8")


def _new_upload_id() -> str:
    """Return a unique runtime upload folder name."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"api_{timestamp}_{uuid.uuid4().hex[:8]}"


def _slug(value: str) -> str:
    """Return a filesystem and manifest safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
    return slug or "api_contract_upload"

