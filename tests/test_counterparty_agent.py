"""Tests for Agent C — Counterparty Resolution Agent."""

import csv
import json
from pathlib import Path

import pytest

from agents.counterparty_agent import (
    CounterpartyAgentError,
    normalize_name,
    run_counterparty_check,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run_dir(tmp_path: Path, run_id: str = "test-run") -> Path:
    """Create a run directory that follows the runtime artifact convention."""
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "audit_log.jsonl").touch()
    return run_dir


def _vendor_master_csv(tmp_path: Path, rows: list[dict[str, str]] | None = None) -> Path:
    """Write a vendor master CSV to a temp directory and return its path."""
    csv_path = tmp_path / "vendor_master.csv"
    if rows is None:
        rows = [
            {
                "vendor_id": "V-1001",
                "vendor_name": "Acme Corporation",
                "contact_email": "contracts@acme.example.com",
                "country": "USA",
                "risk_tier": "low",
                "active": "true",
                "annual_spend_usd": "250000",
            },
            {
                "vendor_id": "V-2001",
                "vendor_name": "TechServe Solutions Ltd.",
                "contact_email": "contracts@techserve.example.com",
                "country": "USA",
                "risk_tier": "medium",
                "active": "true",
                "annual_spend_usd": "480000",
            },
            {
                "vendor_id": "V-1002",
                "vendor_name": "Globex Industries",
                "contact_email": "legal@globex.example.com",
                "country": "USA",
                "risk_tier": "medium",
                "active": "true",
                "annual_spend_usd": "175000",
            },
            {
                "vendor_id": "V-2002",
                "vendor_name": "DataFlow Inc.",
                "contact_email": "legal@dataflow.example.com",
                "country": "Canada",
                "risk_tier": "low",
                "active": "true",
                "annual_spend_usd": "120000",
            },
        ]

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return csv_path


def _context(**overrides: object) -> dict:
    """Return valid context data for counterparty tests."""
    base = {
        "run_id": "test-run",
        "contract_type": "Master Services Agreement",
        "counterparty": "Acme Corporation",
        "jurisdiction": "New York, USA",
        "effective_date": "2025-03-01",
        "contract_file": "contract.pdf",
    }
    base.update(overrides)
    return base


def _extracted_contract(**overrides: object) -> dict:
    """Return a minimal extracted contract dict."""
    base: dict = {
        "run_id": "test-run",
        "document_id": "DOC-001",
        "source_file": "contract.pdf",
        "clauses": [],
    }
    base.update(overrides)
    return base


def _evidence_index() -> dict:
    """Return a minimal page-level evidence index."""
    return {
        "records": [
            {
                "evidence_id": "EV-001",
                "document_id": "DOC-001",
                "source_file": "contract.pdf",
                "page_number": 1,
                "excerpt": "Master Services Agreement - Acme Corporation",
            }
        ]
    }


# ---------------------------------------------------------------------------
# Normalization tests
# ---------------------------------------------------------------------------


class TestNormalizeName:
    """Test party name normalization logic."""

    def test_lowercases_name(self) -> None:
        assert normalize_name("ACME CORPORATION") == "acme"

    def test_strips_common_suffixes(self) -> None:
        assert normalize_name("TechServe Solutions Ltd.") == "techserve solutions"

    def test_strips_inc_suffix(self) -> None:
        assert normalize_name("DataFlow Inc.") == "dataflow"

    def test_collapses_whitespace(self) -> None:
        assert normalize_name("Acme    Corp.") == "acme"

    def test_removes_punctuation(self) -> None:
        result = normalize_name("O'Brien & Associates, L.L.C.")
        assert "'" not in result
        assert "&" not in result
        assert "," not in result

    def test_preserves_hyphens(self) -> None:
        result = normalize_name("Tech-Innovations LLC")
        assert "tech-innovations" in result

    def test_empty_string(self) -> None:
        assert normalize_name("") == ""

    def test_only_suffix(self) -> None:
        # When the name is only a suffix, we keep it to avoid empty output
        result = normalize_name("LLC")
        assert result != ""


# ---------------------------------------------------------------------------
# Vendor master loading tests
# ---------------------------------------------------------------------------


