"""ICRAS — Intelligent Contract Review and Approval System.

CLI entry point for the contract review pipeline.

Usage:
    python main.py --bundle data/bundles/clean_nda
    python main.py --bundle data/bundles/services_agreement
"""

import argparse
import sys
from pathlib import Path

from utils.bundle_loader import BundleLoadError, load_bundle
from utils.run_manager import create_run_folder


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

    # --- Step 1: Load and validate the bundle ---
    try:
        bundle_data = load_bundle(bundle_path)
    except BundleLoadError as exc:
        print(f"ERROR: Bundle validation failed.\n  {exc}", file=sys.stderr)
        return 1

    manifest = bundle_data["manifest"]
    print(f"Bundle loaded: {manifest['bundle_name']}")
    print(f"  Contract type : {manifest['contract_type']}")
    print(f"  Counterparty  : {manifest['counterparty']}")
    print(f"  Jurisdiction  : {manifest['jurisdiction']}")

    # --- Step 2: Create a unique run folder ---
    run_info = create_run_folder(bundle_path)

    print(f"\nRun created successfully.")
    print(f"  Run ID  : {run_info['run_id']}")
    print(f"  Run Dir : {run_info['run_dir']}")
    print(f"  Status  : {run_info['metadata']['status']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
