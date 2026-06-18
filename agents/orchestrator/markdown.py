"""Markdown artifact rendering for workflow orchestration."""

from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.orchestrator.metrics import (
    LOW_CONFIDENCE_AUDIT_THRESHOLD,
    confidence_value,
)
from agents.orchestrator.state import PIPELINE_STEP_ORDER
from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import EvidencePointer, Severity
from schemas.exception_triage import ExceptionTriageItem
from schemas.finding import Finding
from utils.mapping import as_mapping as _as_mapping


def write_exceptions_markdown(
    path: Path,
    run_id: str,
    approval_status: ApprovalStatus,
    overall_severity: Severity,
    approval_routes: Sequence[ApprovalRoute],
    exceptions: Sequence[ExceptionTriageItem],
    findings: Sequence[Finding],
) -> None:
    """Write the human-readable exception summary."""
    lines = [
        "# ICRAS Exceptions Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Decision: {approval_status.value}",
        f"- Overall Severity: {overall_severity.value}",
        f"- Final Finding Count: {len(findings)}",
        f"- Routed Exception Count: {len(exceptions)}",
        "",
        "## Next Actions",
    ]
    if exceptions:
        for exception in exceptions:
            lines.extend(
                [
                    f"- {exception.category.value}: {exception.next_action} "
                    f"(Approver: {exception.approver})",
                ]
            )
        lines.append("")
    else:
        lines.extend(["- No human approval required.", ""])

    lines.append("## Approval Routes")
    if approval_routes:
        for route in approval_routes:
            approvers = ", ".join(route.approvers) if route.approvers else "None"
            finding_ids = ", ".join(route.finding_ids) if route.finding_ids else "None"
            lines.extend(
                [
                    f"### {route.category}",
                    f"- Approvers: {approvers}",
                    f"- Reason: {route.reason}",
                    f"- Findings: {finding_ids}",
                    "",
                ]
            )
    else:
        lines.extend(["No approval routes were required.", ""])

    lines.append("## Exceptions")
    if not exceptions:
        lines.extend(["No exceptions were detected.", ""])
    for exception in exceptions:
        evidence_text = _format_evidence_list(exception.evidence)
        lines.extend(
            [
                f"### {exception.category.value}: {exception.source_title}",
                f"- Finding ID: {exception.finding_id}",
                f"- Severity: {exception.severity.value}",
                f"- Approver: {exception.approver or 'None'}",
                f"- Reason: {exception.reason}",
                f"- Next Action: {exception.next_action}",
                f"- Evidence: {evidence_text}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_final_audit_markdown(
    run_dir: Path,
    run_id: str,
    step_events: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    approval_packet: Mapping[str, Any],
    final_findings: Mapping[str, Any],
    extracted_contract: Mapping[str, Any],
    artifact_paths: Mapping[str, Any],
) -> None:
    """Write the auditor-facing step-by-step Markdown trace."""
    audit_path = run_dir / "audit_log.md"
    decision = _as_mapping(approval_packet.get("decision"))
    exception_categories = _as_mapping(metrics.get("exception_categories"))
    confidence_distributions = _as_mapping(metrics.get("confidence_distributions"))
    ordered_events = _ordered_step_events(step_events)

    lines = [
        "# ICRAS Audit Log",
        "",
        "## Run Summary",
        f"- Run ID: {run_id}",
        f"- Status: {metrics.get('status', '')}",
        f"- Decision: {decision.get('status', '')}",
        f"- Processing Duration Seconds: {_format_metric(metrics.get('total_processing_time_seconds'))}",
        f"- Extraction Count: {metrics.get('extraction_clause_count', 0)}",
        f"- Exception Count: {metrics.get('exception_count', 0)}",
        f"- Exception Categories: {_format_count_map(exception_categories)}",
        f"- Exception Rate Percent: {_format_metric(metrics.get('exception_rate_percent'))}",
        f"- Accuracy Percent: {_format_metric(metrics.get('accuracy_percent'))}",
        f"- Throughput Clauses Per Second: {_format_metric(metrics.get('throughput_clauses_per_second'))}",
        f"- Fallback Used: {'yes' if metrics.get('fallback_assisted') else 'no'}",
        f"- Fallback Reason: {metrics.get('fallback_reason') or 'None'}",
        f"- Low-Confidence Count: {metrics.get('low_confidence_count', 0)}",
        "",
        "## Workflow Order",
    ]

    for index, event in enumerate(ordered_events, start=1):
        step = str(event.get("step") or "unknown_step")
        agent = str(event.get("agent") or "unknown_agent")
        lines.append(f"{index}. {step}_completed ({agent})")

    lines.extend(["", "## Step Trace"])
    for event in ordered_events:
        step = str(event.get("step") or "unknown_step")
        lines.extend(
            [
                f"### {step}_completed",
                f"- Agent: {event.get('agent', '')}",
                f"- Status: {event.get('status', '')}",
                f"- Started At: {event.get('started_at', '')}",
                f"- Finished At: {event.get('finished_at', '')}",
                f"- Duration Seconds: {_format_metric(event.get('duration_seconds'))}",
                f"- Extracted Clause Count: {event.get('extracted_clause_count', 0)}",
                f"- Exception Count: {event.get('exception_count', 0)}",
                f"- Exception Categories: {_format_count_map(_as_mapping(event.get('exception_categories')))}",
                f"- Fallback Used: {'yes' if event.get('fallback_used') else 'no'}",
                f"- Fallback Reason: {event.get('fallback_reason') or 'None'}",
                f"- Low-Confidence Count: {event.get('low_confidence_count', 0)}",
                "",
                "#### Inputs",
            ]
        )
        lines.extend(_format_path_map(_as_mapping(event.get("input_paths"))))
        lines.extend(["", "#### Outputs"])
        lines.extend(_format_path_map(_as_mapping(event.get("output_paths"))))
        lines.append("")

    lines.append("## Confidence Scores")
    if confidence_distributions:
        for name in sorted(confidence_distributions):
            lines.extend(
                _format_confidence_distribution(
                    name,
                    _as_mapping(confidence_distributions[name]),
                )
            )
    else:
        lines.append("- No confidence scores recorded.")

    lines.extend(["", "## Low-Confidence Cases"])
    low_confidence_cases = _low_confidence_cases(
        extracted_contract=extracted_contract,
        final_findings=final_findings,
    )
    if low_confidence_cases:
        lines.extend(f"- {case}" for case in low_confidence_cases)
    else:
        lines.append("- No low-confidence cases detected.")

    lines.extend(["", "## Generated Artifacts"])
    lines.extend(_format_path_map(artifact_paths))
    lines.append("")

    audit_path.write_text("\n".join(lines), encoding="utf-8")


def _ordered_step_events(
    step_events: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return step events in stable workflow order."""
    by_step = {
        str(event.get("step")): event
        for event in step_events
        if isinstance(event, Mapping) and event.get("step")
    }
    return [by_step[step] for step in PIPELINE_STEP_ORDER if step in by_step]


def _format_metric(value: Any) -> str:
    """Format numeric metrics without unstable trailing precision."""
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    return str(value) if value is not None else "0"


def _format_count_map(values: Mapping[str, Any]) -> str:
    """Format a small count mapping for Markdown."""
    if not values:
        return "None"
    return ", ".join(f"{key}={values[key]}" for key in sorted(values))


def _format_path_map(paths: Mapping[str, Any]) -> list[str]:
    """Format path mappings as Markdown bullets."""
    if not paths:
        return ["- None recorded."]
    return [f"- {name}: {paths[name]}" for name in sorted(paths)]


def _format_confidence_distribution(
    name: str,
    distribution: Mapping[str, Any],
) -> list[str]:
    """Format one confidence distribution for Markdown."""
    return [
        f"### {name}",
        f"- Count: {distribution.get('count', 0)}",
        f"- Low: {distribution.get('low_count', 0)}",
        f"- Medium: {distribution.get('medium_count', 0)}",
        f"- High: {distribution.get('high_count', 0)}",
        f"- Min: {_format_metric(distribution.get('min_score'))}",
        f"- Max: {_format_metric(distribution.get('max_score'))}",
        f"- Average: {_format_metric(distribution.get('average_score'))}",
    ]


def _low_confidence_cases(
    extracted_contract: Mapping[str, Any],
    final_findings: Mapping[str, Any],
) -> list[str]:
    """Return human-readable low-confidence clause and finding summaries."""
    cases: list[str] = []
    clauses = extracted_contract.get("clauses")
    if isinstance(clauses, list):
        for clause in clauses:
            if not isinstance(clause, Mapping):
                continue
            score = confidence_value(clause.get("confidence_score", clause.get("confidence")))
            if score is None or score >= LOW_CONFIDENCE_AUDIT_THRESHOLD:
                continue
            clause_id = str(clause.get("clause_id") or "unknown_clause")
            clause_type = str(clause.get("clause_type") or "unknown_type")
            title = str(clause.get("title") or clause_type)
            cases.append(
                f"Clause {clause_id} ({clause_type}) confidence={score:.2f}: {title}"
            )

    findings = final_findings.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            score = confidence_value(finding.get("confidence"))
            issue_type = str(finding.get("issue_type") or "")
            is_low_confidence_issue = issue_type.startswith("low_confidence")
            if (
                not is_low_confidence_issue
                and (score is None or score >= LOW_CONFIDENCE_AUDIT_THRESHOLD)
            ):
                continue
            finding_id = str(finding.get("finding_id") or "unknown_finding")
            title = str(finding.get("title") or "Untitled finding")
            confidence_text = "unknown" if score is None else f"{score:.2f}"
            cases.append(
                f"Finding {finding_id} confidence={confidence_text}: {title}"
            )

    return cases


def _format_evidence_list(evidence_items: Sequence[EvidencePointer]) -> str:
    """Format evidence pointers for human-readable markdown."""
    formatted: list[str] = []
    for evidence in evidence_items:
        evidence_bits = [
            evidence.source_file,
            f"page {evidence.page_number}" if evidence.page_number else None,
            evidence.clause_reference,
            evidence.evidence_id,
        ]
        formatted.append(" | ".join(bit for bit in evidence_bits if bit))
    return "; ".join(value for value in formatted if value) or "No evidence pointer"

