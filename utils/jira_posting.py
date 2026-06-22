"""Jira posting utilities for ICRAS tracker integration."""

import base64
import json
import os
from typing import Any, Mapping, Optional, Protocol, Sequence
from urllib import error, request
from urllib.parse import urljoin, urlparse

from schemas.approval_packet import ApprovalPacket, ApprovalStatus
from schemas.jira_posting import (
    JiraIssueRequest,
    JiraIssueResult,
    JiraPostingConfig,
    JiraPostingResult,
    JiraPostingStatus,
)
from schemas.posting_payload import PostingPayload


class JiraClient(Protocol):
    """Minimal Jira client interface used by tests and production code."""

    def create_issue(
        self,
        config: JiraPostingConfig,
        issue_request: JiraIssueRequest,
    ) -> Mapping[str, Any]:
        """Create a Jira issue and return the decoded JSON response."""


class JiraPostingError(RuntimeError):
    """Raised when Jira issue creation fails."""


class UrllibJiraClient:
    """Small urllib-based Jira Cloud API client."""

    def create_issue(
        self,
        config: JiraPostingConfig,
        issue_request: JiraIssueRequest,
    ) -> Mapping[str, Any]:
        """Create one Jira issue through Jira Cloud REST API v3."""
        url = urljoin(config.base_url.rstrip("/") + "/", "rest/api/3/issue")
        payload = {
            "fields": {
                "project": {"key": issue_request.project_key},
                "summary": issue_request.summary,
                "description": issue_request.description,
                "issuetype": {"name": issue_request.issue_type},
            }
        }
        if issue_request.labels:
            payload["fields"]["labels"] = issue_request.labels

        body = json.dumps(payload).encode("utf-8")
        basic_auth = base64.b64encode(
            f"{config.email}:{config.api_token}".encode("utf-8")
        ).decode("ascii")
        http_request = request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=20) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise JiraPostingError(_safe_http_error(exc)) from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise JiraPostingError(f"Jira request failed: {reason}") from exc

        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise JiraPostingError("Jira returned a non-JSON response.") from exc
        if not isinstance(decoded, Mapping):
            raise JiraPostingError("Jira returned an unexpected response shape.")
        return decoded


def load_jira_config_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[JiraPostingConfig]:
    """Load Jira config from environment variables if all values are present."""
    source = os.environ if env is None else env
    values = {
        "base_url": str(source.get("JIRA_BASE_URL") or "").strip(),
        "email": str(source.get("JIRA_EMAIL") or "").strip(),
        "api_token": str(source.get("JIRA_API_TOKEN") or "").strip(),
        "project_key": str(source.get("JIRA_PROJECT_KEY") or "").strip(),
        "issue_type": str(source.get("JIRA_ISSUE_TYPE") or "Task").strip() or "Task",
    }
    if not all(
        values[name]
        for name in ("base_url", "email", "api_token", "project_key")
    ):
        return None
    return JiraPostingConfig(**values)


