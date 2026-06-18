"""Risk package artifact and playbook loading."""

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from agents.risk.errors import RiskAgentError
from agents.risk.helpers import _as_mapping

def _load_playbook(
    context: Mapping[str, Any],
    playbook_path: str | Path | None,
) -> Mapping[str, Any]:
    """Load YAML playbook rules from path or context packet."""
    if playbook_path is None:
        return _as_mapping(context.get("playbook"))

    path = Path(playbook_path).resolve()
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RiskAgentError(f"Failed to load playbook YAML '{path}': {exc}") from exc
    if not isinstance(data, Mapping):
        raise RiskAgentError(f"Expected playbook YAML '{path}' to contain a mapping.")
    return data


def _read_context_packet(run_path: Optional[Path]) -> Optional[dict[str, Any]]:
    """Read context_packet.json from the run directory."""
    return _read_json_artifact(run_path, "context_packet.json", required=False)


def _read_json_artifact(
    run_path: Optional[Path],
    filename: str,
    required: bool,
) -> Optional[dict[str, Any]]:
    """Read a run-local JSON artifact."""
    if run_path is None:
        if required:
            raise RiskAgentError(f"Missing required artifact: {filename}")
        return None
    path = run_path / filename
    if not path.exists():
        if required:
            raise RiskAgentError(f"Missing required artifact: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RiskAgentError(f"Failed to read '{path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise RiskAgentError(f"Expected '{path}' to contain a JSON object.")
    return payload

