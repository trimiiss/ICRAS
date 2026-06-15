"""ICRAS — Intelligent Contract Review and Approval System.

CLI entry point for the contract review pipeline.

Usage:
    python main.py --bundle data/bundles/clean_nda
    python main.py --bundle data/bundles/services_agreement
"""

import argparse
import sys

from agents.intake_agent import IntakeAgentError, run_intake
from utils.bundle_loader import BundleLoadError, load_bundle
from utils.run_manager import append_audit_event, create_run_folder, update_run_status


def main() -> int:
    """Parse CLI arguments, validate the bundle, and create a run folder.

    Returns:
        0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        prog="icras",
        description="ICRAS — Intelligent Contract Review and Approval System",
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to the contract bundle folder (e.g. data/bundles/clean_nda).",
    )

    args = parser.parse_args()
    bundle_path = args.bundle

    # --- Step 1: Create a unique run folder for this attempt ---
    run_info = create_run_folder(bundle_path)

    # --- Step 2: Load and validate the bundle ---
    try:
        bundle_data = load_bundle(bundle_path)
    except BundleLoadError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "bundle_validation_failed",
                "agent": "intake_agent",
                "message": "Bundle validation failed before intake artifacts were created.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Bundle validation failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    manifest = bundle_data["manifest"]
    print(f"Bundle loaded: {manifest['bundle_name']}")
    print(f"  Contract type : {manifest['contract_type']}")
    print(f"  Counterparty  : {manifest['counterparty']}")
    print(f"  Jurisdiction  : {manifest['jurisdiction']}")

    # --- Step 3: Run intake and create initial artifacts ---
    try:
        intake_result = run_intake(
            bundle_data=bundle_data,
            run_id=run_info["run_id"],
            run_dir=run_info["run_dir"],
        )
    except IntakeAgentError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "intake_failed",
                "agent": "intake_agent",
                "message": "Intake Agent failed before required artifacts were completed.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Intake failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    print("\nRun created successfully.")
    print(f"  Run ID  : {run_info['run_id']}")
    print(f"  Run Dir : {run_info['run_dir']}")
    print(f"  Status  : {run_info['metadata']['status']}")
    print("  Intake artifacts:")
    print(f"    - {intake_result['artifact_paths']['context_packet']}")
    print(f"    - {intake_result['artifact_paths']['document_inventory']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
