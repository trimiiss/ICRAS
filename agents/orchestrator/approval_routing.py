"""Approval routing and exception triage for workflow orchestration."""

from typing import Any, Mapping, Optional, Sequence

from agents.orchestrator.errors import OrchestratorAgentError
from schemas.approval_packet import ApprovalRoute, ApprovalStatus
from schemas.common import Severity
from schemas.exception_triage import ExceptionTriageItem
from schemas.finding import Finding
from utils.collections import ordered_unique
from utils.mapping import as_mapping as _as_mapping
from utils.text import normalize_key as _normalize_key


def triage_findings(
    context: Mapping[str, Any],
    findings: Sequence[Finding],
    overall_severity: Severity,
) -> tuple[ApprovalStatus, list[ExceptionTriageItem]]:
    """Convert final findings into routed exceptions and an approval status."""
    approval_policy = _as_mapping(context.get("approval_policy"))
    auto_approve = _policy_allows_auto_approval(
        approval_policy=approval_policy,
        overall_severity=overall_severity,
    )
    if not findings:
        if auto_approve:
            _require_auto_approve_routing(approval_policy)
            return ApprovalStatus.AUTO_APPROVE, []
        return ApprovalStatus.ESCALATE, []

    rules = _exception_route_rules(approval_policy)
    exceptions: list[ExceptionTriageItem] = []
    for finding in findings:
        matched_rule = _match_exception_route_rule(finding, rules)
        if matched_rule is None:
            raise OrchestratorAgentError(
                "No exception routing rule matched finding "
                f"'{finding.finding_id}' "
                f"(issue_type={finding.issue_type or 'unknown'}, "
                f"field_name={finding.field_name or 'unknown'}). "
                "Update approval_policy.yaml exception_routing.rules."
            )
        exceptions.append(_build_exception_triage_item(finding, matched_rule))

    return ApprovalStatus.ESCALATE, exceptions


def build_approval_routes(
    context: Mapping[str, Any],
    exceptions: Sequence[ExceptionTriageItem],
    approval_status: ApprovalStatus,
    overall_severity: Severity,
) -> list[ApprovalRoute]:
    """Build grouped approval routes from per-exception triage items."""
    approval_policy = _as_mapping(context.get("approval_policy"))
    if approval_status == ApprovalStatus.AUTO_APPROVE:
        auto_route = _require_auto_approve_routing(approval_policy)
        return [
            ApprovalRoute(
                category=str(auto_route["category"]),
                approvers=[],
                reason=str(auto_route["reason"]),
                finding_ids=[],
            )
        ]

    route_data: dict[str, dict[str, Any]] = {}
    base_approvers = _severity_required_approvers(
        approval_policy=approval_policy,
        overall_severity=overall_severity,
    )
    for exception in exceptions:
        category = exception.category.value
        data = route_data.setdefault(
            category,
            {
                "approvers": [],
                "finding_ids": [],
                "reasons": [],
            },
        )
        data["finding_ids"].append(exception.finding_id)
        data["reasons"] = ordered_unique([*data["reasons"], exception.reason])
        data["approvers"] = ordered_unique(
            [
                *data["approvers"],
                exception.approver or "",
                *base_approvers,
            ]
        )

    return [
        ApprovalRoute(
            category=category,
            approvers=list(data["approvers"]),
            reason="; ".join(data["reasons"]),
            finding_ids=list(data["finding_ids"]),
        )
        for category, data in sorted(route_data.items())
    ]


def approval_rationale(
    approval_status: ApprovalStatus,
    overall_severity: Severity,
    findings: Sequence[Finding],
) -> str:
    """Create a deterministic approval rationale."""
    if approval_status == ApprovalStatus.AUTO_APPROVE:
        return "No routed exceptions were detected, so the contract can be auto-approved."
    return (
        f"{len(findings)} finding(s) require review. "
        f"The highest severity is {overall_severity.value}."
    )


def _policy_allows_auto_approval(
    approval_policy: Mapping[str, Any],
    overall_severity: Severity,
) -> bool:
    """Return whether the policy permits auto-approval for a severity level."""
    thresholds = _as_mapping(approval_policy.get("approval_thresholds"))
    severity_threshold = _as_mapping(thresholds.get(overall_severity.value))
    return bool(severity_threshold.get("auto_approve", False))


