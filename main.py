"""ICRAS — Intelligent Contract Review and Approval System.

CLI entry point for the contract review pipeline.

Usage:
    python main.py --bundle data/bundles/clean_nda
    python main.py --bundle data/bundles/services_agreement
"""

import argparse
import sys
from pathlib import Path

from agents.counterparty_agent import CounterpartyAgentError, run_counterparty_check
from agents.extraction_agent import ExtractionAgentError, run_extraction
from agents.intake_agent import IntakeAgentError, run_intake
from agents.risk_agent import RiskAgentError, run_risk_assessment
from agents.validation_agent import ValidationAgentError, run_validation
from utils.bundle_loader import BundleLoadError, load_bundle
from utils.evidence_indexer import EvidenceIndexError, build_evidence_index
from utils.run_manager import append_audit_event, create_run_folder, update_run_status


def main() -> int:
    """Parse CLI arguments, validate the bundle, and create run artifacts.

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

    # --- Step 4: Build page-level evidence index for extraction ---
    try:
        evidence_result = build_evidence_index(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            run_id=run_info["run_id"],
            run_dir=run_info["run_dir"],
        )
    except EvidenceIndexError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "evidence_index_failed",
                "agent": "evidence_indexer",
                "message": "Evidence indexing failed before extraction could start.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Evidence indexing failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    # --- Step 5: Extract structured clauses from the primary contract ---
    try:
        extraction_result = run_extraction(
            bundle_data=bundle_data,
            document_inventory=intake_result["document_inventory"],
            evidence_index=evidence_result["evidence_index"],
            run_id=run_info["run_id"],
            run_dir=run_info["run_dir"],
        )
    except ExtractionAgentError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "extraction_failed",
                "agent": "extraction_agent",
                "message": "Extraction Agent failed before structured clauses were completed.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Extraction failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    # --- Step 6: Validate required contract fields deterministically ---
    try:
        validation_result = run_validation(
            context=intake_result["context_packet"],
            clauses=extraction_result["extracted_contract"]["clauses"],
            run_dir=run_info["run_dir"],
            evidence_index=evidence_result["evidence_index"],
        )
    except ValidationAgentError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "validation_failed",
                "agent": "validation_agent",
                "message": "Validation Agent failed before required artifacts were completed.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Validation failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    # --- Step 7: Score extracted clauses against risk policy ---
    try:
        risk_result = run_risk_assessment(
            context=intake_result["context_packet"],
            extracted_contract=extraction_result["extracted_contract"],
            validation_result=validation_result["validation_result"],
            run_dir=run_info["run_dir"],
        )
    except RiskAgentError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "risk_scoring_failed",
                "agent": "risk_agent",
                "message": "Agent E failed before clause analysis was completed.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Risk scoring failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    # --- Step 8: Resolve counterparty names against vendor master ---
    try:
        counterparty_result = run_counterparty_check(
            context=intake_result["context_packet"],
            extracted_contract=extraction_result["extracted_contract"],
            vendor_master_path=Path(bundle_data["bundle_dir"]) / "vendor_master.csv",
            run_dir=run_info["run_dir"],
            evidence_index=evidence_result["evidence_index"],
        )
    except CounterpartyAgentError as exc:
        error_message = str(exc)
        append_audit_event(
            run_info["run_dir"],
            {
                "event": "counterparty_resolution_failed",
                "agent": "counterparty_agent",
                "message": "Agent C failed before counterparty resolution was completed.",
                "error": error_message,
            },
        )
        update_run_status(run_info["run_dir"], "failed", error_message)
        print(f"ERROR: Counterparty resolution failed.\n  {exc}", file=sys.stderr)
        print(f"  Run Dir : {run_info['run_dir']}", file=sys.stderr)
        return 1

    print("\nRun created successfully.")
    print(f"  Run ID  : {run_info['run_id']}")
    print(f"  Run Dir : {run_info['run_dir']}")
    print(f"  Status  : {run_info['metadata']['status']}")
    print("  Run artifacts:")
    print(f"    - {intake_result['artifact_paths']['context_packet']}")
    print(f"    - {intake_result['artifact_paths']['document_inventory']}")
    print(f"    - {evidence_result['artifact_paths']['evidence_index']}")
    print(f"    - {extraction_result['artifact_paths']['extracted_contract']}")
    print(f"    - {validation_result['artifact_paths']['validation_findings']}")
    print(f"    - {risk_result['artifact_paths']['clause_analysis']}")
    print(f"    - {counterparty_result['artifact_paths']['counterparty_resolution']}")
    print(f"    - {run_info['run_dir']}\\audit_log.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())
