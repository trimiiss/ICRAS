"""Tests for the LangChain approval brief showcase."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from examples.langchain_approval_brief import (
    build_llm_approval_brief_chain,
    generate_approval_brief,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_generate_approval_brief_writes_deterministic_markdown(tmp_path: Path) -> None:
    """The showcase should turn existing run artifacts into a stable brief."""
    run_dir = _sample_run_dir(tmp_path)

    result = generate_approval_brief(run_dir=run_dir, trace=False)

    output_path = run_dir / "approval_brief.md"
    assert result["output_path"] == str(output_path)
    assert output_path.is_file()
    content = output_path.read_text(encoding="utf-8")
    assert "# ICRAS Approval Brief" in content
    assert "Decision: ESCALATE" in content
    assert "Finance approval needed" in content
    assert "FND-001 [HIGH] Payment terms exceed standard" in content
    assert "LangChain Runnable pipeline" in content


def test_langchain_approval_brief_cli_runs_offline(tmp_path: Path) -> None:
    """The CLI example should run without requiring LangSmith credentials."""
    run_dir = _sample_run_dir(tmp_path)
    output_path = tmp_path / "brief.md"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "examples" / "langchain_approval_brief.py"),
            "--run-dir",
            str(run_dir),
            "--output",
            str(output_path),
            "--no-trace",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0
    assert "Approval brief written:" in result.stdout
    assert output_path.is_file()
    assert "Routed Exceptions: 1" in output_path.read_text(encoding="utf-8")


def test_generate_approval_brief_can_use_llm_chain_with_fake_model(
    tmp_path: Path,
) -> None:
    """LLM mode should support LangChain chat models without network in tests."""
    run_dir = _sample_run_dir(tmp_path)
    model = FakeListChatModel(responses=["# LLM Brief\n\nFinance should review."])

    result = generate_approval_brief(
        run_dir=run_dir,
        trace=False,
        use_llm=True,
        model=model,
    )

    assert "# LLM Brief" in result["brief"]
    assert "Finance should review." in (run_dir / "approval_brief.md").read_text(
        encoding="utf-8"
    )


def test_llm_mode_requires_openai_api_key_when_no_model_is_injected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real OpenAI LLM mode should fail clearly without credentials."""
    run_dir = _sample_run_dir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is required"):
        generate_approval_brief(run_dir=run_dir, trace=False, use_llm=True)


def test_build_llm_chain_accepts_fake_model(tmp_path: Path) -> None:
    """The LLM chain should be a normal LangChain runnable."""
    run_dir = _sample_run_dir(tmp_path)
    chain = build_llm_approval_brief_chain(
        model=FakeListChatModel(responses=["brief"])
    )

    assert chain.invoke({"run_dir": str(run_dir)}) == "brief"


def _sample_run_dir(tmp_path: Path) -> Path:
    """Create a minimal completed run folder for the showcase."""
    run_dir = tmp_path / "runs" / "sample-run"
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "context_packet.json",
        {
            "run_id": "sample-run",
            "bundle_name": "net90_services_agreement",
            "contract_type": "Services Agreement",
            "counterparty": "Acme Corporation",
        },
    )
    _write_json(
        run_dir / "approval_packet.json",
        {
            "run_id": "sample-run",
            "decision": {
                "approved": False,
                "status": "ESCALATE",
                "rationale": "1 finding requires review.",
            },
            "approval_route": [
                {
                    "category": "FINANCE",
                    "approvers": ["finance_manager"],
                    "reason": "Finance approval needed.",
                    "finding_ids": ["FND-001"],
                }
            ],
            "exceptions": [{"finding_id": "FND-001"}],
        },
    )
    _write_json(
        run_dir / "final_findings.json",
        {
            "run_id": "sample-run",
            "overall_severity": "HIGH",
            "total_findings": 1,
            "findings": [
                {
                    "finding_id": "FND-001",
                    "severity": "HIGH",
                    "title": "Payment terms exceed standard",
                    "field_name": "payment_terms",
                    "issue_type": "payment_terms_exceed_standard",
                }
            ],
        },
    )
    _write_json(
        run_dir / "metrics.json",
        {
            "run_id": "sample-run",
            "status": "completed",
            "exception_count": 1,
        },
    )
    return run_dir


def _write_json(path: Path, data: dict) -> None:
    """Write a JSON test fixture."""
    path.write_text(json.dumps(data), encoding="utf-8")
