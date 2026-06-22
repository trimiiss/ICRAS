"""Tests for optional Jira posting integration."""

import base64
import json
from typing import Any, Mapping
from urllib import error

from schemas.jira_posting import JiraPostingConfig, JiraPostingStatus
from utils.jira_posting import (
    JiraPostingError,
    UrllibJiraClient,
    build_adf_document,
    build_jira_issue_request,
    load_jira_config_from_env,
    run_jira_posting,
)


class FakeJiraClient:
    """Capture Jira issue creation without making network calls."""

    def __init__(self, response: Mapping[str, Any] | None = None, exc: Exception | None = None):
        self.response = response or {"id": "10001", "key": "GEN-123"}
        self.exc = exc
        self.calls: list[tuple[JiraPostingConfig, object]] = []

    def create_issue(
        self,
        config: JiraPostingConfig,
        issue_request: object,
    ) -> Mapping[str, Any]:
        self.calls.append((config, issue_request))
        if self.exc is not None:
            raise self.exc
        return self.response


class FakeUrlopenResponse:
    """Context-manager response for urllib client tests."""

    def __init__(self, body: Mapping[str, Any]):
        self.body = json.dumps(dict(body)).encode("utf-8")

    def __enter__(self) -> "FakeUrlopenResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_load_jira_config_from_env_requires_all_values() -> None:
    """Partial Jira environment config should leave posting disabled."""
    assert load_jira_config_from_env({}) is None
    assert load_jira_config_from_env({"JIRA_BASE_URL": "https://example.atlassian.net"}) is None

    config = load_jira_config_from_env(
        {
            "JIRA_BASE_URL": "https://example.atlassian.net",
            "JIRA_EMAIL": "reviewer@example.com",
            "JIRA_API_TOKEN": "secret-token",
            "JIRA_PROJECT_KEY": "GEN",
        }
    )

    assert config is not None
    assert config.base_url == "https://example.atlassian.net"
    assert config.project_key == "GEN"
    assert config.issue_type == "Task"
    assert "secret-token" not in repr(config)


def test_load_jira_config_from_env_allows_custom_issue_type() -> None:
    """Jira issue type should be configurable while defaulting to Task."""
    config = load_jira_config_from_env(
        {
            "JIRA_BASE_URL": "https://example.atlassian.net",
            "JIRA_EMAIL": "reviewer@example.com",
            "JIRA_API_TOKEN": "secret-token",
            "JIRA_PROJECT_KEY": "GEN",
            "JIRA_ISSUE_TYPE": "Bug",
        }
    )

    assert config is not None
    assert config.issue_type == "Bug"


def test_urllib_jira_client_builds_expected_request(monkeypatch) -> None:
    """The real Jira client should construct the Jira Cloud request correctly."""
    captured: dict[str, Any] = {}

    def fake_urlopen(http_request, timeout):
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeUrlopenResponse({"id": "10001", "key": "GEN-123"})

    monkeypatch.setattr("utils.jira_posting.request.urlopen", fake_urlopen)
    issue_request = build_jira_issue_request(
        posting_payload_data=_posting_payload(),
        approval_packet_data=_approval_packet(),
        artifact_paths={"approval_packet": "/tmp/run/approval_packet.json"},
        idempotency_result={"input_fingerprint_sha256": "a" * 64},
        project_key="GEN",
        issue_type="Bug",
    )

    response = UrllibJiraClient().create_issue(_config(), issue_request)

    http_request = captured["request"]
    assert response == {"id": "10001", "key": "GEN-123"}
    assert captured["timeout"] == 20
    assert http_request.full_url == "https://example.atlassian.net/rest/api/3/issue"
    assert http_request.get_method() == "POST"
    expected_auth = base64.b64encode(
        b"reviewer@example.com:token"
    ).decode("ascii")
    assert http_request.get_header("Authorization") == f"Basic {expected_auth}"
    assert http_request.get_header("Accept") == "application/json"
    assert http_request.get_header("Content-type") == "application/json"

    body = json.loads(http_request.data.decode("utf-8"))
    fields = body["fields"]
    assert fields["project"]["key"] == "GEN"
    assert fields["summary"] == issue_request.summary
    assert fields["description"]["type"] == "doc"
    assert fields["issuetype"]["name"] == "Bug"
    assert fields["labels"] == issue_request.labels


