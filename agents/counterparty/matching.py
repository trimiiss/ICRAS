"""Counterparty name normalization and vendor matching."""

import re
import unicodedata
from typing import Any, Dict, Mapping, Optional, Sequence

from thefuzz import fuzz

from schemas.counterparty_result import CounterpartyMatch, MatchStatus
from agents.counterparty.constants import (
    COMPANY_SUFFIXES,
    EXACT_THRESHOLD,
    STRONG_MATCH_THRESHOLD,
    WEAK_MATCH_THRESHOLD,
)
from agents.counterparty.evidence import build_evidence_pointer


def normalize_name(name: str) -> str:
    """Normalize a party name for comparison."""
    text = unicodedata.normalize("NFKD", name)
    text = text.lower()
    text = re.sub(r"[^\w\s\-]", " ", text)

    words = text.split()
    stripped: list[str] = []
    suffixes = {suffix.replace(".", "") for suffix in COMPANY_SUFFIXES}
    for word in words:
        if word.replace(".", "").lower() not in suffixes:
            stripped.append(word)
    text = " ".join(stripped) if stripped else " ".join(words)

    return re.sub(r"\s+", " ", text).strip()


def resolve_party(
    party_name: str,
    vendor_records: list[dict[str, str]],
    context: Dict[str, Any],
    evidence_records: Sequence[Mapping[str, Any]],
) -> CounterpartyMatch:
    """Match a single party name against the vendor master."""
    normalized = normalize_name(party_name)
    pre_suffix = _pre_suffix_normalize(party_name)

    best_score: float = 0.0
    best_vendor: Optional[dict[str, str]] = None

    for vendor in vendor_records:
        vendor_name = vendor.get("vendor_name", "")
        normalized_vendor = normalize_name(vendor_name)
        pre_suffix_vendor = _pre_suffix_normalize(vendor_name)

        score_stripped = fuzz.token_sort_ratio(normalized, normalized_vendor) / 100.0
        score_raw = fuzz.token_sort_ratio(pre_suffix, pre_suffix_vendor) / 100.0
        score = max(score_stripped, score_raw)

        if score > best_score:
            best_score = score
            best_vendor = vendor

    if best_score >= EXACT_THRESHOLD:
        match_status = MatchStatus.EXACT
    elif best_score >= STRONG_MATCH_THRESHOLD:
        match_status = MatchStatus.FUZZY
    elif best_score >= WEAK_MATCH_THRESHOLD:
        match_status = MatchStatus.WEAK
    else:
        match_status = MatchStatus.NO_MATCH

    evidence_pointer = build_evidence_pointer(context, evidence_records)
    manual_review = match_status in (MatchStatus.WEAK, MatchStatus.NO_MATCH)
    risk_flag = _assess_risk_flag(
        match_status=match_status,
        party_name=party_name,
        best_vendor=best_vendor,
        similarity_score=best_score,
    )

    matched_vendor_name: Optional[str] = None
    vendor_id: Optional[str] = None
    if best_vendor is not None and match_status != MatchStatus.NO_MATCH:
        matched_vendor_name = best_vendor.get("vendor_name")
        vendor_id = best_vendor.get("vendor_id")

    return CounterpartyMatch(
        original_party_name=party_name,
        normalized_party_name=normalized,
        matched_vendor_name=matched_vendor_name,
        vendor_id=vendor_id,
        similarity_score=round(best_score, 4),
        match_status=match_status,
        manual_review_required=manual_review,
        risk_flag=risk_flag,
        evidence_pointer=evidence_pointer,
    )


def _pre_suffix_normalize(name: str) -> str:
    """Normalize a party name without stripping company suffixes."""
    text = unicodedata.normalize("NFKD", name)
    text = text.lower()
    text = re.sub(r"[^\w\s\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _assess_risk_flag(
    match_status: MatchStatus,
    party_name: str,
    best_vendor: Optional[dict[str, str]],
    similarity_score: float,
) -> Optional[str]:
    """Determine if a risk flag should be raised for this match."""
    if match_status == MatchStatus.NO_MATCH:
        return (
            f"new_counterparty: '{party_name}' has no reliable match in the vendor "
            f"master (best score: {similarity_score:.0%})."
        )

    if match_status == MatchStatus.WEAK:
        vendor_name = best_vendor.get("vendor_name", "unknown") if best_vendor else "unknown"
        return (
            f"weak_match: '{party_name}' matched '{vendor_name}' with only "
            f"{similarity_score:.0%} similarity (threshold: 85%)."
        )

    if best_vendor is not None:
        risk_tier = str(best_vendor.get("risk_tier", "")).lower().strip()
        if risk_tier == "high":
            vendor_name = best_vendor.get("vendor_name", "unknown")
            return (
                f"high_risk_vendor: '{vendor_name}' is classified as a "
                f"high-risk vendor in the vendor master."
            )

    return None
