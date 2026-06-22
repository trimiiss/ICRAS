"""Tests for the Streamlit demo helper layer."""

import shutil
from pathlib import Path

from streamlit_app import (
    build_approval_rows,
    build_obligation_rows,
    build_risk_rows,
    discover_bundles,
    display_status,
    ordered_artifact_items,
    read_audit_log_info,
)


TEST_WORKSPACE = Path("runs") / "test_streamlit_app"


def _workspace(name: str) -> Path:
    """Return a clean test workspace folder under runs/."""
    path = TEST_WORKSPACE / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_discover_bundles_returns_valid_bundle_dirs() -> None:
    """Only folders with a manifest and contract PDF should be selectable."""
    workspace = _workspace("bundles")
    valid_bundle = workspace / "clean_nda"
    valid_bundle.mkdir()
    (valid_bundle / "manifest.yaml").write_text("bundle_name: clean_nda\n")
    (valid_bundle / "contract.pdf").write_bytes(b"%PDF-1.4\n")

    invalid_bundle = workspace / "notes_only"
    invalid_bundle.mkdir()
    (invalid_bundle / "manifest.yaml").write_text("bundle_name: notes_only\n")

    assert discover_bundles(workspace) == [valid_bundle]


def test_build_approval_and_risk_rows_for_display() -> None:
    """Routing and risk rows should expose presenter-friendly fields."""
    approval_rows = build_approval_rows(
        {
            "approval_route": [
                {
                    "category": "LEGAL",
                    "approvers": ["legal_counsel"],
                    "reason": "Liability cap review required.",
                    "finding_ids": ["F-001"],
                }
            ]
        }
    )
    risk_rows = build_risk_rows(
        {
            "final_findings": {
                "findings": [
                    {
                        "severity": "HIGH",
                        "category": "legal",
                        "title": "Missing liability cap",
                        "confidence": 0.95,
                        "recommendation": "Request legal approval.",
                        "evidence": [
                            {
                                "source_file": "contract.pdf",
                                "page_number": 2,
                                "clause_reference": "8",
                            }
                        ],
                    }
                ]
            }
        }
    )

    assert approval_rows == [
        {
            "category": "LEGAL",
            "approvers": "legal_counsel",
            "reason": "Liability cap review required.",
            "findings": "F-001",
        }
    ]
    assert risk_rows[0]["confidence"] == "0.95"
    assert risk_rows[0]["evidence"] == "contract.pdf page 2 clause 8"


def test_audit_info_and_artifact_ordering() -> None:
    """Artifact helpers should read audit logs and prioritize key outputs."""
    workspace = _workspace("artifacts")
    approval_path = workspace / "approval_packet.json"
    metrics_path = workspace / "metrics.json"
    audit_path = workspace / "audit_log.md"
    audit_jsonl_path = workspace / "audit_log.jsonl"
    approval_path.write_text("{}\n", encoding="utf-8")
    metrics_path.write_text("{}\n", encoding="utf-8")
    audit_path.write_text("# Audit\n", encoding="utf-8")
    audit_jsonl_path.write_text("{}\n{}\n", encoding="utf-8")

    artifact_paths = {
        "metrics": str(metrics_path),
        "approval_packet": str(approval_path),
        "audit_log": str(audit_path),
        "audit_log_jsonl": str(audit_jsonl_path),
    }

    ordered_names = [name for name, _path in ordered_artifact_items(artifact_paths)]
    audit_info = read_audit_log_info(artifact_paths)

    assert ordered_names[:2] == ["approval_packet", "metrics"]
    assert audit_info["event_count"] == 2
    assert audit_info["markdown"] == "# Audit\n"
    assert display_status("AUTO_APPROVE") == "AUTO-APPROVE"


def test_build_obligation_rows_for_display() -> None:
    """Obligation rows should expose presenter-friendly fields from the CLM payload."""
    result = {
        "posting_payload": {
            "obligations": [
                {
                    "obligation_id": "OBL-001",
                    "obligation_type": "payment",
                    "responsible_party": "Customer",
                    "obligation_summary": "Customer must pay invoices net 30.",
                    "due_date": None,
                    "timing_trigger": "net 30",
                    "is_recurring": True,
                    "evidence_pointer": {"source_file": "contract.pdf"},
                }
            ]
        }
    }
    rows = build_obligation_rows(result)
    assert len(rows) == 1
    assert rows[0]["id"] == "OBL-001"
    assert rows[0]["type"] == "payment"
    assert rows[0]["party"] == "Customer"
    assert "invoices" in rows[0]["summary"]
    assert rows[0]["due / trigger"] == "net 30"
    assert rows[0]["recurring"] == "Yes"


def test_build_obligation_rows_empty_when_no_obligations() -> None:
    """An empty obligation list should produce no rows."""
    rows = build_obligation_rows({"posting_payload": {"obligations": []}})
    assert rows == []


def test_build_obligation_rows_empty_when_payload_missing() -> None:
    """Missing posting_payload should produce no rows gracefully."""
    rows = build_obligation_rows({})
    assert rows == []