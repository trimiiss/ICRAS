"""Determinism checks for repeated ICRAS pipeline runs."""

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from utils.mapping import as_mapping as _as_mapping

DETERMINISM_COMPARED_SECTIONS: tuple[str, ...] = (
    "risk_result",
    "approval_decision",
)
DETERMINISM_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "created_at",
    "updated_at",
    "reviewed_at",
    "timestamp",
    "started_at",
    "finished_at",
)


def build_determinism_result(
    current_run_dir: Path,
    current_run_id: str,
    bundle_path: str,
    current_risk_result: Mapping[str, Any],
    current_approval_decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare current risk and decision outputs to the latest same-bundle run."""
    baseline_run_dir = _find_previous_completed_run(
        current_run_dir=current_run_dir,
        current_run_id=current_run_id,
        bundle_path=bundle_path,
    )
    if baseline_run_dir is None:
        return compare_determinism_payloads(
            baseline_payload={
                "risk_result": current_risk_result,
                "approval_decision": current_approval_decision,
            },
            current_payload={
                "risk_result": current_risk_result,
                "approval_decision": current_approval_decision,
            },
            baseline_run_id=None,
        )

    baseline_packet = _load_json_mapping(baseline_run_dir / "approval_packet.json")
    baseline_metadata = _load_json_mapping(baseline_run_dir / "metadata.json")
    return compare_determinism_payloads(
        baseline_payload={
            "risk_result": _as_mapping(baseline_packet.get("risk_result")),
            "approval_decision": _as_mapping(baseline_packet.get("decision")),
        },
        current_payload={
            "risk_result": current_risk_result,
            "approval_decision": current_approval_decision,
        },
        baseline_run_id=str(
            baseline_metadata.get("run_id") or baseline_run_dir.name
        ),
    )


def compare_determinism_payloads(
    baseline_payload: Mapping[str, Any],
    current_payload: Mapping[str, Any],
    baseline_run_id: Optional[str],
) -> dict[str, Any]:
    """Compare deterministic output sections while ignoring timestamp fields."""
    differences: list[str] = []
    for section in DETERMINISM_COMPARED_SECTIONS:
        baseline_section = _strip_determinism_ignored_fields(
            baseline_payload.get(section)
        )
        current_section = _strip_determinism_ignored_fields(
            current_payload.get(section)
        )
        _collect_determinism_differences(
            path=section,
            baseline=baseline_section,
            current=current_section,
            differences=differences,
        )

    return {
        "determinism_check": "PASS" if not differences else "FAIL",
        "determinism_baseline_run_id": baseline_run_id,
        "determinism_compared_sections": list(DETERMINISM_COMPARED_SECTIONS),
        "determinism_excluded_timestamp_fields": list(
            DETERMINISM_TIMESTAMP_FIELDS
        ),
        "determinism_differences": differences,
    }


def _find_previous_completed_run(
    current_run_dir: Path,
    current_run_id: str,
    bundle_path: str,
) -> Optional[Path]:
    """Return the latest previous completed run for the same bundle."""
    runs_dir = current_run_dir.parent
    if not runs_dir.is_dir():
        return None

    normalized_bundle_path = _normalized_path_key(bundle_path)
    candidates: list[tuple[str, Path]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir() or run_dir.resolve() == current_run_dir.resolve():
            continue
        metadata = _load_json_mapping(run_dir / "metadata.json")
        if metadata.get("run_id") == current_run_id:
            continue
        if metadata.get("status") != "completed":
            continue
        if _normalized_path_key(metadata.get("bundle_path")) != normalized_bundle_path:
            continue
        if not (run_dir / "approval_packet.json").is_file():
            continue
        candidates.append((str(metadata.get("created_at") or run_dir.name), run_dir))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    """Read a JSON object from disk, returning an empty mapping on failure."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _normalized_path_key(value: Any) -> str:
    """Return a stable comparable path string."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return str(Path(value).resolve()).casefold()
    except OSError:
        return value.casefold()


def _strip_determinism_ignored_fields(value: Any) -> Any:
    """Remove timestamp fields recursively before determinism comparison."""
    if isinstance(value, Mapping):
        return {
            str(key): _strip_determinism_ignored_fields(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_determinism_timestamp_field(str(key))
        }
    if isinstance(value, list):
        return [_strip_determinism_ignored_fields(item) for item in value]
    return value


def _is_determinism_timestamp_field(key: str) -> bool:
    """Return whether a field is timestamp-like and ignored."""
    normalized_key = key.lower()
    return (
        normalized_key in DETERMINISM_TIMESTAMP_FIELDS
        or normalized_key.endswith("_at")
        or "timestamp" in normalized_key
    )


def _collect_determinism_differences(
    path: str,
    baseline: Any,
    current: Any,
    differences: list[str],
) -> None:
    """Append non-timestamp differences between two normalized values."""
    if type(baseline) is not type(current):
        differences.append(
            f"{path}: baseline={_format_determinism_value(baseline)}; "
            f"current={_format_determinism_value(current)}"
        )
        return

    if isinstance(baseline, Mapping) and isinstance(current, Mapping):
        keys = sorted(set(baseline) | set(current))
        for key in keys:
            child_path = f"{path}.{key}"
            if key not in baseline:
                differences.append(
                    f"{child_path}: baseline=<missing>; "
                    f"current={_format_determinism_value(current[key])}"
                )
                continue
            if key not in current:
                differences.append(
                    f"{child_path}: baseline="
                    f"{_format_determinism_value(baseline[key])}; "
                    "current=<missing>"
                )
                continue
            _collect_determinism_differences(
                path=child_path,
                baseline=baseline[key],
                current=current[key],
                differences=differences,
            )
        return

    if isinstance(baseline, list) and isinstance(current, list):
        if len(baseline) != len(current):
            differences.append(
                f"{path}: baseline_length={len(baseline)}; "
                f"current_length={len(current)}"
            )
        for index, (baseline_item, current_item) in enumerate(zip(baseline, current)):
            _collect_determinism_differences(
                path=f"{path}[{index}]",
                baseline=baseline_item,
                current=current_item,
                differences=differences,
            )
        return

    if baseline != current:
        differences.append(
            f"{path}: baseline={_format_determinism_value(baseline)}; "
            f"current={_format_determinism_value(current)}"
        )


def _format_determinism_value(value: Any) -> str:
    """Format a comparison value for compact metrics differences."""
    if isinstance(value, (Mapping, list)):
        formatted = json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        formatted = repr(value)
    if len(formatted) <= 200:
        return formatted
    return formatted[:197].rstrip() + "..."

