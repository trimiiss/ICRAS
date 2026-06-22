"""Tests for the run manager.

Covers:
    - A run folder contains metadata.json, config.json, and audit_log.jsonl.
    - Running twice produces different run folders.
    - Generated files are placed inside the corresponding run folder.
    - metadata.json contains required fields.
    - config.json contains valid pipeline configuration.
    - audit_log.jsonl exists and is initially empty.
"""

import json
import tempfile
from pathlib import Path


from utils.run_manager import (
    append_audit_event,
    create_run_folder,
    create_run_id,
    update_run_metadata,
    update_run_status,
)


# Path to a sample bundle for testing.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"


class TestCreateRunId:
    def test_format(self):
        """Run ID must match YYYYMMDD_HHMMSS_<8-char-hex> format."""
        run_id = create_run_id()
        parts = run_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert len(parts[2]) == 8  # short UUID hex

    def test_uniqueness(self):
        """Two consecutive IDs must be different."""
        id1 = create_run_id()
        id2 = create_run_id()
        assert id1 != id2


class TestCreateRunFolder:
    def test_run_folder_created(self):
        """A run folder must be created with the expected files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            run_dir = Path(result["run_dir"])
            assert run_dir.is_dir()
            assert (run_dir / "metadata.json").is_file()
            assert (run_dir / "config.json").is_file()
            assert (run_dir / "audit_log.jsonl").is_file()

    def test_metadata_contains_required_fields(self):
        """metadata.json must include run_id, bundle_path, created_at, status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            metadata_path = Path(result["run_dir"]) / "metadata.json"
            metadata = json.loads(metadata_path.read_text())

            assert "run_id" in metadata
            assert "bundle_path" in metadata
            assert "created_at" in metadata
            assert "status" in metadata
            assert metadata["status"] == "initialized"

    def test_metadata_accepts_idempotency_fields(self):
        """metadata.json should persist optional idempotency fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
                metadata_extra={
                    "contract_sha256": "contract-hash",
                    "input_fingerprint_sha256": "fingerprint-hash",
                },
            )

            metadata_path = Path(result["run_dir"]) / "metadata.json"
            metadata = json.loads(metadata_path.read_text())

            assert metadata["contract_sha256"] == "contract-hash"
            assert metadata["input_fingerprint_sha256"] == "fingerprint-hash"

    def test_config_contains_pipeline_info(self):
        """config.json must contain pipeline configuration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            config_path = Path(result["run_dir"]) / "config.json"
            config = json.loads(config_path.read_text())

            assert "pipeline_version" in config
            assert "agents" in config
            assert isinstance(config["agents"], list)

    def test_audit_log_initially_empty(self):
        """audit_log.jsonl must exist and be empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            audit_path = Path(result["run_dir"]) / "audit_log.jsonl"
            assert audit_path.is_file()
            assert audit_path.read_text().strip() == ""

    def test_two_runs_produce_different_folders(self):
        """Running the same bundle twice must create two separate run folders."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result1 = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )
            result2 = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            assert result1["run_id"] != result2["run_id"]
            assert result1["run_dir"] != result2["run_dir"]
            assert Path(result1["run_dir"]).is_dir()
            assert Path(result2["run_dir"]).is_dir()

    def test_generated_files_inside_run_folder(self):
        """All generated files must be inside the run folder, not elsewhere."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            run_dir = Path(result["run_dir"])
            generated_files = list(run_dir.iterdir())
            generated_names = {f.name for f in generated_files}

            assert generated_names == {
                "metadata.json",
                "config.json",
                "audit_log.jsonl",
            }

            # Verify nothing was written outside the run folder
            all_run_dirs = list(runs_dir.iterdir())
            assert len(all_run_dirs) == 1  # only one run folder

    def test_custom_config(self):
        """A custom config dict should be written to config.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            custom_config = {"custom_key": "custom_value", "version": "test"}
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
                config=custom_config,
            )

            config_path = Path(result["run_dir"]) / "config.json"
            config = json.loads(config_path.read_text())

            assert config["custom_key"] == "custom_value"

    def test_iso8601_timestamp(self):
        """created_at must be in ISO 8601 format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            metadata = result["metadata"]
            # ISO 8601 UTC timestamps contain 'T' and end with timezone info
            assert "T" in metadata["created_at"]


class TestRunAuditAndStatus:
    def test_append_audit_event_writes_jsonl_event(self):
        """Audit events should be appended as timestamped JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            append_audit_event(
                result["run_dir"],
                {
                    "event": "test_event",
                    "agent": "test_agent",
                    "message": "A test audit event was written.",
                },
            )

            audit_path = Path(result["run_dir"]) / "audit_log.jsonl"
            audit_event = json.loads(audit_path.read_text().strip())
            assert audit_event["event"] == "test_event"
            assert audit_event["agent"] == "test_agent"
            assert "timestamp" in audit_event

    def test_update_run_status_persists_failure_metadata(self):
        """Run status updates should be persisted to metadata.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            metadata = update_run_status(
                result["run_dir"],
                "failed",
                "Bundle validation failed.",
            )

            metadata_path = Path(result["run_dir"]) / "metadata.json"
            saved_metadata = json.loads(metadata_path.read_text())
            assert metadata["status"] == "failed"
            assert saved_metadata["status"] == "failed"
            assert saved_metadata["error_message"] == "Bundle validation failed."
            assert "updated_at" in saved_metadata

    def test_update_run_metadata_merges_fields(self):
        """Run metadata updates should preserve existing fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            result = create_run_folder(
                bundle_path=str(NDA_BUNDLE),
                runs_dir=runs_dir,
            )

            metadata = update_run_metadata(
                result["run_dir"],
                {
                    "idempotency_status": "duplicate",
                    "idempotency_baseline_run_id": "baseline-run",
                },
            )

            metadata_path = Path(result["run_dir"]) / "metadata.json"
            saved_metadata = json.loads(metadata_path.read_text())
            assert metadata["run_id"] == result["run_id"]
            assert saved_metadata["idempotency_status"] == "duplicate"
            assert saved_metadata["idempotency_baseline_run_id"] == "baseline-run"
            assert "updated_at" in saved_metadata
