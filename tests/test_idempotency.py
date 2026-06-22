"""Tests for idempotent input fingerprinting."""

import json
import shutil
from pathlib import Path

from utils.bundle_loader import load_bundle
from utils.idempotency import (
    build_bundle_fingerprint,
    find_completed_run_by_fingerprint,
    hash_file,
)
from utils.run_manager import create_run_folder, update_run_status


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


def test_hash_file_changes_when_content_changes(tmp_path: Path) -> None:
    """File hashes should be stable for content and change when content changes."""
    path = tmp_path / "sample.txt"
    path.write_text("one", encoding="utf-8")
    first_hash = hash_file(path)

    path.write_text("two", encoding="utf-8")
    second_hash = hash_file(path)

    assert first_hash != second_hash
    assert len(first_hash) == 64
    assert len(second_hash) == 64


def test_bundle_fingerprint_changes_when_policy_changes(tmp_path: Path) -> None:
    """Policy files should participate in the input fingerprint."""
    bundle_copy = tmp_path / "bundle"
    shutil.copytree(NDA_BUNDLE, bundle_copy)

    first = build_bundle_fingerprint(load_bundle(bundle_copy))
    policy_path = bundle_copy / "approval_policy.yaml"
    policy_path.write_text(
        policy_path.read_text(encoding="utf-8") + "\n# changed for test\n",
        encoding="utf-8",
    )
    second = build_bundle_fingerprint(load_bundle(bundle_copy))

    assert first["contract_sha256"] == second["contract_sha256"]
    assert first["input_fingerprint_sha256"] != second["input_fingerprint_sha256"]
    assert {item["path"] for item in first["fingerprinted_files"]} >= {
        "contract.pdf",
        "manifest.yaml",
        "approval_policy.yaml",
        "playbook.yaml",
        "jurisdiction_rules.yaml",
        "vendor_master.csv",
    }


def test_completed_run_lookup_uses_input_fingerprint(tmp_path: Path) -> None:
    """Only completed runs with reusable artifacts should match a fingerprint."""
    runs_dir = tmp_path / "runs"
    fingerprint = build_bundle_fingerprint(load_bundle(NDA_BUNDLE))
    completed = create_run_folder(
        str(NDA_BUNDLE),
        runs_dir=runs_dir,
        metadata_extra=fingerprint,
    )
    completed_dir = Path(completed["run_dir"])
    for filename in (
        "approval_packet.json",
        "final_findings.json",
        "posting_payload.json",
        "metrics.json",
    ):
        (completed_dir / filename).write_text("{}", encoding="utf-8")
    (completed_dir / "exceptions.md").write_text("# Exceptions\n", encoding="utf-8")
    update_run_status(completed_dir, "completed")

    initialized = create_run_folder(
        str(NDA_BUNDLE),
        runs_dir=runs_dir,
        metadata_extra=fingerprint,
    )

    assert find_completed_run_by_fingerprint(
        runs_dir,
        str(fingerprint["input_fingerprint_sha256"]),
        current_run_id=initialized["run_id"],
    ) == completed_dir


def test_completed_run_lookup_ignores_incomplete_runs(tmp_path: Path) -> None:
    """Runs without final reusable artifacts should not be reused."""
    runs_dir = tmp_path / "runs"
    fingerprint = build_bundle_fingerprint(load_bundle(NDA_BUNDLE))
    incomplete = create_run_folder(
        str(NDA_BUNDLE),
        runs_dir=runs_dir,
        metadata_extra=fingerprint,
    )
    update_run_status(incomplete["run_dir"], "completed")

    assert find_completed_run_by_fingerprint(
        runs_dir,
        str(fingerprint["input_fingerprint_sha256"]),
    ) is None


def test_fingerprint_metadata_is_json_serializable() -> None:
    """Fingerprint payloads should be safe to store in metadata.json."""
    fingerprint = build_bundle_fingerprint(load_bundle(NDA_BUNDLE))

    json.dumps(fingerprint)
