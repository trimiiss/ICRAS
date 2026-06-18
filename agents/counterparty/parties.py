"""Party-name extraction for counterparty resolution."""

from typing import Any, Dict

from agents.counterparty.errors import CounterpartyAgentError


def extract_party_names(
    context: Dict[str, Any],
    extracted_contract: Dict[str, Any],
) -> list[str]:
    """Collect unique party names from the context and extracted clauses."""
    seen: set[str] = set()
    result: list[str] = []

    def add(name: str) -> None:
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)

    counterparty = context.get("counterparty")
    if isinstance(counterparty, str) and counterparty.strip():
        add(counterparty)

    for key in ("party_names", "parties"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            for part in value.split(";"):
                add(part)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)

    party_clause_types = {
        "party_names",
        "parties",
        "counterparty",
        "party",
        "contracting_parties",
        "party_identification",
    }
    clauses = extracted_contract.get("clauses", [])
    if isinstance(clauses, list):
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            clause_type = str(clause.get("clause_type", "")).lower().strip()
            if clause_type in party_clause_types:
                text = str(clause.get("text", "")).strip()
                if text:
                    add(text)

    if not result:
        raise CounterpartyAgentError(
            "No party names found in context or extracted clauses. "
            "Ensure the contract bundle includes counterparty information "
            "or that extraction identified party-related clauses."
        )

    return result
