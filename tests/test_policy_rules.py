"""Tests for YAML-configurable policy rules."""

import shutil
from pathlib import Path

import pytest

from agents.orchestrator_agent import run_pipeline
from agents.validation_agent import run_validation
from utils.bundle_loader import load_bundle


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "services_agreement"
POLICY_DEMO_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "net60_policy_demo"


def _context_from_bundle(bundle_data: dict) -> dict:
    """Build validation context from freshly loaded bundle data."""
    manifest = bundle_data["manifest"]
    return {
        "run_id": "policy-demo-run",
        "contract_type": manifest["contract_type"],
        "counterparty": manifest["counterparty"],
        "jurisdiction": manifest["jurisdiction"],
        "effective_date": manifest["effective_date"],
        "contract_file": manifest["contract_file"],
        "playbook": bundle_data["playbook"],
        "approval_policy": bundle_data["approval_policy"],
    }


def _net_60_contract_clauses() -> list[dict]:
    """Return clauses for a services contract with net-60 payment terms."""
    return [
        {
            "clause_type": "payment_terms",
            "title": "Payment Terms",
            "text": "Customer shall pay valid invoices on net 60 payment terms.",
            "confidence": 0.95,
        },
        {
            "clause_type": "termination",
            "title": "Termination",
            "text": "Either party may terminate on 30 days' written notice.",
            "confidence": 0.95,
        },
    ]


def test_policy_yaml_edit_changes_payment_terms_decision(tmp_path: Path) -> None:
    """Changing net-30 to net-60 takes effect on the next bundle load."""
    bundle_copy = tmp_path / "services_policy_demo"
    shutil.copytree(SA_BUNDLE, bundle_copy)

    bundle_data = load_bundle(bundle_copy)
    net_30_result = run_validation(
        context=_context_from_bundle(bundle_data),
        clauses=_net_60_contract_clauses(),
    )
    assert any(
        finding["title"] == "Unapproved payment terms"
        for finding in net_30_result["findings"]
    )

    policy_path = bundle_copy / "approval_policy.yaml"
    policy_text = policy_path.read_text(encoding="utf-8")
    policy_path.write_text(
        policy_text.replace("- net-30", "- net-60"),
        encoding="utf-8",
    )

    reloaded_bundle_data = load_bundle(bundle_copy)
    net_60_result = run_validation(
        context=_context_from_bundle(reloaded_bundle_data),
        clauses=_net_60_contract_clauses(),
    )
    assert all(
        finding["title"] != "Unapproved payment terms"
        for finding in net_60_result["findings"]
    )


def test_full_pipeline_policy_edit_changes_demo_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing net-30 to net-60 should change the full demo decision."""
    monkeypatch.chdir(tmp_path)
    bundle_copy = tmp_path / "net60_policy_demo"
    shutil.copytree(POLICY_DEMO_BUNDLE, bundle_copy)

    net_30_result = run_pipeline(str(bundle_copy))
    assert net_30_result["approval_packet"]["decision"]["status"] == "ESCALATE"
    assert any(
        route["category"] == "FINANCE"
        for route in net_30_result["approval_packet"]["approval_route"]
    )

    policy_path = bundle_copy / "approval_policy.yaml"
    policy_path.write_text(
        policy_path.read_text(encoding="utf-8").replace("net-30", "net-60"),
        encoding="utf-8",
    )

    net_60_result = run_pipeline(str(bundle_copy))
    assert net_60_result["approval_packet"]["decision"]["status"] == "AUTO_APPROVE"
    assert net_60_result["approval_packet"]["approval_route"][0]["category"] == (
        "AUTO_APPROVE"
    )
