"""Compliance Agent for GDPR and jurisdiction-specific checks."""

from pathlib import Path
from typing import Any, Mapping, Sequence

from schemas.common import EvidencePointer, Severity
from schemas.compliance_result import ComplianceResult
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from utils.artifacts import validate_run_dir, write_model_json
from utils.clauses import coerce_extracted_clauses
from utils.mapping import as_mapping as _as_mapping
from utils.text import (
    is_non_empty as _is_non_empty,
    normalize_key as _normalize_key,
    optional_int as _optional_int,
    optional_str as _optional_str,
    truncate as _truncate,
)
from utils.run_manager import append_audit_event


class ComplianceAgentError(Exception):
    """Raised when compliance review cannot complete."""


GDPR_PRIVACY_TOKENS = (
    "personal data",
    "privacy",
    "data processing",
    "data protection",
)

GDPR_OBLIGATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "data_processing_terms": (
        "data processing terms",
        "process personal data",
        "processing personal data",
        "processor",
        "controller",
    ),
    "cross_border_transfer_controls": (
        "cross-border transfer",
        "cross border transfer",
        "international transfer",
        "standard contractual clauses",
        "transfer controls",
    ),
    "data_subject_rights": (
        "data subject rights",
        "data subject",
        "access",
        "erasure",
        "rectification",
    ),
}


