"""Streamlit demo interface for the ICRAS contract review pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.orchestrator import OrchestratorAgentError, run_pipeline
from api.bundle_builder import ContractUploadError, create_contract_bundle
from schemas.api_contract_review import ContractReviewMetadata, UploadedFilePayload
from utils.mapping import as_mapping


PROJECT_ROOT = Path(__file__).resolve().parent
BUNDLES_DIR = PROJECT_ROOT / "data" / "bundles"
DEMO_SOURCE = "Demo bundle"
UPLOAD_SOURCE = "PDF upload"
ARTIFACT_ORDER = (
    "approval_packet",
    "posting_payload",
    "final_findings",
    "exceptions",
    "obligations",
    "metrics",
    "audit_log",
    "audit_log_jsonl",
)


def main() -> None:
    """Render and run the Streamlit demo app."""
    st = _load_streamlit()
    st.set_page_config(page_title="ICRAS Demo", layout="wide")
    st.title("ICRAS Contract Review")

    bundles = discover_bundles()
    with st.sidebar:
        st.header("Review")
        source = st.radio("Contract source", (DEMO_SOURCE, UPLOAD_SOURCE))
        selected_bundle = _render_bundle_selector(st, bundles, source)
        upload_state = _render_upload_fields(st, source)
        run_clicked = st.button("Run review", type="primary", use_container_width=True)

    if run_clicked:
        try:
            with st.spinner("Running contract review..."):
                result = run_review(
                    source=source,
                    selected_bundle=selected_bundle,
                    uploaded_file=upload_state["uploaded_file"],
                    supporting_files=upload_state["supporting_files"],
                    metadata=upload_state["metadata"],
                )
            st.session_state["last_result"] = result
        except (ContractUploadError, OrchestratorAgentError, ValueError) as exc:
            st.error(str(exc))

    result = st.session_state.get("last_result")
    if isinstance(result, Mapping):
        render_result(st, result)
    else:
        st.info("Select a contract and run the review.")


def discover_bundles(bundles_dir: Path = BUNDLES_DIR) -> list[Path]:
    """Return valid demo bundle directories sorted by bundle name."""
    if not bundles_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in bundles_dir.iterdir()
            if path.is_dir()
            and (path / "manifest.yaml").is_file()
            and (path / "contract.pdf").is_file()
        ),
        key=lambda path: path.name,
    )


def run_review(
    *,
    source: str,
    selected_bundle: Path | None,
    uploaded_file: Any,
    supporting_files: Sequence[Any],
    metadata: ContractReviewMetadata,
) -> dict[str, Any]:
    """Run the pipeline for a selected bundle or uploaded PDF."""
    if source == DEMO_SOURCE:
        if selected_bundle is None:
            raise ValueError("No demo bundle is available to run.")
        return run_pipeline(str(selected_bundle))

    if uploaded_file is None:
        raise ValueError("Upload a contract PDF before starting the review.")

    bundle_path = create_contract_bundle(
        contract_file=_uploaded_payload(uploaded_file),
        supporting_files=[_uploaded_payload(file) for file in supporting_files],
        metadata=metadata,
    )
    return run_pipeline(str(bundle_path))


def render_result(st: Any, result: Mapping[str, Any]) -> None:
    """Render final decision, routing, risks, artifacts, and audit log."""
    artifact_paths = {
        str(name): str(path)
        for name, path in as_mapping(result.get("artifact_paths")).items()
    }
    approval_packet = as_mapping(result.get("approval_packet"))
    decision = as_mapping(approval_packet.get("decision"))
    metrics = as_mapping(result.get("metrics"))

    st.subheader("Final Decision")
    status = str(decision.get("status") or "UNKNOWN")
    rationale = str(decision.get("rationale") or "No rationale was provided.")
    columns = st.columns(4)
    columns[0].metric("Decision", display_status(status))
    columns[1].metric("Pipeline", str(metrics.get("status") or "unknown"))
    columns[2].metric("Run ID", str(result.get("run_id") or "unknown"))
    columns[3].metric(
        "Findings",
        str(as_mapping(result.get("final_findings")).get("total_findings") or 0),
    )
    _render_decision_banner(st, status, rationale)

    st.subheader("Approval Routing")
    approval_rows = build_approval_rows(approval_packet)
    if approval_rows:
        st.dataframe(approval_rows, hide_index=True, use_container_width=True)
    else:
        st.info("No approval route was generated.")

    st.subheader("Detected Risks")
    risk_rows = build_risk_rows(result)
    if risk_rows:
        st.dataframe(risk_rows, hide_index=True, use_container_width=True)
    else:
        st.success("No risk findings were detected.")

    st.subheader("Contract Obligations")
    obligation_rows = build_obligation_rows(result)
    if obligation_rows:
        st.dataframe(obligation_rows, hide_index=True, use_container_width=True)
    else:
        st.info("No obligation records were extracted.")

    st.subheader("Artifacts")
    _render_artifact_downloads(st, artifact_paths)

    st.subheader("Audit Log")
    audit_info = read_audit_log_info(artifact_paths)
    event_count = audit_info.get("event_count", 0)
    st.caption(f"Events: {event_count} | {audit_info.get('path') or 'audit log unavailable'}")
    markdown = str(audit_info.get("markdown") or "")
    if markdown:
        with st.expander("audit_log.md", expanded=False):
            st.markdown(markdown)
    else:
        st.info("No audit log artifact is available for this run.")


def build_approval_rows(approval_packet: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build display rows from approval routing data."""
    routes = approval_packet.get("approval_route", [])
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)):
        return []

    rows: list[dict[str, str]] = []
    for route in routes:
        route_data = as_mapping(route)
        if not route_data:
            continue
        rows.append(
            {
                "category": str(route_data.get("category") or "UNKNOWN"),
                "approvers": _join_values(route_data.get("approvers")),
                "reason": str(route_data.get("reason") or ""),
                "findings": _join_values(route_data.get("finding_ids")),
            }
        )
    return rows


