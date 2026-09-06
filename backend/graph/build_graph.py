"""
LangGraph wiring for IP-SAKTI.

A deterministic DAG, not an autonomous agent loop: nodes run in a fixed
sequence, with exactly one bounded conditional retry after reranking if the
top result looks weak. should_retry forces the "generate" branch once
flags["retried"] is set, regardless of score, so the retry can fire at most
once — never an unbounded cycle.

    rewrite_query -> triage_formulation -> retrieve -> rerank -+-> generate_answer -> attach_citations -> expand_related_provisions -> END
                                                                |
                                            (weak score,        +-> retry_rewrite_query -> retrieve -> rerank -> ...
                                             not yet retried)        (flags["retried"]=True forces "generate" next time)

expand_related_provisions (graph_kg/kg.py) is the first slice of the PS's
own suggested "stage 2": a deterministic, no-LLM knowledge-graph lookup
over the statutory tags actually carried by this query's retrieved
chunks, surfacing real cross-jurisdiction pointers (e.g. a domestic
Section 3(p) question pointing at the WIPO GRATK Treaty's disclosure
obligation) and real corpus co-occurrence — never new legal content, only
pointers to already-indexed, already-citable chunks. See graph_kg/build_kg.py
for how the graph itself is built and why every edge is either
data-derived or an explicitly reviewed cross-reference table.

triage_formulation (graph/formulation.py) is a deterministic keyword
classifier, not an LLM call — it runs once, after rewrite_query resolves
the standalone question, and is not re-run on the retry path (the
formulation category doesn't change just because retrieval scored weakly).

Most nodes are async (they call Groq/Ollama or query pgvector) and this
compiled graph is driven with .ainvoke(), not .invoke() — see api/main.py.
should_retry, triage_formulation, and attach_citations_node stay sync (pure
logic, no I/O); LangGraph runs sync and async nodes in the same graph
without issue.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from graph.nodes import (
    attach_citations_node,
    expand_related_provisions_node,
    generate_answer,
    rerank_node,
    retrieve,
    retry_rewrite_query,
    rewrite_query,
    should_retry,
    triage_formulation_node,
)
from graph.state import GraphState


def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("triage_formulation", triage_formulation_node)
    graph.add_node("retrieve", retrieve)
    graph.add_node("rerank", rerank_node)
    graph.add_node("retry_rewrite_query", retry_rewrite_query)
    graph.add_node("generate_answer", generate_answer)
    graph.add_node("attach_citations", attach_citations_node)
    graph.add_node("expand_related_provisions", expand_related_provisions_node)

    graph.set_entry_point("rewrite_query")
    graph.add_edge("rewrite_query", "triage_formulation")
    graph.add_edge("triage_formulation", "retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_conditional_edges(
        "rerank",
        should_retry,
        {"retry": "retry_rewrite_query", "generate": "generate_answer"},
    )
    graph.add_edge("retry_rewrite_query", "retrieve")
    graph.add_edge("generate_answer", "attach_citations")
    graph.add_edge("attach_citations", "expand_related_provisions")
    graph.add_edge("expand_related_provisions", END)

    return graph.compile()
