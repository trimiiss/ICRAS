"""Public entry point for obligation register generation."""

from pathlib import Path
from typing import Any, Dict

from schemas.obligation_result import ObligationRegisterResult
from utils.artifacts import validate_run_dir
from utils.run_manager import append_audit_event

from .errors import ObligationRegisterError
from .extraction import coerce_clauses, extract_obligations
from .io import write_obligations_csv


def run_obligation_register(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
    run_dir: str | Path,
) -> Dict[str, Any]:
    """Generate ``obligations.csv`` from extracted contract clauses.

    Args:
        context: Context packet data from intake.
        extracted_contract: Extracted contract data from clause extraction.
        run_dir: Run directory where ``obligations.csv`` must be written.

    Returns:
        A dictionary containing the obligation register and artifact path.

    Raises:
        ObligationRegisterError: If inputs are malformed or CSV output fails.
    """
    run_path = validate_run_dir(
        run_dir,
        error_type=ObligationRegisterError,
        before_action="obligation extraction",
    )
    clauses = coerce_clauses(extracted_contract.get("clauses", []))
    run_id = str(context.get("run_id") or extracted_contract.get("run_id") or "unknown-run")

    obligations = extract_obligations(
        context=context,
        clauses=clauses,
    )
    register = ObligationRegisterResult(run_id=run_id, obligations=obligations)

    output_path = run_path / "obligations.csv"
    write_obligations_csv(output_path, register)
    append_audit_event(
        run_path,
        {
            "event": "obligation_register_completed",
            "agent": "obligation_agent",
            "message": "Generated the obligation register.",
            "artifacts": [output_path.name],
            "obligation_count": len(register.obligations),
        },
    )

    result = register.model_dump(mode="json")
    return {
        "obligation_register": result,
        "obligations": result["obligations"],
        "artifact_paths": {"obligations": str(output_path)},
    }