def build_risk_rows(result: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build display rows from final findings, falling back to the CLM payload."""
    final_findings = as_mapping(result.get("final_findings"))
    findings = final_findings.get("findings")
    if not isinstance(findings, list):
        posting_payload = as_mapping(result.get("posting_payload"))
        risk = as_mapping(posting_payload.get("risk"))
        findings = risk.get("findings", [])
    if not isinstance(findings, list):
        return []

    rows: list[dict[str, str]] = []
    for finding in findings:
        finding_data = as_mapping(finding)
        if not finding_data:
            continue
        rows.append(
            {
                "severity": str(finding_data.get("severity") or ""),
                "category": str(finding_data.get("category") or ""),
                "title": str(finding_data.get("title") or ""),
                "confidence": _format_confidence(finding_data.get("confidence")),
                "recommendation": str(finding_data.get("recommendation") or ""),
                "evidence": _evidence_label(finding_data.get("evidence")),
            }
        )
    return rows


def build_obligation_rows(result: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build display rows from obligation records in the CLM posting payload."""
    posting_payload = as_mapping(result.get("posting_payload"))
    obligations = posting_payload.get("obligations", [])
    if not isinstance(obligations, Sequence) or isinstance(obligations, (str, bytes)):
        return []

    rows: list[dict[str, str]] = []
    for obligation in obligations:
        obligation_data = as_mapping(obligation)
        if not obligation_data:
            continue
        due = str(obligation_data.get("due_date") or obligation_data.get("timing_trigger") or "")
        rows.append(
            {
                "id": str(obligation_data.get("obligation_id") or ""),
                "type": str(obligation_data.get("obligation_type") or ""),
                "party": str(obligation_data.get("responsible_party") or ""),
                "summary": str(obligation_data.get("obligation_summary") or ""),
                "due / trigger": due,
                "recurring": "Yes" if obligation_data.get("is_recurring") else "No",
            }
        )
    return rows


def read_audit_log_info(artifact_paths: Mapping[str, str]) -> dict[str, Any]:
    """Read audit-log artifacts for UI display."""
    markdown_path = Path(str(artifact_paths.get("audit_log") or ""))
    jsonl_path = Path(str(artifact_paths.get("audit_log_jsonl") or ""))
    markdown = _read_text(markdown_path)
    event_count = 0
    if jsonl_path.is_file():
        event_count = sum(1 for line in _read_text(jsonl_path).splitlines() if line.strip())
    return {
        "path": str(markdown_path) if markdown_path.is_file() else None,
        "markdown": markdown,
        "event_count": event_count,
    }


def ordered_artifact_items(artifact_paths: Mapping[str, str]) -> list[tuple[str, Path]]:
    """Return artifact paths in a presenter-friendly order."""
    items = {str(name): Path(str(path)) for name, path in artifact_paths.items()}
    ordered_names = [name for name in ARTIFACT_ORDER if name in items]
    ordered_names.extend(sorted(name for name in items if name not in ordered_names))
    return [(name, items[name]) for name in ordered_names]


def display_status(status: Any) -> str:
    """Return a human-friendly approval status."""
    if status is None:
        return "UNKNOWN"
    return str(status).replace("_", "-")


def _render_bundle_selector(st: Any, bundles: Sequence[Path], source: str) -> Path | None:
    """Render the demo bundle selector when bundle mode is active."""
    if source != DEMO_SOURCE:
        return None
    if not bundles:
        st.warning("No demo bundles were found.")
        return None
    labels = [bundle.name for bundle in bundles]
    default_index = labels.index("clean_nda") if "clean_nda" in labels else 0
    selected_label = st.selectbox("Bundle", labels, index=default_index)
    return bundles[labels.index(str(selected_label))]


def _render_upload_fields(st: Any, source: str) -> dict[str, Any]:
    """Render upload inputs and return upload state."""
    if source != UPLOAD_SOURCE:
        return {
            "uploaded_file": None,
            "supporting_files": [],
            "metadata": ContractReviewMetadata(),
        }

    uploaded_file = st.file_uploader("Contract PDF", type=["pdf"])
    supporting_files = st.file_uploader(
        "Supporting files",
        type=["csv", "yaml", "yml"],
        accept_multiple_files=True,
    )
    metadata = ContractReviewMetadata(
        bundle_name=st.text_input("Bundle name", value="streamlit_upload"),
        contract_type=st.text_input("Contract type", value="Uploaded Contract"),
        counterparty=st.text_input("Counterparty", value="Unknown Counterparty"),
        jurisdiction=st.text_input("Jurisdiction", value="Unspecified"),
        effective_date=st.text_input("Effective date", value="") or None,
    )
    return {
        "uploaded_file": uploaded_file,
        "supporting_files": supporting_files or [],
        "metadata": metadata,
    }


def _render_decision_banner(st: Any, status: str, rationale: str) -> None:
    """Render status-colored decision text."""
    message = f"{display_status(status)}: {rationale}"
    if status == "AUTO_APPROVE":
        st.success(message)
    elif status == "REJECT":
        st.error(message)
    else:
        st.warning(message)


def _render_artifact_downloads(st: Any, artifact_paths: Mapping[str, str]) -> None:
    """Render one download button per available artifact."""
    for name, path in ordered_artifact_items(artifact_paths):
        if not path.is_file():
            st.caption(f"{name}: missing ({path})")
            continue
        st.download_button(
            label=f"{name}: {path.name}",
            data=path.read_bytes(),
            file_name=path.name,
            mime=_mime_for_path(path),
            key=f"download-{name}-{path}",
        )


def _uploaded_payload(uploaded_file: Any) -> UploadedFilePayload:
    """Convert a Streamlit upload object into an API upload payload."""
    filename = str(getattr(uploaded_file, "name", "") or "contract.pdf")
    content_type = getattr(uploaded_file, "type", None)
    content = uploaded_file.getvalue()
    return UploadedFilePayload(
        filename=filename,
        content_type=content_type,
        content=content,
    )


def _format_confidence(value: Any) -> str:
    """Format a confidence score for a compact table cell."""
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return ""


def _evidence_label(evidence: Any) -> str:
    """Return a compact evidence label for a finding."""
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return ""
    if not evidence:
        return ""
    first = as_mapping(evidence[0])
    if not first:
        return ""
    page = first.get("page_number")
    clause = first.get("clause_reference")
    parts = [str(first.get("source_file") or "source")]
    if page is not None:
        parts.append(f"page {page}")
    if clause:
        parts.append(f"clause {clause}")
    return " ".join(parts)


def _join_values(value: Any) -> str:
    """Join list-like display values."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ""
    return ", ".join(str(item) for item in value if item)


def _read_text(path: Path) -> str:
    """Read a text file if it exists."""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return ""


def _mime_for_path(path: Path) -> str:
    """Return a reasonable download MIME type for an artifact."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".jsonl":
        return "application/x-ndjson"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


def _load_streamlit() -> Any:
    """Import Streamlit lazily so helper tests do not require the UI package."""
    try:
        import streamlit as st
    except ImportError as exc:
        raise RuntimeError(
            "Streamlit is required for the demo UI. Install dependencies and run "
            "`streamlit run streamlit_app.py`."
        ) from exc
    return st


if __name__ == "__main__":
    main()