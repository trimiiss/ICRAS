"""Tests for the bundle loader (US-04).

Covers:
    - A valid sample bundle loads successfully.
    - A bundle with missing files raises an understandable error.
    - Manifest fields are correctly parsed.
    - Vendor master CSV is loaded as a list of dicts.
    - Supporting YAML files are parsed.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from utils.bundle_loader import BundleLoadError, load_bundle


# Path to sample bundles (relative to project root).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NDA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "clean_nda"
SA_BUNDLE = PROJECT_ROOT / "data" / "bundles" / "services_agreement"


class TestLoadValidBundle:
    """Test loading the included sample bundles."""

    def test_load_clean_nda(self):
        result = load_bundle(NDA_BUNDLE)
        manifest = result["manifest"]
        assert manifest["bundle_name"] == "clean_nda"
        assert manifest["contract_type"] == "Non-Disclosure Agreement"
        assert manifest["counterparty"] == "Acme Corporation"
        assert manifest["jurisdiction"] == "Delaware, USA"
        assert manifest["contract_file"] == "contract.pdf"

    def test_load_services_agreement(self):
        result = load_bundle(SA_BUNDLE)
        manifest = result["manifest"]
        assert manifest["bundle_name"] == "services_agreement"
        assert manifest["contract_type"] == "Master Services Agreement"

    def test_contract_path_exists(self):
        result = load_bundle(NDA_BUNDLE)
        assert Path(result["contract_path"]).is_file()

    def test_vendor_master_loaded(self):
        result = load_bundle(NDA_BUNDLE)
        vendors = result["vendor_master"]
        assert isinstance(vendors, list)
        assert len(vendors) >= 1
        assert "vendor_name" in vendors[0]

    def test_playbook_loaded(self):
        result = load_bundle(NDA_BUNDLE)
        playbook = result["playbook"]
        assert isinstance(playbook, dict)
        assert "required_clauses" in playbook

    def test_approval_policy_loaded(self):
        result = load_bundle(NDA_BUNDLE)
        policy = result["approval_policy"]
        assert isinstance(policy, dict)
        assert "approval_thresholds" in policy
        assert policy["approved_payment_terms"]["terms"] == ["net-30"]
        assert policy["manual_review_confidence_threshold"] == 0.75
        assert "signing_authority_thresholds" in policy
        assert "liability_cap_requirements" in policy
        assert "auto_renewal_rules" in policy
        assert "high_risk_jurisdictions" in policy
        assert "gdpr_requirements" in policy

    def test_jurisdiction_rules_loaded(self):
        result = load_bundle(NDA_BUNDLE)
        rules = result["jurisdiction_rules"]
        assert isinstance(rules, dict)
        assert "jurisdiction" in rules


class TestLoadInvalidBundle:
    """Test error handling for invalid bundles."""

    def test_nonexistent_folder_raises(self):
        with pytest.raises(BundleLoadError, match="does not exist"):
            load_bundle("/no/such/folder/fake_bundle")

    def test_missing_files_raises_with_names(self):
        """A bundle missing required files must list the missing filenames."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create only manifest.yaml — everything else is missing
            manifest_path = Path(tmpdir) / "manifest.yaml"
            manifest_path.write_text("bundle_name: test\n")

            with pytest.raises(
                BundleLoadError, match="missing required files"
            ) as exc_info:
                load_bundle(tmpdir)

            error_msg = str(exc_info.value)
            # At least contract.pdf and vendor_master.csv should be mentioned
            assert "contract.pdf" in error_msg
            assert "vendor_master.csv" in error_msg

    def test_missing_single_file_raises(self):
        """Removing one required file should cause a clear error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy a valid bundle, then delete one file
            dest = Path(tmpdir) / "test_bundle"
            shutil.copytree(NDA_BUNDLE, dest)
            (dest / "playbook.yaml").unlink()

            with pytest.raises(BundleLoadError, match="playbook.yaml"):
                load_bundle(dest)

    def test_file_path_not_directory_raises(self):
        """Passing a file path instead of a directory should fail."""
        with pytest.raises(BundleLoadError, match="not a directory"):
            load_bundle(NDA_BUNDLE / "manifest.yaml")

    def test_invalid_approval_policy_yaml_raises_clear_error(self):
        """Malformed approval_policy.yaml should identify the bad file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "bad_policy_bundle"
            shutil.copytree(NDA_BUNDLE, dest)
            (dest / "approval_policy.yaml").write_text(
                "approval_thresholds: [\n",
                encoding="utf-8",
            )

            with pytest.raises(
                BundleLoadError,
                match="Failed to parse approval_policy.yaml",
            ):
                load_bundle(dest)

    def test_invalid_approval_policy_rule_raises_clear_error(self):
        """Invalid rule values should include the policy field path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "invalid_policy_bundle"
            shutil.copytree(NDA_BUNDLE, dest)
            (dest / "approval_policy.yaml").write_text(
                "manual_review_confidence_threshold: 1.5\n",
                encoding="utf-8",
            )

            with pytest.raises(
                BundleLoadError,
                match="manual_review_confidence_threshold",
            ):
                load_bundle(dest)
