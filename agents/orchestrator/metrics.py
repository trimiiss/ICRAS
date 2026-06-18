"""Metrics and confidence summaries for orchestrated runs."""

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from agents.orchestrator.errors import OrchestratorAgentError
from agents.orchestrator.finding_merger import counterparty_findings
from schemas.common import Severity
from schemas.exception_triage import ExceptionTriageItem
from schemas.final_artifacts import ConfidenceDistribution, PipelineMetrics
from schemas.finding import Finding
from utils.mapping import as_mapping as _as_mapping


LOW_CONFIDENCE_AUDIT_THRESHOLD = 0.75


def build_metrics(
    state: Mapping[str, Any],
    status: str,
    overall_severity: Severity,
    final_finding_count: int,
    exceptions: Sequence[ExceptionTriageItem],
    final_findings: Sequence[Finding],
    determinism_result: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
) -> PipelineMetrics:
    """Build final pipeline metrics."""
    run_info = _require_state_mapping(state, "run_info")
    metadata = _as_mapping(run_info.get("metadata"))
    extracted_contract = _require_state_mapping(state, "extracted_contract")
    validation_result = _require_state_mapping(state, "validation_result")
    risk_result = _require_state_mapping(state, "risk_result")
    counterparty_resolution = _require_state_mapping(state, "counterparty_resolution")
    obligation_register = _require_state_mapping(state, "obligation_register")
    clauses = extracted_contract.get("clauses", [])
    clause_count = len(clauses) if isinstance(clauses, list) else 0
    duration_seconds = _duration_since(metadata.get("created_at"))
    clause_confidence_distribution = confidence_distribution(
        confidence_scores_from_clauses(clauses)
    )
    finding_confidence_distribution = confidence_distribution(
        confidence_scores_from_findings(final_findings)
    )
    exception_categories = exception_category_counts(exceptions)
    exception_count = len(exceptions)
    exception_rate = 0.0 if clause_count == 0 else round(exception_count / clause_count, 4)
    exception_rate_percent = round(exception_rate * 100, 2)
    throughput = (
        0.0 if duration_seconds <= 0.0 else round(clause_count / duration_seconds, 4)
    )
    accuracy_percent = (
        0.0
        if clause_confidence_distribution.average_score is None
        else round(clause_confidence_distribution.average_score * 100, 2)
    )
    return PipelineMetrics(
        run_id=_require_state_str(state, "run_id"),
        status=status,
        duration_seconds=duration_seconds,
        total_processing_time_seconds=duration_seconds,
        extraction_clause_count=clause_count,
        validation_finding_count=safe_len(validation_result.get("findings")),
        risk_finding_count=safe_len(risk_result.get("findings")),
        counterparty_exception_count=len(
            counterparty_findings(
                run_id=_require_state_str(state, "run_id"),
                context=_require_state_mapping(state, "context_packet"),
                counterparty_resolution=counterparty_resolution,
            )
        ),
        final_finding_count=final_finding_count,
        exception_count=exception_count,
        exception_categories=exception_categories,
        obligation_count=safe_len(obligation_register.get("obligations")),
        fallback_assisted=bool(extracted_contract.get("fallback_assisted")),
        fallback_reason=(
            str(extracted_contract.get("fallback_reason"))
            if extracted_contract.get("fallback_reason")
            else None
        ),
        low_confidence_count=(
            clause_confidence_distribution.low_count
            + finding_confidence_distribution.low_count
        ),
        confidence_distributions={
            "clauses": clause_confidence_distribution,
            "final_findings": finding_confidence_distribution,
        },
        throughput_clauses_per_second=throughput,
        accuracy_percent=accuracy_percent,
        exception_rate=exception_rate,
        exception_rate_percent=exception_rate_percent,
        determinism_check=str(determinism_result.get("determinism_check") or "FAIL"),
        determinism_baseline_run_id=_optional_str(
            determinism_result.get("determinism_baseline_run_id")
        ),
        determinism_compared_sections=[
            str(section)
            for section in determinism_result.get("determinism_compared_sections", [])
        ],
        determinism_excluded_timestamp_fields=[
            str(field)
            for field in determinism_result.get(
                "determinism_excluded_timestamp_fields",
                [],
            )
        ],
        determinism_differences=[
            str(difference)
            for difference in determinism_result.get("determinism_differences", [])
        ],
        overall_severity=overall_severity,
        artifact_paths=dict(artifact_paths),
    )


