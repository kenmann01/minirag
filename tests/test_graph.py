"""Module diagrams keep the most connected nodes, and their ids stay distinct."""

import json

from app.harness.graph import Edge, callee_symbol, candidate_edges, module_mermaid


def _node(node_id: str, label: str) -> dict:
    return {"id": node_id, "label": label, "source_file": f"pkg/{label}.java"}


def _edge(source: str, target: str) -> Edge:
    return Edge(source, target, "calls", "EXTRACTED", "pkg/A.java", "pkg/B.java")


def test_the_diagram_keeps_the_most_connected_nodes():
    nodes = [_node("a", "Leaf"), _node("b", "Hub"), _node("c", "Other")]
    edges = [_edge("b", "a"), _edge("b", "c"), _edge("b", "outside")]
    diagram = module_mermaid(nodes, edges, "pkg/Leaf.java", cap=2)
    assert '["Hub"]' in diagram
    assert '["Leaf"]' in diagram
    assert '["Other"]' not in diagram


def test_equal_degree_keeps_the_earlier_node():
    nodes = [_node("a", "First"), _node("b", "Second")]
    diagram = module_mermaid(nodes, [], "pkg/First.java", cap=1)
    assert '["First"]' in diagram
    assert '["Second"]' not in diagram


def test_callee_symbol_keeps_the_last_segment():
    assert callee_symbol("pkg.Type:approve") == "approve"


def test_a_repeated_call_key_is_dropped_and_an_import_stays():
    loan = "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/Loan.java"
    application = "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/LoanApplication.java"
    schedule = "fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/LoanSchedule.java"
    client = "fineract-provider/src/main/java/org/apache/fineract/portfolio/client/Client.java"
    edges = [
        Edge("Loan", "approve", "calls", "EXTRACTED", loan, loan),
        Edge("Loan", "disburse", "calls", "EXTRACTED", loan, loan),
        Edge("Loan", "Client", "imports", "EXTRACTED", loan, client),
        Edge("LoanApplication", "submit", "calls", "EXTRACTED", application, application),
        Edge("LoanSchedule", "recalc", "calls", "EXTRACTED", schedule, schedule),
        Edge("Client", "identify", "calls", "EXTRACTED", client, client),
    ]
    chosen = candidate_edges(edges, 4)
    assert [(edge.source, edge.relation, edge.target) for edge in chosen] == [
        ("Loan", "imports", "Client"),
        ("LoanApplication", "calls", "submit"),
        ("LoanSchedule", "calls", "recalc"),
        ("Client", "calls", "identify"),
    ]


def test_long_shared_prefixes_stay_distinct_nodes():
    prefix = "fineract_core_src_main_java_org_apache_fineract_portfolio_"
    nodes = [
        _node(prefix + "alpha", "Alpha"),
        _node(prefix + "beta", "Beta"),
    ]
    diagram = module_mermaid(nodes, [], "pkg/Alpha.java")
    declared = [line.strip().split("[", 1)[0] for line in diagram.splitlines() if '["' in line]
    assert len(declared) == 2
    assert declared[0] != declared[1]


def test_node_file_tolerates_missing_nodes_and_unknown_shapes():
    from app.harness.graph import node_file

    assert node_file(None) == ""
    assert node_file({}) == ""
    assert node_file({"file": "pkg/A.java"}) == "pkg/A.java"


def test_edges_may_carry_their_endpoints_as_objects(tmp_path):
    from app.harness.graph import load_graph

    graph = tmp_path / "graph.json"
    graph.write_text(
        json.dumps(
            {
                "nodes": [],
                "links": [
                    {
                        "source": {"id": "Loan"},
                        "target": {"id": "approve"},
                        "relation": "calls",
                        "confidence": "EXTRACTED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _nodes, edges = load_graph(graph)
    assert edges[0].source == "Loan"
    assert edges[0].target == "approve"


def test_an_unknown_node_keeps_its_id_as_the_label():
    from app.harness.graph import node_label

    assert node_label([], "ghost") == "ghost"


def test_mermaid_ids_get_a_prefix_when_they_start_with_a_digit():
    from app.harness.graph import _mermaid_id

    assert _mermaid_id("123abc") == "n_123abc"
    assert _mermaid_id("Loan") == "Loan"


def test_a_bare_file_name_is_its_own_module():
    from app.harness.graph import module_key

    assert module_key("Loan.java") == "Loan.java"


def test_no_loan_nodes_means_loans_are_not_covered():
    from app.harness.graph import loans_covered

    assert loans_covered([{"id": "Client"}]) is False


def test_a_node_with_no_file_keys_has_no_file():
    from app.harness.graph import node_file

    assert node_file({"id": "Loan"}) == ""