def test_urllib_jira_client_maps_http_error_without_response_body(monkeypatch) -> None:
    """HTTP errors should expose status/reason, not raw Jira response bodies."""

    def fake_urlopen(http_request, timeout):
        raise error.HTTPError(
            url=http_request.full_url,
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr("utils.jira_posting.request.urlopen", fake_urlopen)
    issue_request = build_jira_issue_request(
        posting_payload_data=_posting_payload(),
        approval_packet_data=_approval_packet(),
        artifact_paths={},
        idempotency_result={"input_fingerprint_sha256": "a" * 64},
        project_key="GEN",
    )

    try:
        UrllibJiraClient().create_issue(_config(), issue_request)
    except JiraPostingError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected JiraPostingError")

    assert "HTTP 400" in message
    assert "Bad Request" in message
    assert "secret-token" not in message


def test_urllib_jira_client_maps_url_error(monkeypatch) -> None:
    """URL errors should become JiraPostingError."""

    def fake_urlopen(http_request, timeout):
        raise error.URLError("network unavailable")

    monkeypatch.setattr("utils.jira_posting.request.urlopen", fake_urlopen)
    issue_request = build_jira_issue_request(
        posting_payload_data=_posting_payload(),
        approval_packet_data=_approval_packet(),
        artifact_paths={},
        idempotency_result={"input_fingerprint_sha256": "a" * 64},
        project_key="GEN",
    )

    try:
        UrllibJiraClient().create_issue(_config(), issue_request)
    except JiraPostingError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected JiraPostingError")

    assert "network unavailable" in message


def test_build_adf_document_creates_valid_doc_for_empty_sections() -> None:
    """ADF output should stay valid even when optional sections are empty."""
    description = build_adf_document([("Summary", ["Line one"]), ("Empty", [])])

    assert description["type"] == "doc"
    assert description["version"] == 1
    text = _adf_text(description)
    assert "Summary" in text
    assert "Line one" in text
    assert "Empty" in text
    assert "None" in text


def test_build_jira_issue_request_includes_contract_route_evidence_and_marker() -> None:
    """Jira request should include the capstone-required follow-up content."""
    request = build_jira_issue_request(
        posting_payload_data=_posting_payload(),
        approval_packet_data=_approval_packet(),
        artifact_paths={"approval_packet": "/tmp/run/approval_packet.json"},
        idempotency_result={"input_fingerprint_sha256": "a" * 64},
        project_key="GEN",
    )

    assert request.project_key == "GEN"
    assert request.issue_type == "Task"
    assert request.summary == "ICRAS Review: Services Agreement - Acme Corp - ESCALATE"
    assert "icras-fingerprint-aaaaaaaaaaaa" in request.labels
    text = _adf_text(request.description)
    assert "Contract ID: services:DOC-001:contract.pdf" in text
    assert "LEGAL: approvers=legal_counsel" in text
    assert "Evidence: contract.pdf page 2" in text
    assert "ICRAS_INPUT_FINGERPRINT=" + ("a" * 64) in text


def test_auto_approved_run_skips_jira_without_client_call() -> None:
    """Auto-approved contracts should not create tracker noise."""
    client = FakeJiraClient()

    result = run_jira_posting(
        run_id="run-1",
        approval_packet_data=_approval_packet(status="AUTO_APPROVE", approved=True),
        posting_payload_data=_posting_payload(status="AUTO_APPROVE", approved=True),
        idempotency_result={"input_fingerprint_sha256": "a" * 64},
        artifact_paths={},
        config=_config(),
        client=client,
    )

    assert result.status == JiraPostingStatus.SKIPPED
    assert result.reason == "Auto-approved contracts do not require Jira posting."
    assert client.calls == []


def test_duplicate_run_skips_jira_without_client_call() -> None:
    """Idempotent duplicate reruns should not create duplicate Jira issues."""
    client = FakeJiraClient()

    result = run_jira_posting(
        run_id="run-2",
        approval_packet_data=_approval_packet(),
        posting_payload_data=_posting_payload(
            external_posting_allowed=False,
            posting_suppression_reason="Duplicate input fingerprint matched run-1.",
        ),
        idempotency_result={"input_fingerprint_sha256": "b" * 64},
        artifact_paths={},
        config=_config(),
        client=client,
    )

    assert result.status == JiraPostingStatus.SKIPPED
    assert "Duplicate input fingerprint" in result.reason
    assert client.calls == []


def test_missing_jira_config_disables_escalated_posting(monkeypatch) -> None:
    """Missing credentials should be visible but should not fail review."""
    for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
        monkeypatch.delenv(name, raising=False)

    result = run_jira_posting(
        run_id="run-3",
        approval_packet_data=_approval_packet(),
        posting_payload_data=_posting_payload(),
        idempotency_result={"input_fingerprint_sha256": "c" * 64},
        artifact_paths={},
        config=None,
        client=FakeJiraClient(),
    )

    assert result.status == JiraPostingStatus.DISABLED
    assert result.should_post is False
    assert "environment variables" in result.reason


def test_rejected_contract_creates_jira_issue_with_fake_client() -> None:
    """REJECT is explicit and post-worthy unless product decides otherwise."""
    client = FakeJiraClient()

    result = run_jira_posting(
        run_id="run-4",
        approval_packet_data=_approval_packet(status="REJECT"),
        posting_payload_data=_posting_payload(status="REJECT"),
        idempotency_result={"input_fingerprint_sha256": "d" * 64},
        artifact_paths={"approval_packet": "/tmp/run/approval_packet.json"},
        config=_config(),
        client=client,
    )

    assert result.status == JiraPostingStatus.CREATED
    assert result.should_post is True
    assert result.jira_issue_key == "GEN-123"
    assert result.jira_issue_url == "https://example.atlassian.net/browse/GEN-123"
    assert len(client.calls) == 1


def test_jira_failure_is_safe_and_does_not_expose_token() -> None:
    """Failed Jira calls should return a safe error result."""
    client = FakeJiraClient(exc=RuntimeError("401 Unauthorized for secret-token"))

    result = run_jira_posting(
        run_id="run-5",
        approval_packet_data=_approval_packet(),
        posting_payload_data=_posting_payload(),
        idempotency_result={"input_fingerprint_sha256": "e" * 64},
        artifact_paths={},
        config=_config(api_token="secret-token"),
        client=client,
    )

    assert result.status == JiraPostingStatus.FAILED
    assert result.should_post is True
    assert result.error_message is not None
    assert "401 Unauthorized" in result.error_message
    assert "secret-token" not in result.error_message
    assert "api_token" not in result.request_summary


def _config(api_token: str = "token") -> JiraPostingConfig:
    """Return a test Jira config."""
    return JiraPostingConfig(
        base_url="https://example.atlassian.net",
        email="reviewer@example.com",
        api_token=api_token,
        project_key="GEN",
    )


def _posting_payload(
    status: str = "ESCALATE",
    approved: bool = False,
    external_posting_allowed: bool = True,
    posting_suppression_reason: str | None = None,
) -> dict[str, Any]:
    """Return a valid CLM posting payload."""
    return {
        "run_id": "run-1",
        "contract": {
            "contract_id": "services:DOC-001:contract.pdf",
            "document_id": "DOC-001",
            "bundle_name": "services",
            "contract_type": "Services Agreement",
            "source_file": "contract.pdf",
            "jurisdiction": "Delaware, USA",
        },
        "counterparty": {
            "name": "Acme Corp",
            "resolution_status": "exact",
        },
        "decision": {
            "status": status,
            "approved": approved,
            "rationale": "High-severity finding requires review.",
            "requires_human_review": not approved,
        },
        "risk": {
            "overall_severity": "HIGH",
            "summary": "One high-severity finding requires legal review.",
            "final_finding_count": 1,
            "critical_finding_count": 0,
            "high_finding_count": 1,
            "categories": ["legal"],
            "findings": [
                {
                    "finding_id": "F-001",
                    "category": "legal",
                    "title": "Missing liability cap",
                    "description": "The contract does not include a liability cap.",
                    "severity": "HIGH",
                    "confidence": 0.95,
                    "evidence": [_evidence()],
                    "recommendation": "Add a liability cap or obtain legal approval.",
                    "field_name": "liability_cap",
                    "issue_type": "missing_field",
                }
            ],
        },
        "approval": {
            "approval_required": not approved,
            "routes": [
                {
                    "category": "LEGAL",
                    "approvers": ["legal_counsel"],
                    "reason": "Missing liability cap requires legal review.",
                    "finding_ids": ["F-001"],
                }
            ],
            "next_approvers": ["legal_counsel"],
        },
        "obligations": [],
        "artifacts": [],
        "artifact_references": {},
        "external_posting_allowed": external_posting_allowed,
        "posting_suppression_reason": posting_suppression_reason,
    }


def _approval_packet(status: str = "ESCALATE", approved: bool = False) -> dict[str, Any]:
    """Return a valid approval packet."""
    finding = {
        "finding_id": "F-001",
        "category": "legal",
        "title": "Missing liability cap",
        "description": "The contract does not include a liability cap.",
        "severity": "HIGH",
        "confidence": 0.95,
        "evidence": [_evidence()],
        "recommendation": "Add a liability cap or obtain legal approval.",
    }
    return {
        "run_id": "run-1",
        "decision": {
            "approved": approved,
            "status": status,
            "rationale": "High-severity finding requires review.",
        },
        "risk_result": {
            "overall_severity": "HIGH",
            "findings": [finding] if not approved else [],
            "requires_human_review": not approved,
            "summary": "One high-severity finding requires legal review.",
            "total_findings": 0 if approved else 1,
        },
        "approval_route": [
            {
                "category": "LEGAL",
                "approvers": ["legal_counsel"],
                "reason": "Missing liability cap requires legal review.",
                "finding_ids": ["F-001"],
            }
        ],
        "exceptions": [] if approved else [
            {
                "finding_id": "F-001",
                "category": "LEGAL",
                "approver": "legal_counsel",
                "reason": "Missing liability cap requires legal review.",
                "next_action": "Legal must approve or request remediation.",
                "severity": "HIGH",
                "evidence": [_evidence()],
                "source_title": "Missing liability cap",
            }
        ],
        "final_findings": [finding] if not approved else [],
        "artifact_paths": {},
    }


def _evidence() -> dict[str, Any]:
    """Return shared evidence data."""
    return {
        "source_file": "contract.pdf",
        "page_number": 2,
        "clause_reference": "8",
        "excerpt": "The agreement has no liability cap.",
    }


def _adf_text(document: Mapping[str, Any]) -> str:
    """Flatten text nodes from an ADF document."""
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if value.get("type") == "text" and value.get("text") is not None:
                values.append(str(value["text"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(document)
    return "\n".join(values)
