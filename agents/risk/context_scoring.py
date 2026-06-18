"""Context and validation-derived risk scoring rules."""

from typing import Any, Mapping, Sequence

from schemas.common import Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.risk_result import ClauseRisk
from agents.risk.constants import CLAUSE_ALIASES
from agents.risk.helpers import (
    _as_mapping,
    _contains_word,
    _extract_jurisdictions,
    _extract_party_names,
    _find_clauses,
    _first_clause,
    _is_non_empty,
    _primary_evidence,
)
from agents.risk.results import _make_clause_risk


def _score_validation_findings(
    validation_findings: Sequence[Finding],
    context: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Promote risk-engine-ready validation issues into clause risks."""
    mapped_issue_types = {
        "missing_field": "missing_liability_cap",
        "conflicting_governing_law": "conflicting_governing_law",
        "payment_terms_policy_violation": "payment_terms_policy_violation",
        "suspicious_date_ordering": "suspicious_date_ordering",
        "calculation_error": "calculation_error",
        "low_confidence_signature": "low_confidence_signature",
        "multi_party_signature_missing": "multi_party_agreement_gap",
        "multi_party_signature_incomplete": "multi_party_agreement_gap",
    }
    for finding in validation_findings:
        if finding.issue_type == "missing_field" and finding.field_name != "liability_cap":
            continue
        if finding.issue_type not in mapped_issue_types:
            continue
        severity = finding.severity
        if finding.issue_type == "missing_field" and finding.field_name == "liability_cap":
            severity = Severity.HIGH
        clause_risks.append(
            _make_clause_risk(
                field_name=finding.field_name or "validation_finding",
                issue_type=mapped_issue_types[finding.issue_type],
                severity=severity,
                explanation=finding.message or finding.description,
                action=finding.recommendation
                or "Resolve the validation issue before approval routing.",
                context=context,
                clause=None,
                evidence=finding.evidence_pointer or _primary_evidence(finding.evidence),
                clause_text=finding.source_clause_text,
                tolerance_threshold="validation_finding_escalation",
            )
        )


def _score_high_risk_jurisdiction(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    clause_risks: list[ClauseRisk],
) -> None:
    """Escalate jurisdictions marked high risk by policy."""
    policy = _as_mapping(context.get("approval_policy"))
    high_risk_jurisdictions = [
        str(value)
        for value in policy.get("high_risk_jurisdictions", [])
        if _is_non_empty(value)
    ]
    if not high_risk_jurisdictions:
        return

    text_sources = [str(context.get("jurisdiction") or "")]
    text_sources.extend(clause.text for clause in _find_clauses(clauses, CLAUSE_ALIASES["governing_law"]))
    matched = [
        jurisdiction
        for jurisdiction in high_risk_jurisdictions
        if any(_contains_word(source, jurisdiction) for source in text_sources)
    ]
    if not matched:
        return

    clause = _first_clause(clauses, CLAUSE_ALIASES["governing_law"])
    clause_risks.append(
        _make_clause_risk(
            field_name="governing_law",
            issue_type="high_risk_jurisdiction",
            severity=Severity.CRITICAL,
            explanation=(
                "The contract references a high-risk jurisdiction: "
                + ", ".join(sorted(set(matched)))
                + "."
            ),
            action=(
                "Route to legal and compliance for sanctions, enforceability, "
                "and cross-border risk review."
            ),
            context=context,
            clause=clause,
            clause_text=", ".join(sorted(set(matched))),
            tolerance_threshold="approval_policy.high_risk_jurisdictions",
        )
    )


def _score_multi_jurisdiction(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Detect conflicting governing-law jurisdictions."""
    jurisdictions = _extract_jurisdictions(str(context.get("jurisdiction") or ""))
    governing_clauses = _find_clauses(clauses, CLAUSE_ALIASES["governing_law"])
    for clause in governing_clauses:
        jurisdictions.extend(_extract_jurisdictions(clause.text))
    unique = sorted({jurisdiction.lower(): jurisdiction for jurisdiction in jurisdictions}.values())
    if len(unique) <= 1:
        return

    policy = _as_mapping(context.get("approval_policy"))
    high_risk = [
        str(value).lower()
        for value in policy.get("high_risk_jurisdictions", [])
        if _is_non_empty(value)
    ]
    severity = (
        Severity.CRITICAL
        if any(jurisdiction.lower() in high_risk for jurisdiction in unique)
        else Severity.HIGH
    )
    clause = governing_clauses[0] if governing_clauses else None
    clause_risks.append(
        _make_clause_risk(
            field_name="governing_law",
            issue_type="multi_jurisdiction_conflict",
            severity=severity,
            explanation=(
                "Multiple jurisdictions appear in governing-law or context data: "
                + ", ".join(unique)
                + "."
            ),
            action=(
                "Reconcile governing law, forum, and cross-border provisions "
                "before approval."
            ),
            context=context,
            clause=clause,
            clause_text="; ".join(clause.text for clause in governing_clauses) or ", ".join(unique),
            tolerance_threshold="single_governing_law_expected",
        )
    )


def _score_multi_party(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    validation_findings: Sequence[Finding],
    clause_risks: list[ClauseRisk],
) -> None:
    """Detect multi-party agreements with incomplete signature coverage."""
    if any(
        finding.issue_type in {
            "multi_party_signature_missing",
            "multi_party_signature_incomplete",
        }
        for finding in validation_findings
    ):
        return
    parties = _extract_party_names(context, clauses)
    if len(parties) <= 2:
        return
    signature_clauses = _find_clauses(clauses, CLAUSE_ALIASES["signature"])
    signature_text = " ".join(clause.text for clause in signature_clauses).lower()
    missing = [party for party in parties if party.lower() not in signature_text]
    if signature_clauses and not missing:
        return
    clause_risks.append(
        _make_clause_risk(
            field_name="party_names",
            issue_type="multi_party_agreement_gap",
            severity=Severity.HIGH,
            explanation=(
                f"The contract appears to include {len(parties)} parties but "
                "does not show complete signature coverage."
            ),
            action="Confirm all parties are identified and have signature blocks.",
            context=context,
            clause=signature_clauses[0] if signature_clauses else _first_clause(clauses, CLAUSE_ALIASES["party_names"]),
            tolerance_threshold="all_parties_require_signature_coverage",
        )
    )