def exception_category_counts(raw_exceptions: Any) -> dict[str, int]:
    """Count routed exception categories from serialized or model exceptions."""
    if not isinstance(raw_exceptions, Sequence) or isinstance(raw_exceptions, (str, bytes)):
        return {}

    counts: dict[str, int] = {}
    for raw_exception in raw_exceptions:
        category: Any = None
        if isinstance(raw_exception, Mapping):
            category = raw_exception.get("category")
        elif hasattr(raw_exception, "category"):
            category = getattr(raw_exception, "category")
        if hasattr(category, "value"):
            category = category.value
        if not isinstance(category, str) or not category:
            category = "uncategorized"
        counts[category] = counts.get(category, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def confidence_scores_from_clauses(raw_clauses: Any) -> list[float]:
    """Collect normalized confidence scores from extracted clauses."""
    if not isinstance(raw_clauses, Sequence) or isinstance(raw_clauses, (str, bytes)):
        return []

    scores: list[float] = []
    for raw_clause in raw_clauses:
        if not isinstance(raw_clause, Mapping):
            continue
        score = confidence_value(raw_clause.get("confidence_score", raw_clause.get("confidence")))
        if score is not None:
            scores.append(score)
    return scores


def confidence_scores_from_findings(raw_findings: Any) -> list[float]:
    """Collect normalized confidence scores from final findings."""
    if not isinstance(raw_findings, Sequence) or isinstance(raw_findings, (str, bytes)):
        return []

    scores: list[float] = []
    for raw_finding in raw_findings:
        if isinstance(raw_finding, Mapping):
            score = confidence_value(raw_finding.get("confidence"))
        elif hasattr(raw_finding, "confidence"):
            score = confidence_value(getattr(raw_finding, "confidence"))
        else:
            score = None
        if score is not None:
            scores.append(score)
    return scores


def confidence_value(raw_value: Any) -> Optional[float]:
    """Return a valid confidence value or None."""
    try:
        score = float(raw_value)
    except (TypeError, ValueError):
        return None
    if score < 0.0 or score > 1.0:
        return None
    return score


def confidence_distribution(scores: Sequence[float]) -> ConfidenceDistribution:
    """Bucket confidence scores for metrics and audit output."""
    if not scores:
        return ConfidenceDistribution()

    clean_scores = [min(max(float(score), 0.0), 1.0) for score in scores]
    return ConfidenceDistribution(
        count=len(clean_scores),
        low_count=sum(score < LOW_CONFIDENCE_AUDIT_THRESHOLD for score in clean_scores),
        medium_count=sum(
            LOW_CONFIDENCE_AUDIT_THRESHOLD <= score < 0.9
            for score in clean_scores
        ),
        high_count=sum(score >= 0.9 for score in clean_scores),
        min_score=round(min(clean_scores), 4),
        max_score=round(max(clean_scores), 4),
        average_score=round(sum(clean_scores) / len(clean_scores), 4),
    )


def safe_len(value: Any) -> int:
    """Return len(value) when value is a list, else zero."""
    return len(value) if isinstance(value, list) else 0


def _duration_since(created_at: Any) -> float:
    """Return seconds since an ISO timestamp, or zero when missing."""
    if not isinstance(created_at, str) or not created_at:
        return 0.0
    try:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0.0, round((datetime.now(timezone.utc) - start).total_seconds(), 6))


def _require_state_str(state: Mapping[str, Any], key: str) -> str:
    """Return a required string from graph state."""
    value = state.get(key)
    if not isinstance(value, str) or not value:
        raise OrchestratorAgentError(
            f"Pipeline state is missing required string '{key}'."
        )
    return value


def _require_state_mapping(state: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return a required mapping from graph state."""
    value = state.get(key)
    if not isinstance(value, Mapping):
        raise OrchestratorAgentError(
            f"Pipeline state is missing required mapping '{key}'."
        )
    return dict(value)


def _optional_str(value: Any) -> Optional[str]:
    """Return value as a string when non-empty, else None."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return str(value)