def run_compliance_review(
    context: Mapping[str, Any],
    extracted_contract: Mapping[str, Any],
    run_dir: str | Path | None = None,
    evidence_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run GDPR and jurisdiction-specific compliance checks."""
    run_path = (
        validate_run_dir(
            run_dir,
            error_type=ComplianceAgentError,
            before_action="running compliance review",
        )
        if run_dir is not None
        else None
    )
    clauses = _coerce_clauses(extracted_contract.get("clauses", []))
    evidence_records = _extract_evidence_records(evidence_index)
    run_id = str(
        context.get("run_id")
        or extracted_contract.get("run_id")
        or "unknown-run"
    )

    checked_rules = [
        "high_risk_jurisdiction",
        "gdpr_requirements",
        "jurisdiction_required_clauses",
    ]
    findings: list[Finding] = []
    _check_high_risk_jurisdiction(
        context=context,
        clauses=clauses,
        evidence_records=evidence_records,
        findings=findings,
    )
    _check_gdpr_requirements(
        context=context,
        clauses=clauses,
        evidence_records=evidence_records,
        findings=findings,
    )
    _check_jurisdiction_required_clauses(
        context=context,
        clauses=clauses,
        evidence_records=evidence_records,
        findings=findings,
    )

    result = ComplianceResult(
        run_id=run_id,
        findings=findings,
        checked_rules=checked_rules,
        requires_compliance_review=any(
            finding.manual_review_required
            or finding.severity in {Severity.HIGH, Severity.CRITICAL}
            for finding in findings
        ),
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "compliance_findings.json"
        write_model_json(
            output_path,
            result,
            error_type=ComplianceAgentError,
            failure_message="Failed to write compliance artifact '{path}': {exc}",
        )
        append_audit_event(
            run_path,
            {
                "event": "compliance_review_completed",
                "agent": "compliance_agent",
                "message": "Compliance Agent checked GDPR and jurisdiction rules.",
                "artifacts": [output_path.name],
                "finding_count": len(findings),
                "checked_rules": checked_rules,
                "requires_compliance_review": result.requires_compliance_review,
            },
        )
        artifact_paths["compliance_findings"] = str(output_path)

    serialized = result.model_dump(mode="json")
    return {
        "compliance_result": serialized,
        "findings": serialized["findings"],
        "artifact_paths": artifact_paths,
    }


def _check_high_risk_jurisdiction(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Create a compliance finding for high-risk jurisdictions."""
    policy = _as_mapping(context.get("approval_policy"))
    high_risk_values = [
        str(value)
        for value in policy.get("high_risk_jurisdictions", [])
        if _is_non_empty(value)
    ]
    jurisdiction_rules = _as_mapping(context.get("jurisdiction_rules"))
    if not high_risk_values and not _rule_marks_high_risk(jurisdiction_rules):
        return

    sources = _jurisdiction_sources(context, jurisdiction_rules, clauses)
    matched = [
        jurisdiction
        for jurisdiction in high_risk_values
        if any(_contains_word(source, jurisdiction) for source in sources)
    ]
    if not matched and not _rule_marks_high_risk(jurisdiction_rules):
        return

    matched_text = ", ".join(sorted(set(matched))) or str(
        jurisdiction_rules.get("jurisdiction") or context.get("jurisdiction") or "unknown"
    )
    evidence = _clause_or_fallback_evidence(
        context=context,
        clauses=_find_clauses(clauses, ("governing_law", "governing law", "jurisdiction")),
        evidence_records=evidence_records,
        excerpt=f"High-risk jurisdiction: {matched_text}",
    )
    findings.append(
        _make_finding(
            findings=findings,
            category="compliance",
            title="High-risk jurisdiction",
            description=f"The contract references a high-risk jurisdiction: {matched_text}.",
            severity=Severity.CRITICAL,
            confidence=1.0,
            evidence=evidence,
            recommendation=(
                "Route to Compliance for sanctions, enforceability, and "
                "cross-border jurisdiction review."
            ),
            field_name="governing_law",
            issue_type="high_risk_jurisdiction",
            source_clause_text=matched_text,
        )
    )


def _check_gdpr_requirements(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Create GDPR findings when personal-data language lacks required terms."""
    policy = _as_mapping(context.get("approval_policy"))
    gdpr_policy = _as_mapping(policy.get("gdpr_requirements"))
    if gdpr_policy.get("applies_when_personal_data", True) is False:
        return

    combined_text = " ".join(clause.text for clause in clauses).lower()
    privacy_applies = any(token in combined_text for token in GDPR_PRIVACY_TOKENS)
    jurisdiction_rules = _as_mapping(context.get("jurisdiction_rules"))
    privacy_applies = privacy_applies or bool(jurisdiction_rules.get("gdpr_applies"))
    if not privacy_applies:
        return

    severity = _severity_from_value(
        gdpr_policy.get("severity_if_missing"),
        Severity.CRITICAL,
    )
    data_clauses = _find_clauses(
        clauses,
        ("data_protection", "data protection", "privacy", "personal data"),
    )
    if "gdpr" not in combined_text:
        evidence = _clause_or_fallback_evidence(
            context=context,
            clauses=data_clauses,
            evidence_records=evidence_records,
            excerpt="Personal data terms were detected without GDPR obligations.",
        )
        findings.append(
            _make_finding(
                findings=findings,
                category="compliance",
                title="Missing GDPR clause",
                description=(
                    "The contract addresses privacy or personal data but does "
                    "not include a GDPR clause."
                ),
                severity=severity,
                confidence=1.0,
                evidence=evidence,
                recommendation=(
                    "Add GDPR-compliant data processing terms or document why "
                    "GDPR does not apply."
                ),
                field_name="data_protection",
                issue_type="missing_gdpr_clause",
                source_clause_text=evidence[0].excerpt,
            )
        )
        return

    required_obligations = gdpr_policy.get("required_clauses", [])
    if not isinstance(required_obligations, list):
        return
    for raw_obligation in required_obligations:
        obligation = str(raw_obligation).strip()
        if not obligation:
            continue
        if _gdpr_obligation_present(obligation, combined_text):
            continue
        evidence = _clause_or_fallback_evidence(
            context=context,
            clauses=data_clauses,
            evidence_records=evidence_records,
            excerpt=f"Missing GDPR obligation: {obligation}",
        )
        findings.append(
            _make_finding(
                findings=findings,
                category="compliance",
                title="Missing GDPR obligation",
                description=(
                    "The contract references GDPR but does not include the "
                    f"required obligation '{obligation}'."
                ),
                severity=severity,
                confidence=1.0,
                evidence=evidence,
                recommendation=f"Add GDPR language covering {obligation}.",
                field_name="data_protection",
                issue_type="missing_gdpr_obligation",
                source_clause_text=evidence[0].excerpt,
            )
        )


def _check_jurisdiction_required_clauses(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    """Create findings for jurisdiction-specific required compliance clauses."""
    jurisdiction_rules = _as_mapping(context.get("jurisdiction_rules"))
    required_clauses = _jurisdiction_required_clauses(jurisdiction_rules)
    for item in required_clauses:
        clause_type = str(item.get("clause_type") or item.get("name") or "").strip()
        if not clause_type:
            continue
        keywords = _required_clause_keywords(item, clause_type)
        if _find_clauses(clauses, keywords):
            continue
        severity = _severity_from_value(item.get("severity_if_missing"), Severity.HIGH)
        evidence = _clause_or_fallback_evidence(
            context=context,
            clauses=[],
            evidence_records=evidence_records,
            excerpt=f"Missing jurisdiction compliance clause: {clause_type}",
        )
        findings.append(
            _make_finding(
                findings=findings,
                category="compliance",
                title="Missing compliance clause",
                description=(
                    "The jurisdiction rules require compliance clause "
                    f"'{clause_type}', but it was not detected."
                ),
                severity=severity,
                confidence=1.0,
                evidence=evidence,
                recommendation=str(
                    item.get("description")
                    or f"Add the jurisdiction-required clause '{clause_type}'."
                ),
                field_name=clause_type,
                issue_type="missing_compliance_clause",
                source_clause_text=evidence[0].excerpt,
            )
        )


def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Validate extracted clause dictionaries for compliance checks."""
    return coerce_extracted_clauses(
        raw_clauses,
        error_type=ComplianceAgentError,
        list_error="extracted_contract['clauses'] must be a list.",
        mapping_error=lambda index, _clause: (
            f"extracted_contract['clauses'][{index}] must be a mapping."
        ),
        missing_text_error=lambda index: (
            f"extracted_contract['clauses'][{index}] is missing text."
        ),
        invalid_error=lambda index, exc: (
            f"extracted_contract['clauses'][{index}] is invalid: {exc}"
        ),
    )


def _jurisdiction_sources(
    context: Mapping[str, Any],
    jurisdiction_rules: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[str]:
    """Return text sources that may name the governing jurisdiction."""
    sources = [str(context.get("jurisdiction") or "")]
    sources.append(str(jurisdiction_rules.get("jurisdiction") or ""))
    governing_law = _as_mapping(jurisdiction_rules.get("governing_law"))
    sources.extend(str(value) for value in governing_law.values() if _is_non_empty(value))
    sources.extend(
        clause.text
        for clause in _find_clauses(clauses, ("governing_law", "governing law", "jurisdiction"))
    )
    return sources


def _rule_marks_high_risk(jurisdiction_rules: Mapping[str, Any]) -> bool:
    """Return whether jurisdiction_rules.yaml marks the jurisdiction high risk."""
    raw_high_risk = jurisdiction_rules.get("high_risk")
    if isinstance(raw_high_risk, bool):
        return raw_high_risk
    risk_tier = _normalize_key(str(jurisdiction_rules.get("risk_tier") or ""))
    return risk_tier in {"high", "critical", "sanctioned"}


def _jurisdiction_required_clauses(
    jurisdiction_rules: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Return supported jurisdiction-rule required clause entries."""
    candidates: list[Any] = []
    for key in (
        "required_clauses",
        "compliance_required_clauses",
        "compliance_clauses",
        "regulatory_requirements",
    ):
        raw = jurisdiction_rules.get(key)
        if isinstance(raw, list):
            candidates.extend(raw)

    normalized: list[Mapping[str, Any]] = []
    for item in candidates:
        if isinstance(item, Mapping):
            normalized.append(dict(item))
        elif _is_non_empty(item):
            normalized.append({"clause_type": str(item)})
    return normalized


def _required_clause_keywords(
    item: Mapping[str, Any],
    clause_type: str,
) -> tuple[str, ...]:
    """Return matching keywords for a required compliance clause."""
    raw_keywords = item.get("keywords")
    keywords = [
        str(keyword)
        for keyword in raw_keywords
        if _is_non_empty(keyword)
    ] if isinstance(raw_keywords, list) else []
    keywords.extend(
        [
            clause_type,
            clause_type.replace("_", " "),
            _normalize_key(clause_type).replace("_", " "),
        ]
    )
    return tuple(dict.fromkeys(keyword for keyword in keywords if keyword.strip()))


def _gdpr_obligation_present(obligation: str, combined_text: str) -> bool:
    """Return whether a GDPR obligation appears in the contract text."""
    normalized_obligation = _normalize_key(obligation)
    keywords = GDPR_OBLIGATION_KEYWORDS.get(
        normalized_obligation,
        (obligation, obligation.replace("_", " ")),
    )
    return any(keyword.lower() in combined_text for keyword in keywords)


def _find_clauses(
    clauses: Sequence[ExtractedClause],
    keywords: Sequence[str],
) -> list[ExtractedClause]:
    """Return clauses whose type, title, or text matches any keyword."""
    normalized_keywords = {
        _normalize_key(keyword)
        for keyword in keywords
        if str(keyword).strip()
    }
    matches: list[ExtractedClause] = []
    for clause in clauses:
        haystack = _normalize_key(
            " ".join([clause.clause_type, clause.title, clause.text])
        )
        if any(keyword in haystack for keyword in normalized_keywords):
            matches.append(clause)
    return matches


def _clause_or_fallback_evidence(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
    evidence_records: Sequence[Mapping[str, Any]],
    excerpt: str,
) -> list[EvidencePointer]:
    """Return clause evidence when available, otherwise fallback source evidence."""
    if clauses:
        clause = clauses[0]
        return [
            EvidencePointer(
                evidence_id=clause.evidence.evidence_id,
                document_id=clause.evidence.document_id,
                source_file=str(context.get("contract_file") or clause.evidence.source_file),
                page_number=clause.page_number or clause.evidence.page_number,
                clause_reference=clause.section_reference or clause.evidence.clause_reference,
                excerpt=_truncate(clause.text),
            )
        ]
    return [_fallback_evidence(context, evidence_records, excerpt)]


def _fallback_evidence(
    context: Mapping[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
    excerpt: str,
) -> EvidencePointer:
    """Return the best available compliance evidence pointer."""
    for record in evidence_records:
        source_file = record.get("source_file")
        if not _is_non_empty(source_file):
            continue
        return EvidencePointer(
            evidence_id=_optional_str(record.get("evidence_id")),
            document_id=_optional_str(record.get("document_id")),
            source_file=str(source_file),
            page_number=_optional_int(record.get("page_number")),
            clause_reference=_optional_str(record.get("section_reference")),
            excerpt=excerpt or _optional_str(record.get("excerpt")),
        )
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "unknown"),
        excerpt=excerpt,
    )


def _make_finding(
    findings: Sequence[Finding],
    category: str,
    title: str,
    description: str,
    severity: Severity,
    confidence: float,
    evidence: Sequence[EvidencePointer],
    recommendation: str,
    field_name: str,
    issue_type: str,
    source_clause_text: str | None,
) -> Finding:
    """Create a shared compliance finding."""
    finding_evidence = list(evidence)
    primary_evidence = finding_evidence[0]
    return Finding(
        finding_id=f"CMP-{len(findings) + 1:03d}",
        category=category,
        title=title,
        description=description,
        severity=severity,
        confidence=confidence,
        evidence=finding_evidence,
        recommendation=recommendation,
        field_name=field_name,
        issue_type=issue_type,
        message=description,
        source_clause_text=source_clause_text or primary_evidence.excerpt,
        source_page=primary_evidence.page_number,
        evidence_pointer=primary_evidence,
        manual_review_required=True,
        risk_engine_ready=True,
    )


def _extract_evidence_records(
    evidence_index: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    """Read evidence records from accepted evidence index shapes."""
    if evidence_index is None:
        return []

    candidate: Any = evidence_index
    if "evidence_index" in evidence_index:
        candidate = evidence_index["evidence_index"]

    if not isinstance(candidate, Mapping):
        return []

    records = candidate.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, Mapping)]


def _severity_from_value(value: Any, default: Severity) -> Severity:
    """Parse a severity value with fallback."""
    try:
        return Severity(str(value).upper())
    except ValueError:
        return default


def _contains_word(text: str, needle: str) -> bool:
    """Case-insensitive word-ish containment."""
    if not text or not needle:
        return False
    haystack = _normalize_key(text)
    target = _normalize_key(needle)
    return target in haystack.split() or target in haystack


__all__ = [
    "ComplianceAgentError",
    "run_compliance_review",
]
