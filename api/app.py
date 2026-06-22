"""FastAPI service for submitting contracts to ICRAS."""

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from agents.orchestrator import OrchestratorAgentError, run_pipeline
from api.bundle_builder import ContractUploadError, create_contract_bundle
from schemas.api_contract_review import (
    ContractReviewMetadata,
    ContractReviewResponse,
    UploadedFilePayload,
)
from utils.mapping import as_mapping


app = FastAPI(
    title="ICRAS Contract Review API",
    version="0.1.0",
)


@app.post("/contracts/review", response_model=ContractReviewResponse)
async def review_contract(
    contract_file: Annotated[UploadFile, File(description="Primary contract PDF.")],
    supporting_files: Annotated[
        list[UploadFile] | None,
        File(description="Optional bundle support files."),
    ] = None,
    bundle_name: Annotated[str | None, Form()] = None,
    contract_type: Annotated[str, Form()] = "Uploaded Contract",
    counterparty: Annotated[str, Form()] = "Unknown Counterparty",
    jurisdiction: Annotated[str, Form()] = "Unspecified",
    effective_date: Annotated[str | None, Form()] = None,
) -> ContractReviewResponse:
    """Upload a PDF, create a runtime bundle, run the pipeline, and return artifacts."""
    metadata = ContractReviewMetadata(
        bundle_name=bundle_name,
        contract_type=contract_type,
        counterparty=counterparty,
        jurisdiction=jurisdiction,
        effective_date=effective_date,
    )
    try:
        bundle_path = create_contract_bundle(
            contract_file=await _read_upload(contract_file),
            supporting_files=await _read_uploads(supporting_files or []),
            metadata=metadata,
        )
    except ContractUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = run_pipeline(str(bundle_path))
    except OrchestratorAgentError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline failed after bundle creation: {exc}",
        ) from exc

    return _build_response(result=result, bundle_path=str(bundle_path))


async def _read_upload(upload: UploadFile) -> UploadedFilePayload:
    """Read an UploadFile into a validated payload model."""
    content = await upload.read()
    return UploadedFilePayload(
        filename=upload.filename or "",
        content_type=upload.content_type,
        content=content,
    )


async def _read_uploads(uploads: list[UploadFile]) -> list[UploadedFilePayload]:
    """Read optional UploadFile values into payload models."""
    return [await _read_upload(upload) for upload in uploads]


def _build_response(
    result: dict[str, object],
    bundle_path: str,
) -> ContractReviewResponse:
    """Create the endpoint response from final pipeline state."""
    approval_packet = as_mapping(result.get("approval_packet"))
    decision = as_mapping(approval_packet.get("decision"))
    idempotency_result = as_mapping(result.get("idempotency_result"))
    jira_posting_result = as_mapping(result.get("jira_posting_result"))
    metrics = as_mapping(result.get("metrics"))
    return ContractReviewResponse(
        run_id=str(result.get("run_id") or ""),
        status=str(metrics.get("status") or "completed"),
        approval_status=(
            str(decision.get("status"))
            if decision.get("status") is not None
            else None
        ),
        idempotency_status=(
            str(idempotency_result.get("status"))
            if idempotency_result.get("status") is not None
            else None
        ),
        duplicate_of_run_id=(
            str(idempotency_result.get("baseline_run_id"))
            if idempotency_result.get("baseline_run_id") is not None
            else None
        ),
        external_posting_allowed=(
            bool(idempotency_result.get("external_posting_allowed"))
            if idempotency_result.get("external_posting_allowed") is not None
            else None
        ),
        jira_posting_status=(
            str(jira_posting_result.get("status"))
            if jira_posting_result.get("status") is not None
            else None
        ),
        jira_issue_key=(
            str(jira_posting_result.get("jira_issue_key"))
            if jira_posting_result.get("jira_issue_key") is not None
            else None
        ),
        jira_issue_url=(
            str(jira_posting_result.get("jira_issue_url"))
            if jira_posting_result.get("jira_issue_url") is not None
            else None
        ),
        bundle_path=bundle_path,
        artifact_paths={
            str(name): str(path)
            for name, path in as_mapping(result.get("artifact_paths")).items()
        },
    )