def build_jira_issue_request(
    posting_payload_data: Mapping[str, Any],
    approval_packet_data: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
    idempotency_result: Mapping[str, Any],
    project_key: str,
    issue_type: str = "Task",
) -> JiraIssueRequest:
    """Build a deterministic Jira issue request from final pipeline artifacts."""
    posting_payload = PostingPayload.model_validate(posting_payload_data)
    approval_packet = ApprovalPacket.model_validate(approval_packet_data)
    fingerprint = _optional_str(idempotency_result.get("input_fingerprint_sha256"))
    fingerprint_marker = (
        f"ICRAS_INPUT_FINGERPRINT={fingerprint}" if fingerprint else None
    )
    summary = (
        "ICRAS Review: "
        f"{posting_payload.contract.contract_type} - "
        f"{posting_payload.counterparty.name} - "
        f"{posting_payload.decision.status.value}"
    )
    sections = [
        (
            "Run",
            [
                f"Run ID: {posting_payload.run_id}",
                f"Contract ID: {posting_payload.contract.contract_id}",
                f"Bundle: {posting_payload.contract.bundle_name}",
                f"Counterparty: {posting_payload.counterparty.name}",
                f"Decision: {posting_payload.decision.status.value}",
            ],
        ),
        (
            "Risk Summary",
            [
                f"Overall severity: {posting_payload.risk.overall_severity.value}",
                posting_payload.risk.summary,
            ],
        ),
        (
            "Approval Route",
            _approval_route_lines(approval_packet),
        ),
        (
            "Exceptions And Evidence",
            _exception_lines(approval_packet),
        ),
        (
            "Artifacts",
            [f"{name}: {artifact_paths[name]}" for name in sorted(artifact_paths)],
        ),
        (
            "Idempotency",
            [
                f"External posting allowed: {posting_payload.external_posting_allowed}",
                f"Duplicate of run: {posting_payload.duplicate_of_run_id or 'None'}",
                f"Suppression reason: {posting_payload.posting_suppression_reason or 'None'}",
                fingerprint_marker or "ICRAS_INPUT_FINGERPRINT unavailable",
            ],
        ),
    ]
    return JiraIssueRequest(
        project_key=project_key,
        issue_type=issue_type,
        summary=summary,
        description=build_adf_document(sections),
        labels=_jira_labels(fingerprint),
        idempotency_key=fingerprint,
    )


def build_adf_document(
    sections: Sequence[tuple[str, Sequence[str]]],
) -> dict[str, Any]:
    """Build a simple deterministic Atlassian Document Format document."""
    content: list[dict[str, Any]] = []
    for title, lines in sections:
        clean_title = str(title).strip()
        if clean_title:
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": clean_title}],
                }
            )
        clean_lines = [str(line).strip() for line in lines if str(line).strip()]
        if not clean_lines:
            clean_lines = ["None"]
        for line in clean_lines:
            content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}],
                }
            )
    return {"type": "doc", "version": 1, "content": content}


def run_jira_posting(
    run_id: str,
    approval_packet_data: Mapping[str, Any],
    posting_payload_data: Mapping[str, Any],
    idempotency_result: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
    config: Optional[JiraPostingConfig] = None,
    client: Optional[JiraClient] = None,
) -> JiraPostingResult:
    """Create a Jira issue when the final decision requires tracker follow-up."""
    approval_packet = ApprovalPacket.model_validate(approval_packet_data)
    posting_payload = PostingPayload.model_validate(posting_payload_data)
    duplicate_guard = _duplicate_guard(posting_payload, idempotency_result)
    decision = approval_packet.decision.status

    if posting_payload.external_posting_allowed is False:
        return JiraPostingResult(
            run_id=run_id,
            status=JiraPostingStatus.SKIPPED,
            should_post=False,
            reason=posting_payload.posting_suppression_reason
            or "External posting suppressed by idempotency guard.",
            duplicate_guard=duplicate_guard,
        )

    if decision == ApprovalStatus.AUTO_APPROVE:
        return JiraPostingResult(
            run_id=run_id,
            status=JiraPostingStatus.SKIPPED,
            should_post=False,
            reason="Auto-approved contracts do not require Jira posting.",
            duplicate_guard=duplicate_guard,
        )

    active_config = config or load_jira_config_from_env()
    if active_config is None:
        return JiraPostingResult(
            run_id=run_id,
            status=JiraPostingStatus.DISABLED,
            should_post=False,
            reason="Jira posting disabled because required environment variables are missing.",
            duplicate_guard=duplicate_guard,
        )

    issue_request = build_jira_issue_request(
        posting_payload_data=posting_payload.model_dump(mode="json"),
        approval_packet_data=approval_packet.model_dump(mode="json"),
        artifact_paths=dict(artifact_paths),
        idempotency_result=idempotency_result,
        project_key=active_config.project_key,
        issue_type=active_config.issue_type,
    )
    request_summary = _safe_request_summary(active_config, issue_request)
    try:
        raw_result = (client or UrllibJiraClient()).create_issue(
            active_config,
            issue_request,
        )
        issue_key = str(raw_result.get("key") or "")
        if not issue_key:
            raise JiraPostingError("Jira response did not include an issue key.")
        issue_result = JiraIssueResult(
            issue_id=_optional_str(raw_result.get("id")),
            issue_key=issue_key,
            issue_url=f"{active_config.base_url.rstrip('/')}/browse/{issue_key}",
        )
    except Exception as exc:
        return JiraPostingResult(
            run_id=run_id,
            status=JiraPostingStatus.FAILED,
            should_post=True,
            reason="Jira issue creation failed.",
            duplicate_guard=duplicate_guard,
            request_summary=request_summary,
            error_message=_safe_error_message(
                exc,
                secrets=(active_config.api_token, active_config.email),
            ),
        )

    return JiraPostingResult(
        run_id=run_id,
        status=JiraPostingStatus.CREATED,
        should_post=True,
        reason="Jira issue created.",
        jira_issue_key=issue_result.issue_key,
        jira_issue_url=issue_result.issue_url,
        duplicate_guard=duplicate_guard,
        request_summary=request_summary,
    )


