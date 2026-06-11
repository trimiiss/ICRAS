"""Orchestrator Agent — coordinates the full contract review pipeline.

This agent manages the execution order of all other agents, passes data
between them, and produces the final ApprovalPacket.

LLM logic will be added in a later user story.
"""

from typing import Any, Dict


def run_pipeline(bundle_path: str) -> Dict[str, Any]:
    """Execute the full contract review pipeline.

    Args:
        bundle_path: Path to the contract bundle folder.

    Returns:
        A dictionary representing the final approval packet.

    .. note:: Placeholder — actual implementation in a future sprint.
    """
    raise NotImplementedError(
        "Orchestrator agent logic will be implemented in a later story."
    )
