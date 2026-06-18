"""LangChain showcase: generate a deterministic approval brief from run artifacts.

Usage:
    python examples/langchain_approval_brief.py --run-dir runs/<run_id> --trace
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import langsmith as ls
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda


DEFAULT_LANGSMITH_PROJECT = "icras-langchain-showcase"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
TRACE_TRUE_VALUES = {"1", "true", "yes", "on"}


def build_approval_brief_chain() -> Any:
    """Build the deterministic LangChain runnable used by the showcase."""
    load_artifacts = RunnableLambda(_load_run_artifacts).with_config(
        {"run_name": "load_icras_artifacts"}
    )
    select_facts = RunnableLambda(_select_approval_facts).with_config(
        {"run_name": "select_approval_facts"}
    )
    render_brief = RunnableLambda(_render_approval_brief).with_config(
        {"run_name": "render_approval_brief"}
    )
    return load_artifacts | select_facts | render_brief


def build_llm_approval_brief_chain(
    model: BaseChatModel | None = None,
    model_name: str | None = None,
) -> Any:
    """Build the LangChain runnable that drafts the brief with a chat model."""
    chat_model = model or _build_openai_chat_model(model_name)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                (
                    "You draft concise internal approval briefs for contract "
                    "review. Use only the provided ICRAS facts. Do not invent "
                    "approvers, findings, dates, or risk categories. Return "
                    "markdown with these sections: Executive Summary, Approval "
                    "Route, Key Findings, Recommended Next Step."
                ),
            ),
            (
                "user",
                (
                    "Write an approval brief from these ICRAS facts:\n\n"
                    "{facts_markdown}"
                ),
            ),
        ]
    ).with_config({"run_name": "prepare_approval_brief_prompt"})
    return (
        build_approval_facts_chain()
        | RunnableLambda(_facts_to_prompt_input).with_config(
            {"run_name": "format_llm_prompt_input"}
        )
        | prompt
        | chat_model.with_config({"run_name": "draft_approval_brief_with_llm"})
        | StrOutputParser().with_config({"run_name": "parse_llm_brief"})
    )


def build_approval_facts_chain() -> Any:
    """Build the artifact loading and fact selection runnable."""
    load_artifacts = RunnableLambda(_load_run_artifacts).with_config(
        {"run_name": "load_icras_artifacts"}
    )
    select_facts = RunnableLambda(_select_approval_facts).with_config(
        {"run_name": "select_approval_facts"}
    )
    return load_artifacts | select_facts


def generate_approval_brief(
    run_dir: str | Path,
    output_path: str | Path | None = None,
    *,
    trace: bool | None = None,
    project_name: str | None = None,
    use_llm: bool = False,
    model_name: str | None = None,
    model: BaseChatModel | None = None,
) -> dict[str, str]:
    """Generate an approval brief from an existing ICRAS run folder."""
    _load_optional_dotenv()
    run_path = Path(run_dir).resolve()
    target_path = (
        Path(output_path).resolve()
        if output_path is not None
        else run_path / "approval_brief.md"
    )
    trace_enabled = _resolve_trace_enabled(trace)
    trace_project = (
        project_name
        or os.getenv("LANGSMITH_PROJECT")
        or DEFAULT_LANGSMITH_PROJECT
    )
    config = {
        "run_name": "icras_approval_brief_showcase",
        "tags": ["icras", "langchain-showcase", "approval-brief"],
        "metadata": {
            "run_dir": str(run_path),
            "output_path": str(target_path),
            "trace_enabled": trace_enabled,
        },
    }

    with ls.tracing_context(
        enabled=trace_enabled,
        project_name=trace_project if trace_enabled else None,
    ):
        chain = (
            build_llm_approval_brief_chain(model=model, model_name=model_name)
            if use_llm
            else build_approval_brief_chain()
        )
        brief = chain.invoke(
            {"run_dir": str(run_path)},
            config=config,
        )

    target_path.write_text(str(brief), encoding="utf-8")
    return {
        "brief": str(brief),
        "output_path": str(target_path),
    }


def main() -> int:
    """Run the LangChain approval-brief showcase from the command line."""
    parser = argparse.ArgumentParser(
        prog="icras-langchain-approval-brief",
        description="Generate an approval brief from completed ICRAS run artifacts.",
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to a completed runs/<run_id> folder.",
    )
    parser.add_argument(
        "--output",
        help="Optional markdown output path. Defaults to <run-dir>/approval_brief.md.",
    )
    trace_group = parser.add_mutually_exclusive_group()
    trace_group.add_argument(
        "--trace",
        action="store_true",
        default=None,
        help="Enable LangSmith tracing for this showcase invocation.",
    )
    trace_group.add_argument(
        "--no-trace",
        action="store_false",
        dest="trace",
        help="Disable LangSmith tracing for this showcase invocation.",
    )
    parser.add_argument(
        "--project-name",
        help=(
            "LangSmith project name when tracing is enabled. Defaults to "
            f"{DEFAULT_LANGSMITH_PROJECT}."
        ),
    )
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument(
        "--llm",
        action="store_true",
        help="Draft the approval brief with an OpenAI chat model via LangChain.",
    )
    llm_group.add_argument(
        "--no-llm",
        action="store_false",
        dest="llm",
        help="Use deterministic markdown rendering without a model call.",
    )
    parser.set_defaults(llm=False)
    parser.add_argument(
        "--model",
        default=os.getenv("ICRAS_LANGCHAIN_MODEL", DEFAULT_OPENAI_MODEL),
        help=(
            "OpenAI model for --llm. Can also be set with ICRAS_LANGCHAIN_MODEL. "
            f"Defaults to {DEFAULT_OPENAI_MODEL}."
        ),
    )
    args = parser.parse_args()

    result = generate_approval_brief(
        run_dir=args.run_dir,
        output_path=args.output,
        trace=args.trace,
        project_name=args.project_name,
        use_llm=args.llm,
        model_name=args.model,
    )
    print(f"Approval brief written: {result['output_path']}")
    return 0


def _load_run_artifacts(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Load the run artifacts needed for the brief."""
    run_dir = Path(str(inputs.get("run_dir") or "")).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")
    return {
        "run_dir": str(run_dir),
        "context_packet": _read_optional_json(run_dir / "context_packet.json"),
        "approval_packet": _read_required_json(run_dir / "approval_packet.json"),
        "final_findings": _read_required_json(run_dir / "final_findings.json"),
        "metrics": _read_required_json(run_dir / "metrics.json"),
    }


