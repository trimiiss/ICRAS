"""Validation helpers for policy and playbook configuration."""

from typing import Any, Mapping, Optional

from schemas.common import Severity
from agents.validation.constants import (
    CONTRACT_TYPE_PAYMENT_HINTS,
    DEFAULT_FIELD_SEVERITIES,
    FIELD_ALIASES,
)
from agents.validation.core_helpers import _get_context_value, _get_raw_context_value
from utils.payment_terms import (
    canonical_payment_term as _canonical_payment_term,
    extract_payment_terms as _extract_payment_terms,
)
from utils.text import normalize_key as _normalize_key, truncate as _truncate


def _payment_terms_applicable(context: Mapping[str, Any]) -> bool:
    """Return whether payment terms must be validated for this contract."""
    explicit = _get_raw_context_value(
        context,
        ("payment_terms_required", "requires_payment_terms", "payment_required"),
    )
    if isinstance(explicit, bool):
        return explicit

    playbook = context.get("playbook")
    if isinstance(playbook, Mapping):
        required_clauses = playbook.get("required_clauses")
        if isinstance(required_clauses, list):
            for required_clause in required_clauses:
                if not isinstance(required_clause, Mapping):
                    continue
                clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
                if "payment" in clause_type or "fee" in clause_type:
                    return True

    contract_type = _get_context_value(context, ("contract_type",)) or ""
    normalized_contract_type = contract_type.lower()
    return any(hint in normalized_contract_type for hint in CONTRACT_TYPE_PAYMENT_HINTS)


def _liability_cap_required(context: Mapping[str, Any]) -> bool:
    """Return whether policy or playbook requires a liability cap."""
    approval_policy = context.get("approval_policy")
    if isinstance(approval_policy, Mapping):
        requirements = approval_policy.get("liability_cap_requirements")
        if isinstance(requirements, Mapping):
            required = requirements.get("required")
            if isinstance(required, bool):
                return required

    playbook = context.get("playbook")
    if isinstance(playbook, Mapping):
        required_clauses = playbook.get("required_clauses")
        if isinstance(required_clauses, list):
            aliases = {
                _normalize_key(alias) for alias in FIELD_ALIASES["liability_cap"]
            }
            for required_clause in required_clauses:
                if not isinstance(required_clause, Mapping):
                    continue
                clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
                if clause_type in aliases or any(alias in clause_type for alias in aliases):
                    return True

    return False


def _field_severity(context: Mapping[str, Any], field_name: str) -> Severity:
    """Return the configured or default severity for a missing field."""
    if field_name == "liability_cap":
        policy_severity = _liability_cap_missing_severity(context)
        if policy_severity is not None:
            return policy_severity
    playbook_severity = _playbook_missing_severity(context, field_name)
    if playbook_severity is not None:
        return playbook_severity
    return DEFAULT_FIELD_SEVERITIES[field_name]


def _liability_cap_missing_severity(context: Mapping[str, Any]) -> Optional[Severity]:
    """Read liability-cap missing severity from approval policy."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return None
    requirements = approval_policy.get("liability_cap_requirements")
    if not isinstance(requirements, Mapping):
        return None
    raw_severity = str(requirements.get("severity_if_missing", "")).upper()
    try:
        return Severity(raw_severity)
    except ValueError:
        return None


def _playbook_missing_severity(
    context: Mapping[str, Any],
    field_name: str,
) -> Optional[Severity]:
    """Read severity_if_missing from matching playbook required clauses."""
    playbook = context.get("playbook")
    if not isinstance(playbook, Mapping):
        return None

    required_clauses = playbook.get("required_clauses")
    if not isinstance(required_clauses, list):
        return None

    aliases = {_normalize_key(alias) for alias in FIELD_ALIASES[field_name]}
    for required_clause in required_clauses:
        if not isinstance(required_clause, Mapping):
            continue
        clause_type = _normalize_key(str(required_clause.get("clause_type", "")))
        if clause_type not in aliases and not any(alias in clause_type for alias in aliases):
            continue
        raw_severity = str(required_clause.get("severity_if_missing", "")).upper()
        try:
            return Severity(raw_severity)
        except ValueError:
            continue

    return None


def _approved_payment_terms(context: Mapping[str, Any]) -> set[str]:
    """Return approved payment terms from approval_policy.yaml rules."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return {"net-30"}

    approved_payment_terms = approval_policy.get("approved_payment_terms")
    if not isinstance(approved_payment_terms, Mapping):
        return {"net-30"}

    terms = approved_payment_terms.get("terms")
    if not isinstance(terms, list):
        return {"net-30"}

    normalized_terms = {
        normalized
        for term in terms
        if (normalized := _canonical_payment_term(str(term))) is not None
    }
    return normalized_terms or {"net-30"}


def _payment_policy_severity(context: Mapping[str, Any]) -> Severity:
    """Return YAML-configured severity for unapproved payment terms."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return Severity.HIGH

    approved_payment_terms = approval_policy.get("approved_payment_terms")
    if not isinstance(approved_payment_terms, Mapping):
        return Severity.HIGH

    raw_severity = str(
        approved_payment_terms.get("severity_if_unapproved", Severity.HIGH.value)
    ).upper()
    try:
        return Severity(raw_severity)
    except ValueError:
        return Severity.HIGH


def _normalize_payment_terms_value(payment_text: str) -> str:
    """Return detected canonical payment terms or a compact clause excerpt."""
    detected_terms = _extract_payment_terms(payment_text)
    if detected_terms:
        return "; ".join(detected_terms)
    return _truncate(payment_text)


def _manual_review_confidence_threshold(context: Mapping[str, Any]) -> float:
    """Return the configured confidence threshold for manual review."""
    approval_policy = context.get("approval_policy")
    if not isinstance(approval_policy, Mapping):
        return 0.75
    threshold = approval_policy.get("manual_review_confidence_threshold", 0.75)
    try:
        numeric_threshold = float(threshold)
    except (TypeError, ValueError):
        return 0.75
    return min(max(numeric_threshold, 0.0), 1.0)
