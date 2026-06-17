"""ICRAS command-line entry point.

Usage:
    python main.py --bundle data/bundles/clean_nda
    python main.py --bundle data/bundles/net90_services_agreement
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.orchestrator_agent import OrchestratorAgentError, run_pipeline


SUCCESS_MARK = "\u2705"
FAIL_MARK = "\u274c"


def main() -> int:
    """Parse CLI arguments and run the Agent H LangGraph pipeline."""
    _configure_console_encoding()
    parser = argparse.ArgumentParser(
        prog="icras",
        description="ICRAS - Intelligent Contract Review and Approval System",
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to the contract bundle folder (e.g. data/bundles/clean_nda).",
    )
    args = parser.parse_args()

    try:
        result = run_pipeline(args.bundle)
    except OrchestratorAgentError as exc:
        message = str(exc)
        if "load_bundle failed" in message:
            print(f"ERROR: Bundle validation failed.\n  {exc}", file=sys.stderr)
        else:
            print(f"ERROR: Pipeline failed.\n  {exc}", file=sys.stderr)
        return 1

    _print_run_summary(result)
    return 0


def _configure_console_encoding() -> None:
    """Use UTF-8 output so demo status symbols render on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _print_run_summary(result: Mapping[str, Any]) -> None:
    """Print a demo-ready run summary for CLI users."""
    artifact_paths = result.get("artifact_paths", {})
    metrics = result.get("metrics", {})
    approval_packet = result.get("approval_packet", {})
    decision = (
        approval_packet.get("decision", {})
        if isinstance(approval_packet, Mapping)
        else {}
    )
    status = decision.get("status", "") if isinstance(decision, Mapping) else ""

    print("\nICRAS demo run")
    print(f"Selected bundle: {_selected_bundle(result)}")
    print(f"Run ID: {result.get('run_id', '')}")
    print(f"Run directory: {result.get('run_dir', '')}")
    print(f"Pipeline status: {_require_metrics_status(metrics)}")

    print("\nAgent steps:")
    for event in _step_events(result.get("step_events")):
        marker = SUCCESS_MARK if event.get("status") == "completed" else FAIL_MARK
        step = str(event.get("step") or "unknown_step")
        agent = str(event.get("agent") or "unknown_agent")
        duration = event.get("duration_seconds")
        duration_text = _format_seconds(duration)
        print(f"  {marker} {step} ({agent}) - {duration_text}")

    print(f"\nFinal Decision: {_display_status(status)}")
    print("Approval Route:")
    _print_approval_route(approval_packet)

    print("\nGenerated artifact paths:")
    if isinstance(artifact_paths, Mapping):
        for artifact_name in sorted(artifact_paths):
            artifact_path = str(artifact_paths[artifact_name])
            marker = SUCCESS_MARK if Path(artifact_path).is_file() else FAIL_MARK
            print(f"  {marker} {artifact_name}: {artifact_path}")


def _selected_bundle(result: Mapping[str, Any]) -> str:
    """Return the selected bundle path from pipeline state."""
    bundle_path = result.get("bundle_path")
    if isinstance(bundle_path, str) and bundle_path:
        return str(Path(bundle_path).resolve())

    run_info = result.get("run_info")
    if isinstance(run_info, Mapping):
        metadata = run_info.get("metadata")
        if isinstance(metadata, Mapping):
            metadata_bundle = metadata.get("bundle_path")
            if isinstance(metadata_bundle, str) and metadata_bundle:
                return metadata_bundle
    return "(unknown bundle)"


def _step_events(value: Any) -> Sequence[Mapping[str, Any]]:
    """Return CLI-safe step event mappings."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [event for event in value if isinstance(event, Mapping)]


def _print_approval_route(approval_packet: Any) -> None:
    """Print grouped approval route details."""
    if not isinstance(approval_packet, Mapping):
        print("  - No approval packet generated.")
        return

    routes = approval_packet.get("approval_route", [])
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)):
        print("  - No approval route generated.")
        return
    if not routes:
        print("  - No approval route generated.")
        return

    for route in routes:
        if not isinstance(route, Mapping):
            continue
        category = str(route.get("category") or "UNKNOWN")
        approvers = route.get("approvers", [])
        reason = str(route.get("reason") or "No reason provided.")
        finding_ids = route.get("finding_ids", [])
        print(
            "  - "
            f"{_route_label(category)} ({category}): "
            f"{_format_approvers(approvers)}"
        )
        print(f"    Reason: {reason}")
        if isinstance(finding_ids, Sequence) and not isinstance(finding_ids, (str, bytes)):
            finding_text = ", ".join(str(finding_id) for finding_id in finding_ids)
            print(f"    Findings: {finding_text or 'None'}")


def _route_label(category: str) -> str:
    """Return a human-readable approval route label."""
    labels = {
        "AUTO_APPROVE": "Auto-approve",
        "LEGAL": "Legal review",
        "FINANCE": "Finance approval",
        "COMPLIANCE": "Compliance review",
        "MANUAL_REVIEW": "Manual review",
    }
    return labels.get(category, category.replace("_", " ").title())


def _format_approvers(approvers: Any) -> str:
    """Format route approvers for console output."""
    if not isinstance(approvers, Sequence) or isinstance(approvers, (str, bytes)):
        return "No human approver required"
    formatted = [str(approver) for approver in approvers if approver]
    if not formatted:
        return "No human approver required"
    return ", ".join(formatted)


def _display_status(status: Any) -> str:
    """Return a presenter-friendly decision status."""
    if not isinstance(status, str) or not status:
        return "(unknown)"
    return status.replace("_", "-")


def _format_seconds(value: Any) -> str:
    """Format elapsed seconds for step output."""
    if isinstance(value, (int, float)):
        return f"{float(value):.3f}s"
    return "duration unavailable"


def _require_metrics_status(metrics: Any) -> str:
    """Return metrics.status or raise a clear state-contract error."""
    if not isinstance(metrics, Mapping):
        raise RuntimeError(
            "Pipeline completed but metrics is missing or not a mapping."
        )
    status = metrics.get("status")
    if not isinstance(status, str) or not status:
        raise RuntimeError("Pipeline completed but metrics.status is missing.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
