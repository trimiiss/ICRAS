"""Extraction Agent — extracts clauses and key terms from contracts.

This agent receives a ContextPacket and the contract text, then identifies
and extracts individual clauses for downstream analysis.

LLM logic will be added in a later user story.
"""

from typing import Any, Dict, List


def run_extraction(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract clauses from the contract document.

    Args:
        context: The context packet from the intake agent.

    Returns:
        A list of extracted clause dictionaries.

    .. note:: Placeholder — actual implementation in a future sprint.
    """
    raise NotImplementedError(
        "Extraction agent logic will be implemented in a later story."
    )
