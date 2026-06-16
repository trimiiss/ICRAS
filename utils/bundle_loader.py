"""Bundle Loader — validates and loads contract bundle folders.

A valid bundle folder must contain:
    - manifest.yaml
    - contract.pdf  (referenced in manifest)
    - vendor_master.csv
    - playbook.yaml
    - approval_policy.yaml
    - jurisdiction_rules.yaml
"""

import csv
from pathlib import Path
from typing import Any, Dict, List

import yaml
from pydantic import ValidationError

from schemas.policy_rules import PolicyRules


# Files that every bundle folder must contain.
REQUIRED_FILES: List[str] = [
    "manifest.yaml",
    "contract.pdf",
    "vendor_master.csv",
    "playbook.yaml",
    "approval_policy.yaml",
    "jurisdiction_rules.yaml",
]

# Fields that must appear in manifest.yaml.
REQUIRED_MANIFEST_FIELDS: List[str] = [
    "bundle_name",
    "contract_file",
    "contract_type",
    "counterparty",
    "jurisdiction",
]


class BundleLoadError(Exception):
    """Raised when a bundle cannot be loaded due to structural or content issues."""


def load_bundle(bundle_path: str | Path) -> Dict[str, Any]:
    """Load and validate a contract bundle folder.

    Steps:
        1. Confirm the bundle folder exists.
        2. Confirm all required files are present.
        3. Parse manifest.yaml and validate required fields.
        4. Confirm the contract file referenced in the manifest exists.
        5. Load supporting YAML files.
        6. Load vendor_master.csv.
        7. Return a structured dictionary with all data.

    Args:
        bundle_path: Path to the bundle folder (string or Path).

    Returns:
        A dictionary containing:
            - manifest: parsed manifest data
            - playbook: parsed playbook rules
            - approval_policy: parsed approval policy
            - jurisdiction_rules: parsed jurisdiction rules
            - vendor_master: list of vendor records (dicts)
            - contract_path: absolute path to the contract PDF

    Raises:
        BundleLoadError: If the bundle folder is missing, incomplete,
            or contains invalid data.
    """
    bundle_dir = Path(bundle_path).resolve()

    # 1. Confirm folder exists
    if not bundle_dir.exists():
        raise BundleLoadError(f"Bundle folder does not exist: {bundle_dir}")
    if not bundle_dir.is_dir():
        raise BundleLoadError(f"Bundle path is not a directory: {bundle_dir}")

    # 2. Confirm all required files exist
    missing_files = [f for f in REQUIRED_FILES if not (bundle_dir / f).is_file()]
    if missing_files:
        raise BundleLoadError(
            f"Bundle '{bundle_dir.name}' is missing required files: "
            + ", ".join(missing_files)
        )

    # 3. Parse manifest.yaml
    manifest = _load_yaml(bundle_dir / "manifest.yaml")
    missing_fields = [f for f in REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing_fields:
        raise BundleLoadError(
            "manifest.yaml is missing required fields: "
            + ", ".join(missing_fields)
        )

    # 4. Confirm referenced contract file exists
    contract_file = manifest["contract_file"]
    contract_path = bundle_dir / contract_file
    if not contract_path.is_file():
        raise BundleLoadError(
            f"Contract file referenced in manifest does not exist: {contract_path}"
        )

    # 5. Load supporting YAML files
    playbook = _load_yaml(bundle_dir / "playbook.yaml")
    approval_policy = _load_approval_policy(bundle_dir / "approval_policy.yaml")
    jurisdiction_rules = _load_yaml(bundle_dir / "jurisdiction_rules.yaml")

    # 6. Load vendor_master.csv
    vendor_master = _load_csv(bundle_dir / "vendor_master.csv")

    # 7. Return structured result
    return {
        "manifest": manifest,
        "playbook": playbook,
        "approval_policy": approval_policy,
        "jurisdiction_rules": jurisdiction_rules,
        "vendor_master": vendor_master,
        "contract_path": str(contract_path),
        "bundle_dir": str(bundle_dir),
    }


def _load_yaml(filepath: Path) -> Dict[str, Any]:
    """Load and parse a YAML file, returning a dictionary.

    Args:
        filepath: Absolute path to the YAML file.

    Returns:
        Parsed YAML contents as a dictionary.

    Raises:
        BundleLoadError: If the file cannot be read or parsed.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise BundleLoadError(
                f"Expected a YAML mapping in {filepath.name}, got {type(data).__name__}"
            )
        return data
    except yaml.YAMLError as exc:
        raise BundleLoadError(f"Failed to parse {filepath.name}: {exc}") from exc


def _load_approval_policy(filepath: Path) -> Dict[str, Any]:
    """Load and validate approval policy rules from YAML.

    Args:
        filepath: Absolute path to approval_policy.yaml.

    Returns:
        A JSON-compatible dictionary with defaults applied.

    Raises:
        BundleLoadError: If the YAML or policy rule values are invalid.
    """
    raw_policy = _load_yaml(filepath)
    try:
        return PolicyRules.model_validate(raw_policy).model_dump(mode="json")
    except ValidationError as exc:
        error_messages = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            error_messages.append(f"{location}: {error['msg']}")
        raise BundleLoadError(
            f"{filepath.name} contains invalid policy rules: "
            + "; ".join(error_messages)
        ) from exc


def _load_csv(filepath: Path) -> List[Dict[str, str]]:
    """Load a CSV file and return a list of row dictionaries.

    Args:
        filepath: Absolute path to the CSV file.

    Returns:
        List of dictionaries, one per CSV row.

    Raises:
        BundleLoadError: If the file cannot be read.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return list(reader)
    except Exception as exc:
        raise BundleLoadError(
            f"Failed to read {filepath.name}: {exc}"
        ) from exc
