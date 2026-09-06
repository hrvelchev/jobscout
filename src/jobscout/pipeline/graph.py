"""The per-posting LangGraph pipeline: reserve -> score -> gate -> draft -> sanitize.

The conditional edges ARE the cost and quality control: an empty wallet ends
the run before any call; a below-threshold score never pays for a draft; a
rule-breaking draft loops back exactly once with corrective feedback.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph

from jobscout.budget import Budget
from jobscout.models import PipelineState
from jobscout.pipeline import nodes
from jobscout.store import Store


def build_graph(
    client,
    store: Store,
    budget: Budget,
    *,
    draft_threshold: int,
):
    g = StateGraph(PipelineState)
    g.add_node("reserve", partial(nodes.reserve_node, budget=budget))
    g.add_node("score", partial(nodes.score_node, client=client, store=store, budget=budget))
    g.add_node("gate", partial(nodes.gate_node, draft_threshold=draft_threshold))
    g.add_node("draft", partial(nodes.draft_node, client=client, store=store, budget=budget))
    g.add_node("sanitize", partial(nodes.sanitize_node, store=store))

    g.set_entry_point("reserve")
    g.add_conditional_edges(
        "reserve",
        lambda s: END if s.get("outcome") == nodes.OUTCOME_OVER_CAP else "score",
    )
    g.add_conditional_edges(
        "score",
        lambda s: END if s.get("outcome") == nodes.OUTCOME_SCORE_FAILED else "gate",
    )
    g.add_conditional_edges(
        "gate",
        lambda s: END if s.get("outcome") == nodes.OUTCOME_BELOW_THRESHOLD else "draft",
    )
    g.add_conditional_edges(
        "draft",
        lambda s: END if s.get("outcome") == nodes.OUTCOME_OVER_CAP else "sanitize",
    )
    g.add_conditional_edges(
        "sanitize",
        lambda s: "draft" if s.get("sanitize_note") else END,
    )
    return g.compile()