def _severity_required_approvers(
    approval_policy: Mapping[str, Any],
    overall_severity: Severity,
) -> list[str]:
    """Return policy approvers required for a severity level."""
    thresholds = _as_mapping(approval_policy.get("approval_thresholds"))
    severity_threshold = _as_mapping(thresholds.get(overall_severity.value))
    approvers = severity_threshold.get("required_approvers", [])
    if not isinstance(approvers, list):
        return []
    return ordered_unique(str(approver) for approver in approvers)


def _exception_route_rules(approval_policy: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return configured exception route rules or raise a clear error."""
    routing = _as_mapping(approval_policy.get("exception_routing"))
    rules = routing.get("rules")
    if not isinstance(rules, list) or not rules:
        raise OrchestratorAgentError(
            "approval_policy.yaml must define exception_routing.rules before "
            "The workflow orchestrator can route exceptions."
        )
    return [dict(rule) for rule in rules if isinstance(rule, Mapping)]


def _require_auto_approve_routing(approval_policy: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return configured auto-approval routing details or raise a clear error."""
    routing = _as_mapping(approval_policy.get("exception_routing"))
    auto_approve = _as_mapping(routing.get("auto_approve"))
    required_fields = ("category", "reason", "next_action")
    missing = [
        field
        for field in required_fields
        if not str(auto_approve.get(field) or "").strip()
    ]
    if missing:
        raise OrchestratorAgentError(
            "approval_policy.yaml exception_routing.auto_approve is missing: "
            + ", ".join(missing)
        )
    return auto_approve


def _match_exception_route_rule(
    finding: Finding,
    rules: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Return the first configured route rule that matches a finding."""
    for rule in rules:
        if _exception_route_rule_matches(finding, rule):
            return rule
    return None


def _exception_route_rule_matches(
    finding: Finding,
    rule: Mapping[str, Any],
) -> bool:
    """Return whether a configured route rule matches a finding."""
    checks: list[bool] = []
    issue_types = _normalized_policy_values(rule.get("match_issue_types"))
    if issue_types:
        checks.append(_normalize_key(finding.issue_type or "") in issue_types)

    field_names = _normalized_policy_values(rule.get("match_field_names"))
    if field_names:
        checks.append(_normalize_key(finding.field_name or "") in field_names)

    categories = _normalized_policy_values(rule.get("match_categories"))
    if categories:
        checks.append(_normalize_key(finding.category) in categories)

    text_fragments = _normalized_policy_values(rule.get("match_text"))
    if text_fragments:
        haystack = _normalize_key(
            " ".join(
                [
                    finding.category,
                    finding.field_name or "",
                    finding.issue_type or "",
                    finding.title,
                    finding.description,
                    finding.message or "",
                    finding.source_clause_text or "",
                ]
            )
        )
        checks.append(any(fragment in haystack for fragment in text_fragments))

    manual_review_required = rule.get("manual_review_required")
    if isinstance(manual_review_required, bool):
        checks.append(finding.manual_review_required is manual_review_required)

    max_confidence = rule.get("max_confidence")
    if isinstance(max_confidence, (int, float)):
        checks.append(float(finding.confidence) <= float(max_confidence))

    return bool(checks) and all(checks)


def _build_exception_triage_item(
    finding: Finding,
    rule: Mapping[str, Any],
) -> ExceptionTriageItem:
    """Build one schema-valid exception triage item from a matched rule."""
    return ExceptionTriageItem(
        finding_id=finding.finding_id,
        category=str(rule["category"]),
        approver=str(rule.get("approver") or ""),
        reason=str(rule["reason"]),
        next_action=str(rule["next_action"]),
        severity=finding.severity,
        evidence=list(finding.evidence),
        source_title=finding.title,
        issue_type=finding.issue_type,
        field_name=finding.field_name,
    )


def _normalized_policy_values(raw_values: Any) -> set[str]:
    """Normalize a policy list into comparable string keys."""
    if not isinstance(raw_values, list):
        return set()
    return {
        _normalize_key(str(value))
        for value in raw_values
        if str(value).strip()
    }


