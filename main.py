"""ICRAS command-line entry point.

Usage:
    python main.py --bundle data/bundles/clean_nda
    python main.py --bundle data/bundles/services_agreement
"""

import argparse
import sys
from typing import Any, Mapping

from agents.orchestrator_agent import OrchestratorAgentError, run_pipeline


def main() -> int:
    """Parse CLI arguments and run the Agent H LangGraph pipeline."""
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


def _print_run_summary(result: Mapping[str, Any]) -> None:
    """Print a compact run summary for CLI users."""
    artifact_paths = result.get("artifact_paths", {})
    metrics = result.get("metrics", {})
    approval_packet = result.get("approval_packet", {})
    decision = (
        approval_packet.get("decision", {})
        if isinstance(approval_packet, Mapping)
        else {}
    )

    print("\nRun completed successfully.")
    print(f"  Run ID  : {result.get('run_id', '')}")
    print(f"  Run Dir : {result.get('run_dir', '')}")
    print(f"  Status  : {_require_metrics_status(metrics)}")
    print(f"  Decision: {decision.get('status', '') if isinstance(decision, Mapping) else ''}")
    print("  Run artifacts:")
    if isinstance(artifact_paths, Mapping):
        for artifact_name in sorted(artifact_paths):
            print(f"    - {artifact_paths[artifact_name]}")


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
