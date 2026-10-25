from backend.council.compiler import build_graph, clarifying_questions

M = ["ollama:a", "ollama:b"]


def _agents(g):
    return [n.id for n in g.nodes if n.type == "agent"]


def _agent_levels(g):
    ids = set(_agents(g))
    return [lv for lv in ([a for a in lvl if a in ids] for lvl in g.levels()) if lv]


def _valid(g):
    g.levels()
    assert g.config_hash


def test_cycle_is_broken_and_graph_valid():
    g = build_graph([{"id": "a", "role": "x", "depends_on": ["b"]},
                     {"id": "b", "role": "y", "depends_on": ["a"]}], M, M[-1])
    _valid(g)
    assert g.edge("b", "a") and not g.edge("a", "b")


def test_unknown_and_self_deps_dropped():
    g = build_graph([{"id": "a", "role": "x", "depends_on": ["a", "ghost"]},
                     {"id": "b", "role": "y"}], M, M[-1])
    _valid(g)
    assert g.parents("a") == ["fanout"]


def test_pipeline_has_ordered_levels():
    g = build_graph([{"id": "r", "role": "x"}, {"id": "d", "role": "y", "depends_on": ["r"]},
                     {"id": "v", "role": "z", "depends_on": ["d"]}], M, M[-1])
    _valid(g)
    assert _agent_levels(g) == [["r"], ["d"], ["v"]]
    assert g.parents("join") == ["v"]


def test_parallel_graph_single_agent_level():
    g = build_graph([{"id": "a", "role": "x"}, {"id": "b", "role": "y"}], M, M[-1])
    _valid(g)
    assert len(_agent_levels(g)) == 1


def test_audit_fallback_keeps_security_quality_docs():
    g = build_graph([], M, M[-1], intent="audit")
    _valid(g)
    assert _agents(g) == ["security", "quality", "docs"]


def test_non_audit_fallbacks_are_intent_shaped():
    for intent in ("build", "compare", "analyze", "explain", "chat", "other"):
        g = build_graph([], M, M[-1], intent=intent)
        _valid(g)
        assert set(_agents(g)).isdisjoint({"security", "quality", "docs"}), intent
    assert len(_agent_levels(build_graph([], M, M[-1], intent="build"))) == 3
    assert len(_agents(build_graph([], M, M[-1], intent="chat"))) == 2


def test_degenerate_still_allowed():
    g = build_graph([{"id": "a", "role": "x"}], M, M[-1], allow_degenerate=True)
    assert _agents(g) == ["a"]


def test_clarifying_questions_intent_aware():
    assert "audit" in clarifying_questions("g")[0]
    assert "audit" not in " ".join(clarifying_questions("g", "explain"))