def _select_approval_facts(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    """Select stable approval facts from ICRAS artifacts."""
    context = _as_mapping(artifacts.get("context_packet"))
    approval_packet = _as_mapping(artifacts.get("approval_packet"))
    final_findings = _as_mapping(artifacts.get("final_findings"))
    metrics = _as_mapping(artifacts.get("metrics"))
    decision = _as_mapping(approval_packet.get("decision"))
    routes = _as_sequence(approval_packet.get("approval_route"))
    findings = _as_sequence(final_findings.get("findings"))

    return {
        "run_id": str(
            approval_packet.get("run_id") or final_findings.get("run_id") or ""
        ),
        "bundle_name": str(context.get("bundle_name") or "(unknown bundle)"),
        "contract_type": str(
            context.get("contract_type") or "(unknown contract type)"
        ),
        "counterparty": str(
            context.get("counterparty") or "(unknown counterparty)"
        ),
        "decision_status": str(decision.get("status") or "(unknown)"),
        "decision_rationale": str(
            decision.get("rationale") or "No rationale available."
        ),
        "overall_severity": str(
            final_findings.get("overall_severity") or "(unknown)"
        ),
        "total_findings": int(final_findings.get("total_findings") or len(findings)),
        "exception_count": int(metrics.get("exception_count") or 0),
        "routes": [_route_summary(route) for route in routes[:5]],
        "top_findings": [_finding_summary(finding) for finding in findings[:5]],
    }


def _render_approval_brief(facts: Mapping[str, Any]) -> str:
    """Render a concise approval brief as markdown."""
    lines = [
        "# ICRAS Approval Brief",
        "",
        f"- Run ID: {facts['run_id']}",
        f"- Bundle: {facts['bundle_name']}",
        f"- Contract Type: {facts['contract_type']}",
        f"- Counterparty: {facts['counterparty']}",
        f"- Decision: {facts['decision_status']}",
        f"- Overall Severity: {facts['overall_severity']}",
        f"- Final Findings: {facts['total_findings']}",
        f"- Routed Exceptions: {facts['exception_count']}",
        "",
        "## Decision Rationale",
        str(facts["decision_rationale"]),
        "",
        "## Approval Route",
    ]
    routes = _as_sequence(facts.get("routes"))
    if routes:
        lines.extend(f"- {route}" for route in routes)
    else:
        lines.append("- No human approver required.")

    lines.extend(["", "## Top Findings"])
    top_findings = _as_sequence(facts.get("top_findings"))
    if top_findings:
        lines.extend(f"- {finding}" for finding in top_findings)
    else:
        lines.append("- No findings were detected.")

    lines.extend(
        [
            "",
            "## Presenter Note",
            (
                "This brief was generated with a LangChain Runnable pipeline from "
                "deterministic ICRAS artifacts. Enable LangSmith tracing with "
                "`--trace` to inspect each transformation step."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _facts_to_prompt_input(facts: Mapping[str, Any]) -> dict[str, str]:
    """Convert selected facts into prompt input for the LLM chain."""
    return {"facts_markdown": _render_approval_brief(facts)}


def _build_openai_chat_model(model_name: str | None) -> BaseChatModel:
    """Create the OpenAI chat model used by --llm mode."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is required when using --llm. "
            "Run with --no-llm for the deterministic offline showcase."
        )
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "langchain-openai is required for --llm mode. "
            "Install project dependencies with uv sync."
        ) from exc
    return ChatOpenAI(
        model=model_name or DEFAULT_OPENAI_MODEL,
        temperature=0,
    )


def _route_summary(route: Any) -> str:
    """Format one approval route for the brief."""
    route_data = _as_mapping(route)
    category = str(route_data.get("category") or "UNKNOWN")
    approvers = _as_sequence(route_data.get("approvers"))
    approver_text = ", ".join(str(approver) for approver in approvers) or "none"
    reason = str(route_data.get("reason") or "No reason provided.")
    return f"{category}: {approver_text} - {reason}"


def _finding_summary(finding: Any) -> str:
    """Format one finding for the brief."""
    finding_data = _as_mapping(finding)
    finding_id = str(finding_data.get("finding_id") or "finding")
    severity = str(finding_data.get("severity") or "UNKNOWN")
    title = str(finding_data.get("title") or "Untitled finding")
    field_name = str(finding_data.get("field_name") or "unknown field")
    issue_type = str(finding_data.get("issue_type") or "unknown issue")
    return f"{finding_id} [{severity}] {title} ({field_name}, {issue_type})"


def _read_required_json(path: Path) -> dict[str, Any]:
    """Read a required JSON mapping artifact."""
    if not path.is_file():
        raise FileNotFoundError(f"Required run artifact is missing: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path.name}.")
    return data


def _read_optional_json(path: Path) -> dict[str, Any]:
    """Read an optional JSON mapping artifact."""
    if not path.is_file():
        return {}
    return _read_required_json(path)


def _resolve_trace_enabled(trace: bool | None) -> bool:
    """Resolve tracing preference from CLI argument or environment."""
    if trace is not None:
        return trace
    return os.getenv("LANGSMITH_TRACING", "").strip().lower() in TRACE_TRUE_VALUES


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping or an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> Sequence[Any]:
    """Return a non-string sequence or an empty tuple."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _load_optional_dotenv() -> None:
    """Load .env when python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


if __name__ == "__main__":
    raise SystemExit(main())
