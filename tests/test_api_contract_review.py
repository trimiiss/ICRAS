"""Tests for the contract review API."""

from pathlib import Path
from typing import Any
import importlib

import yaml
from fastapi.testclient import TestClient

api_app_module = importlib.import_module("api.app")


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _fake_pipeline_result(bundle_path: str) -> dict[str, Any]:
    """Return a pipeline response for API tests."""
    return {
        "run_id": "api-run-001",
        "metrics": {"status": "completed"},
        "approval_packet": {"decision": {"status": "AUTO_APPROVE"}},
        "artifact_paths": {
            "approval_packet": str(Path(bundle_path) / "approval_packet.json"),
            "final_findings": str(Path(bundle_path) / "final_findings.json"),
        },
    }


def test_pdf_upload_creates_bundle_and_returns_run_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A PDF upload should create a bundle and start the pipeline."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, str] = {}

    def fake_run_pipeline(bundle_path: str) -> dict[str, Any]:
        captured["bundle_path"] = bundle_path
        return _fake_pipeline_result(bundle_path)

    monkeypatch.setattr(api_app_module, "run_pipeline", fake_run_pipeline)
    client = TestClient(api_app_module.app)

    response = client.post(
        "/contracts/review",
        files={
            "contract_file": (
                "msa.pdf",
                PDF_BYTES,
                "application/pdf",
            )
        },
        data={
            "contract_type": "Master Services Agreement",
            "counterparty": "Acme Corporation",
            "jurisdiction": "Delaware, USA",
            "effective_date": "2025-01-15",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "api-run-001"
    assert body["status"] == "completed"
    assert body["approval_status"] == "AUTO_APPROVE"
    assert "approval_packet" in body["artifact_paths"]
    assert captured["bundle_path"] == body["bundle_path"]

    bundle_path = Path(body["bundle_path"])
    assert bundle_path.is_dir()
    assert bundle_path.is_relative_to(tmp_path)
    assert (bundle_path / "contract.pdf").read_bytes() == PDF_BYTES
    assert (bundle_path / "vendor_master.csv").is_file()
    assert (bundle_path / "playbook.yaml").is_file()
    assert (bundle_path / "approval_policy.yaml").is_file()
    assert (bundle_path / "jurisdiction_rules.yaml").is_file()
    manifest = yaml.safe_load((bundle_path / "manifest.yaml").read_text())
    assert manifest["contract_file"] == "contract.pdf"
    assert manifest["counterparty"] == "Acme Corporation"
    assert manifest["effective_date"] == "2025-01-15"


def test_optional_supporting_file_overrides_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Supported uploaded bundle files should replace generated defaults."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, str] = {}

    def fake_run_pipeline(bundle_path: str) -> dict[str, Any]:
        captured["bundle_path"] = bundle_path
        return _fake_pipeline_result(bundle_path)

    monkeypatch.setattr(api_app_module, "run_pipeline", fake_run_pipeline)
    client = TestClient(api_app_module.app)
    vendor_csv = (
        "vendor_id,vendor_name,contact_email,country,risk_tier,active,annual_spend_usd\n"
        "V-123,Custom Vendor,legal@example.com,USA,medium,true,1000\n"
    )

    response = client.post(
        "/contracts/review",
        files=[
            ("contract_file", ("contract.pdf", PDF_BYTES, "application/pdf")),
            ("supporting_files", ("vendor_master.csv", vendor_csv, "text/csv")),
        ],
        data={"counterparty": "Custom Vendor"},
    )

    assert response.status_code == 200
    bundle_path = Path(captured["bundle_path"])
    assert "Custom Vendor" in (bundle_path / "vendor_master.csv").read_text()
    assert "legal@example.com" in (bundle_path / "vendor_master.csv").read_text()


def test_invalid_contract_file_type_returns_clear_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Non-PDF contract uploads should return a clear 400 error."""
    monkeypatch.chdir(tmp_path)
    client = TestClient(api_app_module.app)

    response = client.post(
        "/contracts/review",
        files={
            "contract_file": (
                "contract.txt",
                b"not a pdf",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400
    assert "contract_file must be a PDF" in response.json()["detail"]


def test_invalid_supporting_file_type_returns_clear_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Unsupported supporting file names should return a clear 400 error."""
    monkeypatch.chdir(tmp_path)
    client = TestClient(api_app_module.app)

    response = client.post(
        "/contracts/review",
        files=[
            ("contract_file", ("contract.pdf", PDF_BYTES, "application/pdf")),
            ("supporting_files", ("notes.txt", b"notes", "text/plain")),
        ],
    )

    assert response.status_code == 400
    assert "Unsupported supporting file" in response.json()["detail"]
