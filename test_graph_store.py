from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import networkx as nx

from graph_store import (
    GraphWorkbookError,
    add_connection,
    graph_to_excel_bytes,
    load_graph_from_excel,
    load_graph_from_excel_bytes,
    save_graph_to_excel,
)


class GraphStoreTests(unittest.TestCase):
    def test_excel_round_trip_preserves_nodes_edges_positions_and_zero(self) -> None:
        graph = nx.DiGraph()
        graph.add_node("N001", label="Ремонт дорог", x=0.2, y=0.3)
        graph.add_node("N002", label="Аварийность", x=0.8, y=0.7)
        graph.add_node("N003", label="Мобильность", x=0.5, y=0.1)
        add_connection(graph, "N001", "N002", -0.75, bidirectional=True, reverse_weight=0.25, bold=True)
        add_connection(graph, "N003", "N001", 0.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "graph.xlsx"
            save_graph_to_excel(graph, path)
            restored = load_graph_from_excel(path)

        self.assertEqual(set(graph.nodes), set(restored.nodes))
        self.assertEqual(set(graph.edges), set(restored.edges))
        self.assertEqual(restored["N001"]["N002"]["weight"], -0.75)
        self.assertEqual(restored["N002"]["N001"]["weight"], 0.25)
        self.assertTrue(restored["N001"]["N002"]["bold"])
        self.assertTrue(restored["N002"]["N001"]["bold"])
        self.assertEqual(restored["N003"]["N001"]["weight"], 0.0)
        self.assertAlmostEqual(restored.nodes["N001"]["x"], 0.2)

        from_bytes = load_graph_from_excel_bytes(graph_to_excel_bytes(graph))
        self.assertEqual(from_bytes["N003"]["N001"]["weight"], 0.0)

    def test_weight_outside_range_is_rejected(self) -> None:
        graph = nx.DiGraph()
        graph.add_nodes_from(("A", "B"))
        with self.assertRaises(GraphWorkbookError):
            add_connection(graph, "A", "B", 1.01)


if __name__ == "__main__":
    unittest.main()
