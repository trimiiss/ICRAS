"""Agent E - Clause Risk Scoring Engine.

The risk agent deterministically scores extracted clauses and validation
findings against playbook and approval-policy rules. No LLM calls are used.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import yaml

from schemas.common import EvidencePointer, Severity
from schemas.extracted_clause import ExtractedClause
from schemas.finding import Finding
from schemas.risk_result import ClauseAnalysisResult, ClauseRisk, RiskResult
from utils.run_manager import append_audit_event


class RiskAgentError(Exception):
    """Raised when Agent E cannot produce clause_analysis.json."""


SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

CLAUSE_ALIASES: dict[str, tuple[str, ...]] = {
    "payment_terms": ("payment_terms", "payment", "fees", "invoice", "billing"),
    "liability_cap": (
        "liability_cap",
        "limitation_of_liability",
        "limited_liability",
        "liability_limit",
    ),
    "governing_law": ("governing_law", "jurisdiction", "choice_of_law"),
    "auto_renewal": ("auto_renewal", "renewal", "automatic_renewal"),
    "data_protection": ("data_protection", "gdpr", "privacy", "personal_data"),
    "party_names": ("party_names", "parties", "party", "counterparty"),
    "signature": ("signature", "signatures", "execution", "signed"),
    "termination": ("termination", "term_and_duration", "term", "expiration"),
    "confidentiality_definition": (
        "confidentiality_definition",
        "confidentiality",
        "confidential_information",
    ),
    "term_and_duration": ("term_and_duration", "termination", "duration", "term"),
    "limitation_of_liability": (
        "limitation_of_liability",
        "liability_cap",
        "liability",
    ),
}

KNOWN_JURISDICTIONS: tuple[str, ...] = (
    "New York",
    "Delaware",
    "California",
    "Texas",
    "Florida",
    "England and Wales",
    "United Kingdom",
    "Germany",
    "France",
    "India",
    "Singapore",
    "Netherlands",
    "Ireland",
    "Russia",
    "Iran",
    "North Korea",
    "Syria",
)

STANDARD_PAYMENT_DAYS = 30


def run_risk_assessment(
    findings: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
    extracted_contract: Optional[Dict[str, Any]] = None,
    validation_result: Optional[Dict[str, Any]] = None,
    run_dir: str | Path | None = None,
    playbook_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Score extracted clauses and validation findings.

    Args:
        findings: Optional legacy list of shared findings. If no clause context is
            provided, Agent E returns an aggregate RiskResult only.
        context: Context packet data containing playbook and approval policy.
        extracted_contract: Extracted contract data from Agent B.
        validation_result: Validation findings from Agent D.
        run_dir: Run directory used to read and write artifacts.
        playbook_path: Optional YAML playbook override.

    Returns:
        A dictionary with ``clause_analysis``, ``risk_result``, ``findings``, and
        artifact paths.
    """
    run_path = _validate_run_dir(run_dir) if run_dir is not None else None
    context_payload = context or _read_context_packet(run_path)
    extracted_payload = extracted_contract or _read_json_artifact(
        run_path,
        "extracted_contract.json",
        required=context_payload is not None,
    )
    validation_payload = validation_result or _read_json_artifact(
        run_path,
        "validation_findings.json",
        required=False,
    )

    if context_payload is None and extracted_payload is None:
        return _legacy_aggregate(findings or [])

    if context_payload is None:
        raise RiskAgentError(
            "Agent E requires context data. Provide context or run_dir with "
            "context_packet.json."
        )
    if extracted_payload is None:
        raise RiskAgentError(
            "Agent E requires extracted clauses. Provide extracted_contract or "
            "run_dir with extracted_contract.json."
        )

    playbook = _load_playbook(context_payload, playbook_path)
    approval_policy = _as_mapping(context_payload.get("approval_policy"))
    validation_findings = _coerce_findings(
        (validation_payload or {}).get("findings", []) if validation_payload else []
    )
    if findings:
        validation_findings.extend(_coerce_findings(findings))

    clauses = _coerce_clauses(extracted_payload.get("clauses", []))
    run_id = str(context_payload.get("run_id") or extracted_payload.get("run_id") or "unknown-run")

    clause_risks: list[ClauseRisk] = []
    _score_payment_terms(clauses, context_payload, clause_risks)
    _score_validation_findings(validation_findings, context_payload, clause_risks)
    _score_high_risk_jurisdiction(context_payload, clauses, clause_risks)
    _score_auto_renewal(clauses, context_payload, approval_policy, clause_risks)
    _score_gdpr_requirements(clauses, context_payload, approval_policy, clause_risks)
    _score_multi_jurisdiction(clauses, context_payload, clause_risks)
    _score_multi_party(clauses, context_payload, validation_findings, clause_risks)
    _score_playbook_variance(clauses, context_payload, playbook, clause_risks)
    _score_prohibited_clauses(clauses, context_payload, playbook, clause_risks)

    clause_risks = _deduplicate_clause_risks(clause_risks)
    findings_out = [
        _finding_from_clause_risk(index=index, risk=risk)
        for index, risk in enumerate(clause_risks, start=1)
    ]
    overall_severity = _overall_severity([risk.severity for risk in clause_risks])
    requires_legal_review = any(risk.legal_review_required for risk in clause_risks)
    summary = _summary(overall_severity, clause_risks)

    clause_analysis = ClauseAnalysisResult(
        run_id=run_id,
        overall_severity=overall_severity,
        clause_risks=clause_risks,
        findings=findings_out,
        requires_legal_review=requires_legal_review,
        summary=summary,
    )
    risk_result = RiskResult(
        overall_severity=overall_severity,
        findings=findings_out,
        requires_human_review=requires_legal_review,
        summary=summary,
        total_findings=len(findings_out),
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "clause_analysis.json"
        _write_model_json(output_path, clause_analysis)
        append_audit_event(
            run_path,
            {
                "event": "risk_scoring_completed",
                "agent": "risk_agent",
                "message": "Agent E scored clauses against playbook and policy rules.",
                "artifacts": [output_path.name],
                "risk_count": len(clause_risks),
                "overall_severity": overall_severity.value,
                "requires_legal_review": requires_legal_review,
            },
        )
        artifact_paths["clause_analysis"] = str(output_path)

    return {
        "clause_analysis": clause_analysis.model_dump(mode="json"),
        "risk_result": risk_result.model_dump(mode="json"),
        "findings": [finding.model_dump(mode="json") for finding in findings_out],
        "artifact_paths": artifact_paths,
    }


def _legacy_aggregate(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep the original aggregate-only call shape working."""
    finding_models = _coerce_findings(findings)
    overall_severity = _overall_severity([finding.severity for finding in finding_models])
    requires_review = any(
        finding.severity in {Severity.HIGH, Severity.CRITICAL}
        or finding.manual_review_required
        for finding in finding_models
    )
    result = RiskResult(
        overall_severity=overall_severity,
        findings=finding_models,
        requires_human_review=requires_review,
        summary=_summary(overall_severity, []),
        total_findings=len(finding_models),
    )
    return {
        "risk_result": result.model_dump(mode="json"),
        "findings": result.model_dump(mode="json")["findings"],
        "artifact_paths": {},
    }


def _score_payment_terms(
    clauses: Sequence[ExtractedClause],
    context: Mapping[str, Any],
    clause_risks: list[ClauseRisk],
) -> None:
    """Score payment terms against the net-30 standard."""
    for clause in _find_clauses(clauses, CLAUSE_ALIASES["payment_terms"]):
        for days in _extract_payment_days(clause.text):
            if days <= STANDARD_PAYMENT_DAYS:
                continue
            clause_risks.append(
                _make_clause_risk(
                    field_name="payment_terms",
                    issue_type="payment_terms_exceed_standard",
                    severity=Severity.HIGH,
                    explanation=(
                        f"Payment terms are net-{days}, which exceeds the "
                        f"approved net-{STANDARD_PAYMENT_DAYS} standard."
                    ),
                    action=(
                        "Revise payment terms to net-30 or obtain legal and "
                        "business approval for the extended payment cycle."
                    ),
                    context=context,
                    clause=clause,
                    tolerance_threshold="payment_terms_days_standard=30",
                )
            )


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


def _make_clause_risk(
    field_name: str,
    issue_type: str,
    severity: Severity,
    explanation: str,
    action: str,
    context: Mapping[str, Any],
    clause: Optional[ExtractedClause],
    evidence: Optional[EvidencePointer] = None,
    clause_text: Optional[str] = None,
    tolerance_threshold: Optional[str] = None,
) -> ClauseRisk:
    """Create a ClauseRisk with evidence and legal-review flags."""
    primary_evidence = evidence
    if primary_evidence is None and clause is not None:
        primary_evidence = _clause_evidence(context, clause)
    if primary_evidence is None:
        primary_evidence = EvidencePointer(
            source_file=str(context.get("contract_file") or "unknown"),
            excerpt=clause_text or explanation,
        )
    text = clause_text or (clause.text if clause is not None else primary_evidence.excerpt)
    return ClauseRisk(
        risk_id="PENDING",
        clause_id=clause.clause_id if clause is not None else None,
        field_name=field_name,
        issue_type=issue_type,
        severity=severity,
        risk_explanation=explanation,
        recommended_action=action,
        clause_text=_truncate(text or explanation),
        source_page=primary_evidence.page_number,
        evidence_pointer=primary_evidence,
        legal_review_required=severity in {Severity.HIGH, Severity.CRITICAL},
        tolerance_threshold=tolerance_threshold,
    )


def _finding_from_clause_risk(index: int, risk: ClauseRisk) -> Finding:
    """Convert a ClauseRisk into the shared Finding schema."""
    return Finding(
        finding_id=f"RISK-{index:03d}",
        category="clause_risk",
        title=risk.issue_type.replace("_", " ").title(),
        description=risk.risk_explanation,
        severity=risk.severity,
        confidence=1.0,
        evidence=[risk.evidence_pointer],
        recommendation=risk.recommended_action,
        field_name=risk.field_name,
        issue_type=risk.issue_type,
        message=risk.risk_explanation,
        source_clause_text=risk.clause_text,
        source_page=risk.source_page,
        evidence_pointer=risk.evidence_pointer,
        manual_review_required=risk.legal_review_required,
        risk_engine_ready=True,
    )


def _deduplicate_clause_risks(risks: Sequence[ClauseRisk]) -> list[ClauseRisk]:
    """Remove duplicate risks and assign deterministic risk IDs."""
    deduped: list[ClauseRisk] = []
    seen: set[tuple[str, str, str, str]] = set()
    for risk in risks:
        key = (
            risk.field_name,
            risk.issue_type,
            risk.clause_text,
            risk.evidence_pointer.excerpt or "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(risk.model_copy(update={"risk_id": f"RISK-{len(deduped) + 1:03d}"}))
    return deduped


def _coerce_clauses(raw_clauses: Any) -> list[ExtractedClause]:
    """Validate extracted clause dictionaries."""
    if not isinstance(raw_clauses, list):
        raise RiskAgentError("extracted_contract['clauses'] must be a list.")
    clauses: list[ExtractedClause] = []
    for index, raw_clause in enumerate(raw_clauses, start=1):
        if not isinstance(raw_clause, Mapping):
            raise RiskAgentError(
                f"extracted_contract['clauses'][{index - 1}] must be a mapping."
            )
        clause_data = dict(raw_clause)
        clause_type = str(clause_data.get("clause_type") or f"clause_{index}")
        text = str(clause_data.get("text") or clause_data.get("clause_text") or "")
        if not text.strip():
            raise RiskAgentError(
                f"extracted_contract['clauses'][{index - 1}] is missing text."
            )
        clause_data.setdefault("clause_id", f"CLAUSE-{index:03d}")
        clause_data.setdefault("clause_type", clause_type)
        clause_data.setdefault("title", clause_type.replace("_", " ").title())
        clause_data.setdefault("text", text)
        clause_data.setdefault("clause_text", text)
        clause_data.setdefault("confidence", 1.0)
        clause_data.setdefault("confidence_score", clause_data["confidence"])
        if "page_numbers" not in clause_data and clause_data.get("page_number") is not None:
            clause_data["page_numbers"] = [clause_data["page_number"]]
        clause_data.setdefault(
            "evidence",
            EvidencePointer(
                source_file="unknown",
                page_number=_optional_int(clause_data.get("page_number")),
                clause_reference=_optional_str(clause_data.get("section_reference")),
                excerpt=_truncate(text),
            ).model_dump(mode="json"),
        )
        clause_data.setdefault("evidence_pointer", clause_data["evidence"])
        clause_data.setdefault(
            "manual_review_required",
            float(clause_data["confidence"]) < 0.75,
        )
        try:
            clauses.append(ExtractedClause.model_validate(clause_data))
        except Exception as exc:
            raise RiskAgentError(
                f"extracted_contract['clauses'][{index - 1}] is invalid: {exc}"
            ) from exc
    return clauses


def _coerce_findings(raw_findings: Any) -> list[Finding]:
    """Validate shared finding dictionaries."""
    if not raw_findings:
        return []
    if not isinstance(raw_findings, list):
        raise RiskAgentError("validation findings must be a list.")
    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings):
        if not isinstance(raw_finding, Mapping):
            continue
        try:
            findings.append(Finding.model_validate(raw_finding))
        except Exception as exc:
            raise RiskAgentError(f"finding[{index}] is invalid: {exc}") from exc
    return findings


def _find_clauses(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> list[ExtractedClause]:
    """Return clauses matching any alias."""
    return [clause for clause in clauses if _clause_matches(clause, aliases)]


def _first_clause(
    clauses: Sequence[ExtractedClause],
    aliases: Sequence[str],
) -> Optional[ExtractedClause]:
    """Return first clause matching aliases."""
    matches = _find_clauses(clauses, aliases)
    return matches[0] if matches else None


def _clause_matches(clause: ExtractedClause, aliases: Sequence[str]) -> bool:
    """Return whether a clause matches any canonical alias."""
    normalized_aliases = {_normalize_key(alias) for alias in aliases}
    clause_type = _normalize_key(clause.clause_type)
    title = _normalize_key(clause.title)
    text = _normalize_key(clause.text)
    return any(
        alias == clause_type
        or alias == title
        or alias in clause_type
        or alias in title
        or alias in text
        for alias in normalized_aliases
    )


def _aliases_for_clause_type(clause_type: str) -> tuple[str, ...]:
    """Return aliases for a playbook clause type."""
    normalized = _normalize_key(clause_type)
    configured = CLAUSE_ALIASES.get(normalized)
    if configured is not None:
        return configured
    return (normalized,)


def _extract_payment_days(text: str) -> list[int]:
    """Extract net payment day values."""
    days: list[int] = []
    for match in re.finditer(r"\bnet[\s-]?(\d{1,3})\b", text, re.IGNORECASE):
        value = int(match.group(1))
        if value not in days:
            days.append(value)
    return days


def _extract_jurisdictions(text: str) -> list[str]:
    """Extract known jurisdiction names from text."""
    return [
        jurisdiction
        for jurisdiction in KNOWN_JURISDICTIONS
        if _contains_word(text, jurisdiction)
    ]


def _extract_party_names(
    context: Mapping[str, Any],
    clauses: Sequence[ExtractedClause],
) -> list[str]:
    """Extract likely party names from context and party clauses."""
    parties: list[str] = []
    raw = context.get("party_names") or context.get("parties")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        parties.extend(str(item).strip() for item in raw if _is_non_empty(item))
    elif isinstance(raw, Mapping):
        parties.extend(str(item).strip() for item in raw.values() if _is_non_empty(item))
    elif _is_non_empty(raw):
        parties.extend(_split_party_text(str(raw)))
    party_clause = _first_clause(clauses, CLAUSE_ALIASES["party_names"])
    if party_clause is not None:
        parties.extend(_split_party_text(party_clause.text))

    unique: list[str] = []
    for party in parties:
        cleaned = party.strip(" .;:")
        if len(cleaned) < 3:
            continue
        if cleaned.lower() not in {existing.lower() for existing in unique}:
            unique.append(cleaned)
    return unique


def _split_party_text(text: str) -> list[str]:
    """Split common party-list phrasing into names."""
    match = re.search(
        r"(?:between|among)\s+(.+?)(?:\.|,?\s+each\s+a\s+|,?\s+collectively\s+)",
        text,
        re.IGNORECASE,
    )
    party_text = match.group(1) if match is not None else text
    party_text = re.sub(r"\s+\([^)]*\)", "", party_text)
    return [
        item.strip()
        for item in re.split(r"\s*,\s*|\s+and\s+|\s+&\s+", party_text)
        if item.strip()
    ]


def _risk_tolerance_thresholds(context: Mapping[str, Any]) -> dict[str, float]:
    """Return configured or default tolerance thresholds."""
    policy = _as_mapping(context.get("approval_policy"))
    thresholds = _as_mapping(policy.get("risk_tolerance_thresholds"))
    return {
        "minor_missing_required_ratio": _float_or_default(
            thresholds.get("minor_missing_required_ratio"),
            0.25,
        ),
        "material_missing_required_ratio": _float_or_default(
            thresholds.get("material_missing_required_ratio"),
            0.50,
        ),
    }


def _minor_variance_severity(configured_severity: Severity) -> Severity:
    """Downgrade a missing-clause issue when tolerance classifies it as minor."""
    if configured_severity == Severity.CRITICAL:
        return Severity.HIGH
    if configured_severity == Severity.HIGH:
        return Severity.MEDIUM
    if configured_severity == Severity.MEDIUM:
        return Severity.LOW
    return Severity.LOW


def _overall_severity(severities: Iterable[Severity]) -> Severity:
    """Return highest severity, defaulting to LOW."""
    severity_list = list(severities)
    if not severity_list:
        return Severity.LOW
    return max(severity_list, key=lambda severity: SEVERITY_RANK[severity])


def _summary(overall_severity: Severity, risks: Sequence[ClauseRisk]) -> str:
    """Build a concise risk summary."""
    if not risks:
        return "No clause-level risks were detected."
    high_or_critical = sum(
        1 for risk in risks if risk.severity in {Severity.HIGH, Severity.CRITICAL}
    )
    return (
        f"Agent E identified {len(risks)} clause-level risk(s); "
        f"{high_or_critical} require legal review. Overall severity is "
        f"{overall_severity.value}."
    )


def _load_playbook(
    context: Mapping[str, Any],
    playbook_path: str | Path | None,
) -> Mapping[str, Any]:
    """Load YAML playbook rules from path or context packet."""
    if playbook_path is None:
        return _as_mapping(context.get("playbook"))

    path = Path(playbook_path).resolve()
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RiskAgentError(f"Failed to load playbook YAML '{path}': {exc}") from exc
    if not isinstance(data, Mapping):
        raise RiskAgentError(f"Expected playbook YAML '{path}' to contain a mapping.")
    return data


def _read_context_packet(run_path: Optional[Path]) -> Optional[dict[str, Any]]:
    """Read context_packet.json from the run directory."""
    return _read_json_artifact(run_path, "context_packet.json", required=False)


def _read_json_artifact(
    run_path: Optional[Path],
    filename: str,
    required: bool,
) -> Optional[dict[str, Any]]:
    """Read a run-local JSON artifact."""
    if run_path is None:
        if required:
            raise RiskAgentError(f"Missing required artifact: {filename}")
        return None
    path = run_path / filename
    if not path.exists():
        if required:
            raise RiskAgentError(f"Missing required artifact: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RiskAgentError(f"Failed to read '{path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise RiskAgentError(f"Expected '{path}' to contain a JSON object.")
    return payload


def _validate_run_dir(run_dir: str | Path) -> Path:
    """Return a valid run directory path or raise a clear risk-agent error."""
    run_path = Path(run_dir).resolve()
    if not run_path.exists():
        raise RiskAgentError(
            f"Run directory does not exist: {run_path}. "
            "Create it with create_run_folder before risk scoring."
        )
    if not run_path.is_dir():
        raise RiskAgentError(f"Run path is not a directory: {run_path}")
    return run_path


def _write_model_json(path: Path, model: ClauseAnalysisResult) -> None:
    """Write clause analysis as deterministic JSON."""
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(model.model_dump(mode="json"), file, indent=2, ensure_ascii=False)
            file.write("\n")
    except OSError as exc:
        raise RiskAgentError(f"Failed to write clause analysis '{path}': {exc}") from exc


def _clause_evidence(
    context: Mapping[str, Any],
    clause: ExtractedClause,
) -> EvidencePointer:
    """Build a source pointer from an extracted clause."""
    return EvidencePointer(
        evidence_id=clause.evidence.evidence_id,
        document_id=clause.evidence.document_id,
        source_file=str(context.get("contract_file") or clause.evidence.source_file),
        page_number=clause.page_number or clause.evidence.page_number,
        clause_reference=clause.section_reference or clause.evidence.clause_reference,
        excerpt=_truncate(clause.text),
    )


def _primary_evidence(evidence: Sequence[EvidencePointer]) -> Optional[EvidencePointer]:
    """Return first evidence pointer when present."""
    return evidence[0] if evidence else None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return value if it is a mapping, else an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _severity_from_value(value: Any, default: Severity) -> Severity:
    """Parse a Severity value."""
    try:
        return Severity(str(value).upper())
    except ValueError:
        return default


def _contains_word(text: str, needle: str) -> bool:
    """Case-insensitive word-ish containment for jurisdiction names."""
    if not text or not needle:
        return False
    return bool(re.search(rf"\b{re.escape(needle)}\b", text, re.IGNORECASE))


def _normalize_key(value: str) -> str:
    """Normalize text for alias matching."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_non_empty(value: Any) -> bool:
    """Return whether a value carries meaningful content."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_is_non_empty(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_is_non_empty(item) for item in value)
    return True


def _optional_str(value: Any) -> Optional[str]:
    """Return a non-empty string or None."""
    return str(value) if _is_non_empty(value) else None


def _optional_int(value: Any) -> Optional[int]:
    """Return an int if possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: Any, default: float) -> float:
    """Return a float threshold with fallback."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truncate(value: str, max_chars: int = 500) -> str:
    """Return a compact source snippet."""
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."
