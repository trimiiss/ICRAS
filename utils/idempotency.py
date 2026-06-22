"""Input fingerprinting and duplicate-run lookup for idempotent reruns."""

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional

from utils.bundle_loader import REQUIRED_FILES


FINGERPRINT_ALGORITHM = "sha256"
IDEMPOTENCY_ARTIFACT = "idempotency_result.json"


def hash_file(path: Path) -> str:
    """Return the SHA-256 hash for a file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle_fingerprint(bundle_data: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic fingerprint for all bundle inputs that affect output."""
    bundle_dir = Path(str(bundle_data.get("bundle_dir") or "")).resolve()
    contract_path = Path(str(bundle_data.get("contract_path") or "")).resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"Bundle directory does not exist: {bundle_dir}")
    if not contract_path.is_file():
        raise FileNotFoundError(f"Contract file does not exist: {contract_path}")

    files = _fingerprinted_paths(bundle_dir, contract_path)
    file_hashes = [
        {
            "path": _relative_path(path, bundle_dir),
            "sha256": hash_file(path),
        }
        for path in files
    ]
    fingerprint_payload = {
        "algorithm": FINGERPRINT_ALGORITHM,
        "files": file_hashes,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    contract_sha256 = hash_file(contract_path)
    return {
        "contract_sha256": contract_sha256,
        "input_fingerprint_sha256": fingerprint,
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "fingerprinted_files": file_hashes,
    }


def find_completed_run_by_fingerprint(
    runs_dir: Path,
    fingerprint: str,
    current_run_id: str | None = None,
) -> Optional[Path]:
    """Return the latest completed run with the same input fingerprint."""
    if not fingerprint or not runs_dir.is_dir():
        return None

    candidates: list[tuple[str, Path]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        metadata = load_metadata(run_dir)
        if not metadata:
            continue
        if current_run_id and metadata.get("run_id") == current_run_id:
            continue
        if metadata.get("status") != "completed":
            continue
        if metadata.get("input_fingerprint_sha256") != fingerprint:
            continue
        if not _has_reusable_artifacts(run_dir):
            continue
        candidates.append((str(metadata.get("created_at") or run_dir.name), run_dir))

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def load_metadata(run_dir: Path) -> Mapping[str, Any]:
    """Load metadata.json from a run folder."""
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.is_file():
        return {}
    try:
        with metadata_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _fingerprinted_paths(bundle_dir: Path, contract_path: Path) -> list[Path]:
    """Return required bundle input paths in deterministic order."""
    paths = [bundle_dir / filename for filename in REQUIRED_FILES]
    if contract_path not in paths:
        paths.append(contract_path)

    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Cannot build idempotency fingerprint; missing required file(s): "
            + ", ".join(sorted(missing))
        )
    return sorted({path.resolve() for path in paths}, key=lambda path: str(path))


def _relative_path(path: Path, bundle_dir: Path) -> str:
    """Return a stable bundle-relative path when possible."""
    try:
        return path.relative_to(bundle_dir).as_posix()
    except ValueError:
        return path.name


def _has_reusable_artifacts(run_dir: Path) -> bool:
    """Return whether a completed run has enough final artifacts to reuse."""
    required = (
        "approval_packet.json",
        "final_findings.json",
        "exceptions.md",
        "posting_payload.json",
        "metrics.json",
    )
    return all((run_dir / filename).is_file() for filename in required)
