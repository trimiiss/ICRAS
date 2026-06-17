"""End-to-end demo scenario coverage for representative contracts."""

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from agents.orchestrator_agent import run_pipeline


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_ROOT = PROJECT_ROOT / "data" / "bundles"
SCENARIO_BUNDLES = sorted(SCENARIO_ROOT.glob("scenario_*"))


def _scenario_id(bundle_path: Path) -> str:
    """Return a readable pytest ID for one scenario bundle."""
    return bundle_path.name


@pytest.mark.parametrize("bundle_path", SCENARIO_BUNDLES, ids=_scenario_id)
def test_demo_scenario_produces_expected_decision(
    bundle_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each representative bundle should route to its expected decision."""
    monkeypatch.chdir(tmp_path)
    expected = _read_expected_output(bundle_path)

    result = run_pipeline(str(bundle_path))
    observed = _observed_decision(result)

    errors = _scenario_mismatches(expected, observed)
    assert not errors, (
        f"Scenario {bundle_path.name} did not produce the expected outcome.\n"
        f"Mismatches: {json.dumps(errors, indent=2, sort_keys=True)}\n"
        f"Expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"Observed: {json.dumps(observed, indent=2, sort_keys=True)}"
    )


def test_all_required_demo_scenarios_exist() -> None:
    """The demo suite must include all nine required scenario bundles."""
    assert len(SCENARIO_BUNDLES) == 9
    for bundle_path in SCENARIO_BUNDLES:
        for filename in (
            "contract.pdf",
            "manifest.yaml",
            "expected_output.json",
        ):
            assert (bundle_path / filename).is_file(), (
                f"{bundle_path.name} is missing {filename}"
            )


def _read_expected_output(bundle_path: Path) -> Mapping[str, Any]:
    """Read expected_output.json for one scenario."""
    expected_path = bundle_path / "expected_output.json"
    with expected_path.open("r", encoding="utf-8") as file:
        expected = json.load(file)
    if not isinstance(expected, Mapping):
        raise AssertionError(f"{expected_path} must contain a JSON object.")
    return expected


def _observed_decision(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return comparable decision details from a pipeline result."""
    approval_packet = _as_mapping(result.get("approval_packet"))
    decision = _as_mapping(approval_packet.get("decision"))
    approval_routes = approval_packet.get("approval_route", [])
    exceptions = approval_packet.get("exceptions", [])

    route_categories = sorted(
        {
            str(route.get("category"))
            for route in approval_routes
            if isinstance(route, Mapping) and route.get("category")
        }
    )
    exception_categories = sorted(
        {
            str(exception.get("category"))
            for exception in exceptions
            if isinstance(exception, Mapping) and exception.get("category")
        }
    )
    approvers = sorted(
        {
            str(approver)
            for route in approval_routes
            if isinstance(route, Mapping)
            for approver in route.get("approvers", [])
            if approver
        }
        | {
            str(exception.get("approver"))
            for exception in exceptions
            if isinstance(exception, Mapping) and exception.get("approver")
        }
    )
    issue_types = sorted(
        {
            str(exception.get("issue_type"))
            for exception in exceptions
            if isinstance(exception, Mapping) and exception.get("issue_type")
        }
    )

    return {
        "approval_status": decision.get("status"),
        "approved": decision.get("approved"),
        "route_categories": route_categories,
        "exception_categories": exception_categories,
        "approvers": approvers,
        "issue_types": issue_types,
    }


def _scenario_mismatches(
    expected: Mapping[str, Any],
    observed: Mapping[str, Any],
) -> list[str]:
    """Return clear, field-level scenario mismatches."""
    mismatches: list[str] = []
    expected_status = expected.get("expected_approval_status")
    if observed.get("approval_status") != expected_status:
        mismatches.append(
            "approval_status expected "
            f"{expected_status!r}, observed {observed.get('approval_status')!r}"
        )

    expected_category = expected.get("expected_exception_category")
    observed_categories = set(observed.get("exception_categories", [])) | set(
        observed.get("route_categories", [])
    )
    if expected_category and expected_category not in observed_categories:
        mismatches.append(
            "category expected "
            f"{expected_category!r}, observed {sorted(observed_categories)!r}"
        )

    expected_approver = expected.get("expected_approver")
    observed_approvers = set(observed.get("approvers", []))
    if expected_approver and expected_approver not in observed_approvers:
        mismatches.append(
            "approver expected "
            f"{expected_approver!r}, observed {sorted(observed_approvers)!r}"
        )

    expected_issue_type = expected.get("expected_issue_type")
    observed_issue_types = set(observed.get("issue_types", []))
    if expected_issue_type and expected_issue_type not in observed_issue_types:
        mismatches.append(
            "issue_type expected "
            f"{expected_issue_type!r}, observed {sorted(observed_issue_types)!r}"
        )

    return mismatches


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Return a mapping or an empty mapping."""
    return value if isinstance(value, Mapping) else {}
