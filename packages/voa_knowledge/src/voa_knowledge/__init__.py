"""The dedicated knowledge agent (docs/KNOWLEDGE-PLAN.md v2).

One component, isolated: callers know one contract, ``KnowledgeAgent.answer``
(question in, ``answered`` + text or ``deferred`` out). See ``agent.py``.
"""

from .agent import REASONS, KnowledgeAgent, KnowledgeResult, output_veto
from .classify import Classification, LlmClassification
from .draft import DRAFT_PROMPT_VERSION, Draft
from .ground import Fact, GroundingResult

__all__ = [
    "DRAFT_PROMPT_VERSION",
    "REASONS",
    "Classification",
    "Draft",
    "Fact",
    "GroundingResult",
    "KnowledgeAgent",
    "KnowledgeResult",
    "LlmClassification",
    "output_veto",
]
