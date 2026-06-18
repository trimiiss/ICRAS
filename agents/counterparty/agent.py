"""Counterparty matching public entry point."""

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from schemas.counterparty_result import CounterpartyMatch, CounterpartyResolution
from agents.counterparty.errors import CounterpartyAgentError
from agents.counterparty.evidence import extract_evidence_records
from agents.counterparty.matching import normalize_name, resolve_party
from agents.counterparty.parties import extract_party_names
from agents.counterparty.vendor_master import load_vendor_master
from utils.artifacts import validate_run_dir, write_model_json
from utils.run_manager import append_audit_event


def run_counterparty_check(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
    vendor_master_path: str | Path,
    run_dir: str | Path | None = None,
    evidence_index: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve contract party names against the vendor master.

    Args:
        context: Context packet from the Intake Agent.
        extracted_contract: The ``extracted_contract`` dict (with ``clauses``).
        vendor_master_path: Path to ``vendor_master.csv``.
        run_dir: Optional run directory for writing ``counterparty_resolution.json``.
        evidence_index: Optional page-level evidence index for source pointers.

    Returns:
        A dictionary containing the resolution result and artifact paths.

    Raises:
        CounterpartyAgentError: If inputs are malformed or the artifact cannot be saved.
    """
    run_path = (
        validate_run_dir(
            run_dir,
            error_type=CounterpartyAgentError,
            before_action="running counterparty resolution",
            trailing_period=True,
        )
        if run_dir is not None
        else None
    )
    vendor_records = load_vendor_master(vendor_master_path)
    party_names = extract_party_names(context, extracted_contract)
    evidence_records = extract_evidence_records(evidence_index)

    run_id = str(context.get("run_id") or "unknown-run")

    matches: list[CounterpartyMatch] = []
    for party_name in party_names:
        match = resolve_party(
            party_name=party_name,
            vendor_records=vendor_records,
            context=context,
            evidence_records=evidence_records,
        )
        matches.append(match)

    resolution = CounterpartyResolution(
        run_id=run_id,
        matches=matches,
    )

    artifact_paths: dict[str, str] = {}
    if run_path is not None:
        output_path = run_path / "counterparty_resolution.json"
        write_model_json(
            output_path,
            resolution,
            error_type=CounterpartyAgentError,
            failure_message=(
                "Failed to write counterparty resolution artifact to "
                "{path}: {exc}"
            ),
            default=str,
            ensure_ascii=True,
            trailing_newline=False,
        )
        append_audit_event(
            run_path,
            {
                "event": "counterparty_resolution_completed",
                "agent": "counterparty_agent",
                "message": "Counterparty matching resolved contract party names against vendor master.",
                "artifacts": [output_path.name],
                "match_count": len(matches),
                "flagged_count": sum(
                    1 for match in matches if match.manual_review_required or match.risk_flag
                ),
            },
        )
        artifact_paths["counterparty_resolution"] = str(output_path)

    result = resolution.model_dump(mode="json")
    return {
        "counterparty_resolution": result,
        "matches": result["matches"],
        "artifact_paths": artifact_paths,
    }


__all__ = [
    "CounterpartyAgentError",
    "normalize_name",
    "run_counterparty_check",
]
