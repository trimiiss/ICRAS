"""Merge and deduplicate findings for workflow finalization."""

from typing import Any, Mapping, Optional, Sequence

from agents.orchestrator.errors import OrchestratorAgentError
from schemas.common import EvidencePointer, Severity
from schemas.finding import Finding
from utils.severity import SEVERITY_RANK, highest_severity
from utils.text import normalize_key as _normalize_key


def merge_deduplicate_sort_findings(
    run_id: str,
    context: Mapping[str, Any],
    validation_result: Mapping[str, Any],
    risk_result: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
    compliance_result: Optional[Mapping[str, Any]] = None,
    anomaly_result: Optional[Mapping[str, Any]] = None,
) -> list[Finding]:
    """Merge all source findings into a deterministic final list."""
    source_findings: list[Finding] = []
    source_findings.extend(
        _coerce_finding_list(
            validation_result.get("findings", []),
            fallback_prefix="VAL",
            context=context,
        )
    )
    source_findings.extend(
        _coerce_finding_list(
            risk_result.get("findings", []),
            fallback_prefix="RISK",
            context=context,
        )
    )
    if compliance_result is not None:
        source_findings.extend(
            _coerce_finding_list(
                compliance_result.get("findings", []),
                fallback_prefix="CMP",
                context=context,
            )
        )
    if anomaly_result is not None:
        source_findings.extend(
            _coerce_finding_list(
                anomaly_result.get("findings", []),
                fallback_prefix="ANM",
                context=context,
            )
        )
    source_findings.extend(
        counterparty_findings(
            run_id=run_id,
            context=context,
            counterparty_resolution=counterparty_resolution,
        )
    )

    by_key: dict[tuple[str, ...], Finding] = {}
    for finding in source_findings:
        key = _finding_key(finding)
        if key in by_key:
            by_key[key] = _merge_findings(by_key[key], finding)
        else:
            by_key[key] = finding

    return sorted(
        by_key.values(),
        key=lambda finding: (
            -SEVERITY_RANK[finding.severity],
            finding.category.lower(),
            finding.finding_id,
        ),
    )


def counterparty_findings(
    run_id: str,
    context: Mapping[str, Any],
    counterparty_resolution: Mapping[str, Any],
) -> list[Finding]:
    """Convert flagged counterparty matches into shared findings."""
    raw_matches = counterparty_resolution.get("matches", [])
    if not isinstance(raw_matches, list):
        raise OrchestratorAgentError(
            "Expected counterparty_resolution['matches'] to be a list."
        )

    findings: list[Finding] = []
    for index, raw_match in enumerate(raw_matches, start=1):
        if not isinstance(raw_match, Mapping):
            continue
        match_status = str(raw_match.get("match_status") or "")
        risk_flag = str(raw_match.get("risk_flag") or "").strip()
        manual_review_required = bool(raw_match.get("manual_review_required"))
        if not manual_review_required and not risk_flag and match_status not in {"weak", "no_match"}:
            continue

        original_name = str(raw_match.get("original_party_name") or "Unknown party")
        evidence_data = raw_match.get("evidence_pointer")
        evidence = (
            EvidencePointer.model_validate(evidence_data)
            if isinstance(evidence_data, Mapping)
            else _fallback_evidence(context, excerpt=original_name)
        )
        severity = Severity.HIGH if match_status == "no_match" or risk_flag else Severity.MEDIUM
        findings.append(
            Finding(
                finding_id=f"CPY-{index:03d}",
                category="counterparty",
                title="Counterparty requires review",
                description=(
                    f"Counterparty '{original_name}' resolved with status "
                    f"'{match_status or 'unknown'}'."
                ),
                severity=severity,
                confidence=float(raw_match.get("similarity_score") or 0.0),
                evidence=[evidence],
                recommendation="Review counterparty identity before approval.",
                field_name="counterparty",
                issue_type="counterparty_resolution_review",
                message=risk_flag or "Counterparty match requires manual review.",
                source_clause_text=original_name,
                source_page=evidence.page_number,
                evidence_pointer=evidence,
                manual_review_required=True,
                risk_engine_ready=True,
            )
        )
    return findings