def _approval_route_lines(approval_packet: ApprovalPacket) -> list[str]:
    """Return concise approval-route lines for Jira."""
    if not approval_packet.approval_route:
        return ["No approval route recorded."]
    lines: list[str] = []
    for route in approval_packet.approval_route:
        approvers = ", ".join(route.approvers) if route.approvers else "None"
        findings = ", ".join(route.finding_ids) if route.finding_ids else "None"
        lines.append(
            f"{route.category}: approvers={approvers}; findings={findings}; reason={route.reason}"
        )
    return lines


def _exception_lines(approval_packet: ApprovalPacket) -> list[str]:
    """Return exception and evidence lines for Jira."""
    if not approval_packet.exceptions:
        return ["No routed exceptions."]
    lines: list[str] = []
    for exception in approval_packet.exceptions:
        lines.append(
            f"{exception.finding_id}: {exception.source_title} "
            f"({exception.severity.value}) -> {exception.next_action}"
        )
        for evidence in exception.evidence:
            source = evidence.source_file or "unknown source"
            page = f" page {evidence.page_number}" if evidence.page_number else ""
            excerpt = evidence.excerpt or "No excerpt recorded."
            lines.append(f"Evidence: {source}{page}: {excerpt}")
    return lines


def _jira_labels(fingerprint: Optional[str]) -> list[str]:
    """Return Jira-safe labels for traceability."""
    labels = ["icras"]
    if fingerprint:
        labels.append(f"icras-fingerprint-{fingerprint[:12]}")
    return labels


def _duplicate_guard(
    posting_payload: PostingPayload,
    idempotency_result: Mapping[str, Any],
) -> Optional[str]:
    """Return the active duplicate-prevention marker."""
    fingerprint = _optional_str(idempotency_result.get("input_fingerprint_sha256"))
    if fingerprint:
        return fingerprint
    return posting_payload.posting_suppression_reason


def _safe_request_summary(
    config: JiraPostingConfig,
    issue_request: JiraIssueRequest,
) -> dict[str, Any]:
    """Return a request summary that contains no secrets."""
    return {
        "jira_host": urlparse(config.base_url).netloc or config.base_url,
        "project_key": config.project_key,
        "issue_type": issue_request.issue_type,
        "summary": issue_request.summary,
        "labels": list(issue_request.labels),
        "idempotency_key": issue_request.idempotency_key,
    }


def _safe_http_error(exc: error.HTTPError) -> str:
    """Return a concise HTTP error message without response body secrets."""
    return f"Jira request failed with HTTP {exc.code}: {exc.reason}"


def _safe_error_message(
    exc: Exception,
    secrets: Sequence[str] = (),
) -> str:
    """Return a safe error message that does not expose credentials."""
    message = str(exc).replace("\n", " ")
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _optional_str(value: Any) -> Optional[str]:
    """Return a stripped string or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