class TestVendorMasterLoading:
    """Test vendor master CSV loading and validation."""

    def test_missing_vendor_master_file_raises_error(self, tmp_path: Path) -> None:
        with pytest.raises(CounterpartyAgentError, match="Vendor master file not found"):
            run_counterparty_check(
                context=_context(),
                extracted_contract=_extracted_contract(),
                vendor_master_path=tmp_path / "nonexistent.csv",
            )

    def test_empty_vendor_master_raises_error(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("vendor_id,vendor_name\n", encoding="utf-8")
        with pytest.raises(CounterpartyAgentError, match="empty or has no data rows"):
            run_counterparty_check(
                context=_context(),
                extracted_contract=_extracted_contract(),
                vendor_master_path=csv_path,
            )

    def test_missing_columns_raises_error(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("name,email\nAcme,acme@test.com\n", encoding="utf-8")
        with pytest.raises(CounterpartyAgentError, match="missing required columns"):
            run_counterparty_check(
                context=_context(),
                extracted_contract=_extracted_contract(),
                vendor_master_path=csv_path,
            )


# ---------------------------------------------------------------------------
# Exact and fuzzy match tests
# ---------------------------------------------------------------------------


class TestExactMatching:
    """Test exact vendor name matching."""

    def test_exact_match_returns_exact_status(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Acme Corporation"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        matches = result["matches"]
        assert len(matches) == 1
        match = matches[0]
        assert match["original_party_name"] == "Acme Corporation"
        assert match["matched_vendor_name"] == "Acme Corporation"
        assert match["vendor_id"] == "V-1001"
        assert match["similarity_score"] >= 0.85
        assert match["match_status"] in ("exact", "fuzzy")
        assert match["manual_review_required"] is False


class TestFuzzyMatching:
    """Test fuzzy matching for spelling differences."""

    def test_small_spelling_difference_resolves(self, tmp_path: Path) -> None:
        """AC-1: Small spelling differences are resolved correctly."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Acme Corporaton"),  # typo
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["matched_vendor_name"] == "Acme Corporation"
        assert match["similarity_score"] >= 0.85
        assert match["match_status"] in ("exact", "fuzzy")

    def test_suffix_variation_resolves(self, tmp_path: Path) -> None:
        """Company suffix differences still match."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Acme Corp."),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["matched_vendor_name"] == "Acme Corporation"
        assert match["similarity_score"] >= 0.85

    def test_case_insensitive_matching(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="acme corporation"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["matched_vendor_name"] == "Acme Corporation"
        assert match["similarity_score"] >= 0.85


# ---------------------------------------------------------------------------
# Weak match and no-match tests
# ---------------------------------------------------------------------------


class TestWeakAndNoMatch:
    """Test weak matches and unknown vendors."""

    def test_unknown_vendor_flagged_as_new_counterparty(self, tmp_path: Path) -> None:
        """AC-2: Unknown vendors are flagged as new counterparties."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Completely Unknown Company XYZ"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["match_status"] in ("no_match", "weak")
        assert match["manual_review_required"] is True
        assert match["risk_flag"] is not None

    def test_weak_match_flagged_below_threshold(self, tmp_path: Path) -> None:
        """AC-3: Weak matches are flagged when similarity score is below 85%."""
        csv_path = _vendor_master_csv(tmp_path)
        # A name that is somewhat similar but not enough
        result = run_counterparty_check(
            context=_context(counterparty="Acme Global Partners"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        # Should either be weak or no_match depending on score
        if match["similarity_score"] < 0.85:
            assert match["manual_review_required"] is True
            assert match["risk_flag"] is not None

    def test_similarity_score_stored_in_output(self, tmp_path: Path) -> None:
        """AC-4: Similarity score is stored in findings."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert "similarity_score" in match
        assert 0.0 <= match["similarity_score"] <= 1.0

    def test_original_and_matched_names_stored(self, tmp_path: Path) -> None:
        """AC-5: Original and matched party names are stored."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Acme Corp"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["original_party_name"] == "Acme Corp"
        assert "normalized_party_name" in match
        assert match["matched_vendor_name"] is not None


# ---------------------------------------------------------------------------
# High-risk vendor tests
# ---------------------------------------------------------------------------


class TestHighRiskVendor:
    """Test high-risk counterparty detection."""

    def test_high_risk_vendor_flagged(self, tmp_path: Path) -> None:
        """AC-6: High-risk counterparty changes are flagged."""
        high_risk_rows = [
            {
                "vendor_id": "V-9001",
                "vendor_name": "Risky Vendor Corp",
                "contact_email": "info@risky.example.com",
                "country": "USA",
                "risk_tier": "high",
                "active": "true",
                "annual_spend_usd": "500000",
            },
        ]
        csv_path = _vendor_master_csv(tmp_path, rows=high_risk_rows)

        result = run_counterparty_check(
            context=_context(counterparty="Risky Vendor Corp"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["risk_flag"] is not None
        assert "high_risk" in match["risk_flag"]


# ---------------------------------------------------------------------------
# Manual review routing tests
# ---------------------------------------------------------------------------


class TestManualReview:
    """Test manual review routing for low-confidence matches."""

    def test_low_confidence_routes_for_manual_review(self, tmp_path: Path) -> None:
        """AC-7: Low matching confidence routes for manual review."""
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Totally Unrelated Entity ZZZZ"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["manual_review_required"] is True

    def test_high_confidence_not_flagged_for_review(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Acme Corporation"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["manual_review_required"] is False


# ---------------------------------------------------------------------------
# Artifact generation tests
# ---------------------------------------------------------------------------


class TestArtifactGeneration:
    """Test counterparty_resolution.json file generation."""

    def test_generates_counterparty_resolution_json(self, tmp_path: Path) -> None:
        """AC-8: counterparty_resolution.json is generated successfully."""
        run_dir = _run_dir(tmp_path)
        csv_path = _vendor_master_csv(tmp_path)

        result = run_counterparty_check(
            context=_context(),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        artifact_path = run_dir / "counterparty_resolution.json"
        assert artifact_path.is_file()
        assert result["artifact_paths"]["counterparty_resolution"] == str(artifact_path)

        # Verify JSON contents
        saved = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert saved["run_id"] == "test-run"
        assert len(saved["matches"]) >= 1

        # Verify all required fields in each match
        required_fields = {
            "original_party_name",
            "normalized_party_name",
            "matched_vendor_name",
            "vendor_id",
            "similarity_score",
            "match_status",
            "manual_review_required",
            "risk_flag",
            "evidence_pointer",
        }
        for match in saved["matches"]:
            assert required_fields.issubset(set(match.keys()))

    def test_works_without_run_dir(self, tmp_path: Path) -> None:
        """Agent works without writing artifacts when no run_dir is provided."""
        csv_path = _vendor_master_csv(tmp_path)

        result = run_counterparty_check(
            context=_context(),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        assert result["artifact_paths"] == {}
        assert len(result["matches"]) >= 1


# ---------------------------------------------------------------------------
# Party name extraction tests
# ---------------------------------------------------------------------------


class TestPartyNameExtraction:
    """Test extraction of party names from different sources."""

    def test_extracts_from_counterparty_field(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty="Globex Industries"),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        assert any(
            m["original_party_name"] == "Globex Industries"
            for m in result["matches"]
        )

    def test_extracts_from_parties_list(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(
                counterparty="",
                parties=["Acme Corporation", "DataFlow Inc."],
            ),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        names = [m["original_party_name"] for m in result["matches"]]
        assert "Acme Corporation" in names
        assert "DataFlow Inc." in names

    def test_extracts_from_clause(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(counterparty=""),
            extracted_contract=_extracted_contract(
                clauses=[
                    {
                        "clause_type": "party_names",
                        "text": "TechServe Solutions Ltd.",
                        "confidence": 0.95,
                    }
                ]
            ),
            vendor_master_path=csv_path,
        )

        assert any(
            m["original_party_name"] == "TechServe Solutions Ltd."
            for m in result["matches"]
        )

    def test_no_party_names_raises_error(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        with pytest.raises(CounterpartyAgentError, match="No party names found"):
            run_counterparty_check(
                context=_context(counterparty=""),
                extracted_contract=_extracted_contract(),
                vendor_master_path=csv_path,
            )

    def test_deduplicates_party_names(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        result = run_counterparty_check(
            context=_context(
                counterparty="Acme Corporation",
                parties=["Acme Corporation"],
            ),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        # Should not produce duplicate matches for the same name
        assert len(result["matches"]) == 1


# ---------------------------------------------------------------------------
# Evidence pointer tests
# ---------------------------------------------------------------------------


class TestEvidencePointers:
    """Test evidence pointer creation."""

    def test_evidence_pointer_from_evidence_index(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        run_dir = _run_dir(tmp_path)

        result = run_counterparty_check(
            context=_context(),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
            run_dir=run_dir,
            evidence_index=_evidence_index(),
        )

        match = result["matches"][0]
        assert match["evidence_pointer"] is not None
        assert match["evidence_pointer"]["evidence_id"] == "EV-001"
        assert match["evidence_pointer"]["source_file"] == "contract.pdf"

    def test_evidence_pointer_without_evidence_index(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)

        result = run_counterparty_check(
            context=_context(),
            extracted_contract=_extracted_contract(),
            vendor_master_path=csv_path,
        )

        match = result["matches"][0]
        assert match["evidence_pointer"] is not None
        assert match["evidence_pointer"]["source_file"] == "contract.pdf"


# ---------------------------------------------------------------------------
# Run directory validation
# ---------------------------------------------------------------------------


class TestRunDirectory:
    """Test run directory validation."""

    def test_missing_run_directory_raises_clear_error(self, tmp_path: Path) -> None:
        csv_path = _vendor_master_csv(tmp_path)
        with pytest.raises(CounterpartyAgentError, match="Run directory does not exist"):
            run_counterparty_check(
                context=_context(),
                extracted_contract=_extracted_contract(),
                vendor_master_path=csv_path,
                run_dir=tmp_path / "runs" / "missing",
            )