def overall_severity(severities: Sequence[Severity]) -> Severity:
    """Return the highest severity, defaulting to LOW."""
    return highest_severity(severities)


def _coerce_finding_list(
    raw_findings: Any,
    fallback_prefix: str,
    context: Mapping[str, Any],
) -> list[Finding]:
    """Validate finding dictionaries with clear orchestrator errors."""
    if not isinstance(raw_findings, list):
        raise OrchestratorAgentError(
            f"Expected {fallback_prefix} findings to be a list."
        )

    findings: list[Finding] = []
    for index, raw_finding in enumerate(raw_findings, start=1):
        if not isinstance(raw_finding, Mapping):
            raise OrchestratorAgentError(
                f"Expected {fallback_prefix} finding {index} to be a mapping."
            )
        finding_data = dict(raw_finding)
        finding_data.setdefault("finding_id", f"{fallback_prefix}-{index:03d}")
        if not finding_data.get("evidence"):
            evidence_pointer = finding_data.get("evidence_pointer")
            if isinstance(evidence_pointer, Mapping):
                finding_data["evidence"] = [dict(evidence_pointer)]
            else:
                finding_data["evidence"] = [_fallback_evidence(context).model_dump(mode="json")]
        try:
            findings.append(Finding.model_validate(finding_data))
        except Exception as exc:
            raise OrchestratorAgentError(
                f"Invalid {fallback_prefix} finding {index}: {exc}"
            ) from exc
    return findings


def _fallback_evidence(
    context: Mapping[str, Any],
    excerpt: Optional[str] = None,
) -> EvidencePointer:
    """Build a fallback evidence pointer for non-clause findings."""
    return EvidencePointer(
        source_file=str(context.get("contract_file") or "unknown"),
        excerpt=excerpt or "Finding created from structured pipeline output.",
    )


def _finding_key(finding: Finding) -> tuple[str, ...]:
    """Return a stable key used to deduplicate equivalent findings."""
    evidence = finding.evidence_pointer or finding.evidence[0]
    evidence_key = (
        evidence.evidence_id
        or f"{evidence.source_file}:{evidence.page_number}:{evidence.clause_reference}:{_normalize_key(evidence.excerpt or '')[:80]}"
    )
    return (
        _normalize_key(finding.issue_type or finding.title),
        _normalize_key(finding.field_name or finding.category),
        _normalize_key(evidence_key),
    )


def _merge_findings(existing: Finding, incoming: Finding) -> Finding:
    """Merge duplicate findings while preserving the strongest signal."""
    severity = (
        incoming.severity
        if SEVERITY_RANK[incoming.severity] > SEVERITY_RANK[existing.severity]
        else existing.severity
    )
    evidence_by_key: dict[tuple[str, ...], EvidencePointer] = {}
    for evidence in [*existing.evidence, *incoming.evidence]:
        key = (
            evidence.evidence_id or "",
            evidence.source_file,
            str(evidence.page_number or ""),
            evidence.clause_reference or "",
            evidence.excerpt or "",
        )
        evidence_by_key[key] = evidence
    evidence = list(evidence_by_key.values())
    primary_evidence = existing.evidence_pointer or evidence[0]
    return Finding(
        finding_id=existing.finding_id,
        category=existing.category,
        title=existing.title,
        description=existing.description,
        severity=severity,
        confidence=max(existing.confidence, incoming.confidence),
        evidence=evidence,
        recommendation=existing.recommendation or incoming.recommendation,
        field_name=existing.field_name or incoming.field_name,
        issue_type=existing.issue_type or incoming.issue_type,
        message=existing.message or incoming.message,
        source_clause_text=existing.source_clause_text or incoming.source_clause_text,
        source_page=existing.source_page or incoming.source_page,
        evidence_pointer=primary_evidence,
        manual_review_required=(
            existing.manual_review_required or incoming.manual_review_required
        ),
        risk_engine_ready=existing.risk_engine_ready or incoming.risk_engine_ready,
    )
