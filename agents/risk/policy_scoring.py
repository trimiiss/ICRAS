"""Policy and playbook risk scoring rules."""

import re
from typing import Any, Mapping, Sequence

from schemas.common import Severity
from schemas.extracted_clause import ExtractedClause
from schemas.risk_result import ClauseRisk
from agents.risk.constants import CLAUSE_ALIASES, STANDARD_PAYMENT_DAYS
from agents.risk.helpers import (
    _aliases_for_clause_type,
    _find_clauses,
    _first_clause,
    _minor_variance_severity,
    _risk_tolerance_thresholds,
    _severity_from_value,
)
from agents.risk.results import _make_clause_risk
from utils.mapping import as_mapping as _as_mapping
from utils.payment_terms import extract_payment_days as _extract_payment_days
from utils.text import optional_int as _optional_int


def _score_payment_terms(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Score payment terms against the approved policy standard."""
    standard_days = _approved_payment_days(context)
    for clause in _find_clauses(clauses, CLAUSE_ALIASES["payment_terms"]):
        for days in _extract_payment_days(clause.text):
            if days <= standard_days:
                continue
            clause_risks.append(
                _make_clause_risk(
                    field_name="payment_terms",
                    issue_type="payment_terms_exceed_standard",
                    severity=Severity.HIGH,
                    explanation=(
                        f"Payment terms are net-{days}, which exceeds the "
                        f"approved net-{standard_days} standard."
                    ),
                    action=(
                        f"Revise payment terms to net-{standard_days} or obtain "
                        "legal and business approval for the extended payment cycle."
                    ),
                    context=context,
                    clause=clause,
                    tolerance_threshold=f"payment_terms_days_standard={standard_days}",
                )
            )


def _approved_payment_days(context: Mapping[str, Any]) -> int:
    """Return the longest approved net payment term from policy."""
    policy = _as_mapping(context.get("approval_policy"))
    approved_payment_terms = _as_mapping(policy.get("approved_payment_terms"))
    raw_terms = approved_payment_terms.get("terms")
    if not isinstance(raw_terms, Sequence) or isinstance(raw_terms, (str, bytes)):
        return STANDARD_PAYMENT_DAYS

    approved_days: list[int] = []
    for raw_term in raw_terms:
        approved_days.extend(_extract_payment_days(str(raw_term)))
    return max(approved_days) if approved_days else STANDARD_PAYMENT_DAYS


def _score_auto_renewal(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    approval_policy: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Detect auto-renewal clauses without opt-out protection."""
    renewal_policy = _as_mapping(approval_policy.get("auto_renewal_rules"))
    auto_renewal_allowed = bool(renewal_policy.get("allowed", False))
    minimum_notice = _optional_int(renewal_policy.get("minimum_notice_days")) or 30
    severity = _severity_from_value(
        renewal_policy.get("severity_if_unapproved"),
        Severity.HIGH,
    )
    for clause in _find_clauses(clauses, CLAUSE_ALIASES["auto_renewal"]):
        text = clause.text.lower()
        if "does not auto-renew" in text or "will not auto-renew" in text:
            continue
        if not ("auto-renew" in text or "automatic renewal" in text or "automatically renew" in text):
            continue
        has_opt_out = bool(
            re.search(r"\b(opt[- ]?out|non[- ]?renew|terminate)\b", text)
            or re.search(r"\b\d+\s+days?\s+(?:prior|before|advance)\b", text)
        )
        if auto_renewal_allowed and has_opt_out:
            continue
        issue_type = "auto_renewal_without_opt_out" if not has_opt_out else "auto_renewal_policy_violation"
        clause_risks.append(
            _make_clause_risk(
                field_name="auto_renewal",
                issue_type=issue_type,
                severity=Severity.HIGH if issue_type == "auto_renewal_without_opt_out" else severity,
                explanation=(
                    "The contract auto-renews without a clear opt-out or "
                    f"{minimum_notice}-day notice protection."
                ),
                action=(
                    "Add an opt-out right with advance notice or remove the "
                    "auto-renewal provision."
                ),
                context=context,
                clause=clause,
                tolerance_threshold=f"minimum_notice_days={minimum_notice}",
            )
        )


def _score_gdpr_requirements(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    approval_policy: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Score missing GDPR language where privacy terms indicate it is needed."""
    gdpr_policy = _as_mapping(approval_policy.get("gdpr_requirements"))
    if not bool(gdpr_policy.get("applies_when_personal_data", True)):
        return

    combined_text = " ".join(clause.text for clause in clauses).lower()
    privacy_applies = any(
        token in combined_text
        for token in ("personal data", "privacy", "data processing", "data protection")
    )
    if not privacy_applies or "gdpr" in combined_text:
        return

    data_clause = _first_clause(clauses, CLAUSE_ALIASES["data_protection"])
    clause_risks.append(
        _make_clause_risk(
            field_name="data_protection",
            issue_type="missing_gdpr_clause",
            severity=Severity.HIGH,
            explanation=(
                "The contract addresses privacy or personal data but does not "
                "include a GDPR clause."
            ),
            action=(
                "Add GDPR-compliant data processing terms or document why GDPR "
                "does not apply."
            ),
            context=context,
            clause=data_clause,
            tolerance_threshold="gdpr_required_when_personal_data=true",
        )
    )


def _score_playbook_variance(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    playbook: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Score missing required playbook clauses using variance tolerances."""
    required_clauses = [
        item for item in playbook.get("required_clauses", []) if isinstance(item, Mapping)
    ]
    if not required_clauses:
        return

    missing_required = [
        item
        for item in required_clauses
        if not _first_clause(clauses, _aliases_for_clause_type(str(item.get("clause_type", ""))))
    ]
    if not missing_required:
        return

    thresholds = _risk_tolerance_thresholds(context)
    missing_ratio = len(missing_required) / len(required_clauses)
    is_material = missing_ratio >= thresholds["material_missing_required_ratio"]

    for item in missing_required:
        clause_type = str(item.get("clause_type", "required_clause"))
        configured_severity = _severity_from_value(
            item.get("severity_if_missing"),
            Severity.MEDIUM,
        )
        severity = configured_severity if is_material else _minor_variance_severity(configured_severity)
        if severity == Severity.LOW and configured_severity in {Severity.LOW, Severity.MEDIUM}:
            continue
        clause_risks.append(
            _make_clause_risk(
                field_name=clause_type,
                issue_type=(
                    "material_variance_from_playbook"
                    if is_material
                    else "minor_variance_from_playbook"
                ),
                severity=severity,
                explanation=(
                    f"Required playbook clause '{clause_type}' was not detected. "
                    f"Missing required-clause ratio is {missing_ratio:.0%}."
                ),
                action=(
                    str(item.get("description") or "Add the required playbook clause.")
                ),
                context=context,
                clause=None,
                clause_text=f"Missing required playbook clause: {clause_type}",
                tolerance_threshold=(
                    "material_missing_required_ratio="
                    f"{thresholds['material_missing_required_ratio']:.0%}"
                ),
            )
        )


def _score_prohibited_clauses(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    playbook: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Score prohibited or non-standard clauses from the playbook."""
    prohibited = [
        item for item in playbook.get("prohibited_clauses", []) if isinstance(item, Mapping)
    ]
    for item in prohibited:
        clause_type = str(item.get("clause_type", "prohibited_clause"))
        clause = _first_clause(clauses, _aliases_for_clause_type(clause_type))
        if clause is None:
            continue
        clause_risks.append(
            _make_clause_risk(
                field_name=clause_type,
                issue_type="non_standard_prohibited_clause",
                severity=_severity_from_value(item.get("severity_if_present"), Severity.HIGH),
                explanation=(
                    f"Prohibited playbook clause '{clause_type}' was detected."
                ),
                action=str(item.get("description") or "Remove or revise the prohibited clause."),
                context=context,
                clause=clause,
                tolerance_threshold="prohibited_clause_present",
            )
        )
