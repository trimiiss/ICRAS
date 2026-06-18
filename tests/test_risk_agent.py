"""Tests for risk assessment."""

import json
from pathlib import Path

from agents.risk import run_risk_assessment


def _run_dir(tmp_path: Path, run_id: str = "risk-run") -> Path:
    """Create a run directory with audit files."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _evidence(page_number: int = 1, excerpt: str = "Clause text") -> dict:
    """Return a valid evidence pointer."""
    return {
        "source_file": "contract.pdf",
        "page_number": page_number,
        "excerpt": excerpt,
    }


def _clause(
    clause_type: str,
    text: str,
    page_number: int = 1,
    **overrides: object,
) -> dict:
    """Return an extracted clause dictionary."""
    base = {
        "clause_type": clause_type,
        "title": clause_type.replace("_", " ").title(),
        "text": text,
        "page_number": page_number,
        "confidence": 0.95,
        "evidence": _evidence(page_number, text),
        "evidence_pointer": _evidence(page_number, text),
    }
    base.update(overrides)
    return base


def _context(**overrides: object) -> dict:
    """Return risk-scoring context."""
    base = {
        "run_id": "risk-run",
        "contract_type": "Master Services Agreement",
        "counterparty": "Acme Corporation",
        "jurisdiction": "New York, USA",
        "contract_file": "contract.pdf",
        "playbook": {
            "required_clauses": [
                {
                    "clause_type": "payment_terms",
                    "description": "Must specify payment terms.",
                    "severity_if_missing": "HIGH",
                },
                {
                    "clause_type": "limitation_of_liability",
                    "description": "Must cap liability.",
                    "severity_if_missing": "HIGH",
                },
                {
                    "clause_type": "data_protection",
                    "description": "Must address data protection.",
                    "severity_if_missing": "HIGH",
                },
            ],
            "prohibited_clauses": [
                {
                    "clause_type": "non_compete",
                    "description": "Remove non-compete language.",
                    "severity_if_present": "HIGH",
                }
            ],
        },
        "approval_policy": {
            "approved_payment_terms": {
                "terms": ["net-30"],
                "severity_if_unapproved": "HIGH",
            },
            "auto_renewal_rules": {
                "allowed": False,
                "minimum_notice_days": 30,
                "severity_if_unapproved": "MEDIUM",
            },
            "high_risk_jurisdictions": ["Russia", "Iran", "North Korea", "Syria"],
            "gdpr_requirements": {
                "applies_when_personal_data": True,
                "required_clauses": ["data_processing_terms"],
                "severity_if_missing": "HIGH",
            },
            "risk_tolerance_thresholds": {
                "minor_missing_required_ratio": 0.25,
                "material_missing_required_ratio": 0.50,
            },
        },
    }
    base.update(overrides)
    return base


def _validation_finding(
    field_name: str,
    issue_type: str,
    severity: str = "HIGH",
    title: str = "Validation finding",
) -> dict:
    """Return a validation finding ready for risk assessment."""
    evidence = _evidence(4, title)
    return {
        "finding_id": "VAL-001",
        "category": "contract_validation",
        "title": title,
        "description": title,
        "severity": severity,
        "confidence": 1.0,
        "evidence": [evidence],
        "recommendation": "Resolve before approval.",
        "field_name": field_name,
        "issue_type": issue_type,
        "message": title,
        "source_clause_text": title,
        "source_page": 4,
        "evidence_pointer": evidence,
        "manual_review_required": True,
        "risk_engine_ready": True,
    }


def test_standard_clauses_create_minimal_noise(tmp_path: Path) -> None:
    """Standard clauses should not produce risk findings."""
    run_dir = _run_dir(tmp_path)

    result = run_risk_assessment(
        context=_context(
            playbook={
                "required_clauses": [
                    {"clause_type": "payment_terms", "severity_if_missing": "HIGH"},
                    {
                        "clause_type": "limitation_of_liability",
                        "severity_if_missing": "HIGH",
                    },
                    {"clause_type": "data_protection", "severity_if_missing": "HIGH"},
                ],
                "prohibited_clauses": [],
            }
        ),
        extracted_contract={
            "run_id": "risk-run",
            "clauses": [
                _clause("payment_terms", "Invoices are payable net 30.", 1),
                _clause(
                    "liability_cap",
                    "Each party's liability is capped at fees paid in 12 months.",
                    2,
                ),
                _clause(
                    "data_protection",
                    "The parties will comply with GDPR and privacy law.",
                    3,
                ),
                _clause(
                    "governing_law",
                    "This Agreement is governed by New York law.",
                    4,
                ),
            ],
        },
        validation_result={"findings": []},
        run_dir=run_dir,
    )

    assert result["clause_analysis"]["overall_severity"] == "LOW"
    assert result["clause_analysis"]["clause_risks"] == []
    assert (run_dir / "clause_analysis.json").is_file()


def test_policy_deviations_create_risk_findings(tmp_path: Path) -> None:
    """High-risk rules should become source-backed legal-review risks."""
    run_dir = _run_dir(tmp_path)
    result = run_risk_assessment(
        context=_context(jurisdiction="Syria"),
        extracted_contract={
            "run_id": "risk-run",
            "clauses": [
                _clause(
                    "parties",
                    "This Agreement is among Alpha LLC, Beta Inc, and Gamma GmbH.",
                    1,
                ),
                _clause(
                    "payment_terms",
                    "Customer shall pay invoices on net 90 terms.",
                    2,
                ),
                _clause(
                    "governing_law",
                    "This Agreement is governed by New York law.",
                    3,
                ),
                _clause(
                    "governing_law",
                    "Disputes are also subject to Delaware law.",
                    4,
                ),
                _clause(
                    "auto_renewal",
                    "The Agreement will automatically renew each year.",
                    5,
                ),
                _clause(
                    "data_protection",
                    "Supplier may process personal data and privacy records.",
                    6,
                ),
                _clause("signature", "Signed by Alpha LLC and Beta Inc.", 7),
            ],
        },
        validation_result={
            "findings": [
                _validation_finding(
                    field_name="liability_cap",
                    issue_type="missing_field",
                    title="Missing liability cap",
                ),
                _validation_finding(
                    field_name="governing_law",
                    issue_type="conflicting_governing_law",
                    title="Conflicting governing law clauses",
                ),
            ]
        },
        run_dir=run_dir,
    )

    clause_analysis = result["clause_analysis"]
    issue_types = {
        risk["issue_type"] for risk in clause_analysis["clause_risks"]
    }
    assert "payment_terms_exceed_standard" in issue_types
    assert "missing_liability_cap" in issue_types
    assert "high_risk_jurisdiction" in issue_types
    assert "auto_renewal_without_opt_out" in issue_types
    assert "missing_gdpr_clause" in issue_types
    assert "conflicting_governing_law" in issue_types
    assert "multi_jurisdiction_conflict" in issue_types
    assert "multi_party_agreement_gap" in issue_types
    assert clause_analysis["overall_severity"] == "CRITICAL"

    high_risks = [
        risk
        for risk in clause_analysis["clause_risks"]
        if risk["severity"] in {"HIGH", "CRITICAL"}
    ]
    assert high_risks
    for risk in high_risks:
        assert risk["legal_review_required"] is True
        assert risk["clause_text"]
        assert risk["evidence_pointer"]["source_file"] == "contract.pdf"
        assert risk["recommended_action"]


def test_playbook_tolerance_thresholds_classify_material_variance(
    tmp_path: Path,
) -> None:
    """Missing playbook standards use tolerance thresholds for severity."""
    run_dir = _run_dir(tmp_path)
    result = run_risk_assessment(
        context=_context(
            playbook={
                "required_clauses": [
                    {"clause_type": "payment_terms", "severity_if_missing": "HIGH"},
                    {
                        "clause_type": "limitation_of_liability",
                        "severity_if_missing": "HIGH",
                    },
                    {"clause_type": "data_protection", "severity_if_missing": "HIGH"},
                    {
                        "clause_type": "intellectual_property",
                        "severity_if_missing": "HIGH",
                    },
                ],
                "prohibited_clauses": [],
            }
        ),
        extracted_contract={
            "run_id": "risk-run",
            "clauses": [
                _clause("payment_terms", "Invoices are payable net 30.", 1),
                _clause(
                    "data_protection",
                    "The parties comply with GDPR for personal data.",
                    2,
                ),
            ],
        },
        validation_result={"findings": []},
        run_dir=run_dir,
    )

    material_risks = [
        risk
        for risk in result["clause_analysis"]["clause_risks"]
        if risk["issue_type"] == "material_variance_from_playbook"
    ]
    assert material_risks
    assert all(risk["severity"] == "HIGH" for risk in material_risks)
    assert all("material_missing_required_ratio=50%" in risk["tolerance_threshold"] for risk in material_risks)


def test_reads_run_artifacts_and_generates_clause_analysis(tmp_path: Path) -> None:
    """Risk assessment can read prior artifacts directly from the run directory."""
    run_dir = _run_dir(tmp_path)
    (run_dir / "context_packet.json").write_text(
        json.dumps(_context()),
        encoding="utf-8",
    )
    (run_dir / "extracted_contract.json").write_text(
        json.dumps(
            {
                "run_id": "risk-run",
                "document_id": "DOC-001",
                "source_file": "contract.pdf",
                "clauses": [_clause("payment_terms", "Invoices are payable net 90.", 2)],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "validation_findings.json").write_text(
        json.dumps({"run_id": "risk-run", "findings": []}),
        encoding="utf-8",
    )

    result = run_risk_assessment(run_dir=run_dir)

    assert result["artifact_paths"]["clause_analysis"] == str(
        run_dir / "clause_analysis.json"
    )
    assert any(
        risk["issue_type"] == "payment_terms_exceed_standard"
        for risk in result["clause_analysis"]["clause_risks"]
    )


def test_payment_terms_standard_uses_approval_policy(tmp_path: Path) -> None:
    """Risk assessment should honor edited approved payment terms from policy."""
    run_dir = _run_dir(tmp_path)
    context = _context()
    context["approval_policy"]["approved_payment_terms"]["terms"] = ["net-60"]

    result = run_risk_assessment(
        context=context,
        extracted_contract={
            "run_id": "risk-run",
            "clauses": [
                _clause("payment_terms", "Invoices are payable net 60.", 1),
                _clause(
                    "liability_cap",
                    "Each party's liability is capped at fees paid in 12 months.",
                    2,
                ),
                _clause(
                    "data_protection",
                    "The parties will comply with GDPR and privacy law.",
                    3,
                ),
            ],
        },
        validation_result={"findings": []},
        run_dir=run_dir,
    )

    issue_types = {
        risk["issue_type"]
        for risk in result["clause_analysis"]["clause_risks"]
    }
    assert "payment_terms_exceed_standard" not in issue_types
