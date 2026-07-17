from __future__ import annotations

import math
import time
from datetime import datetime
from pathlib import Path

import networkx as nx
import streamlit as st

from graph_component import apply_position_updates, render_draggable_graph

from graph_store import (
    DEFAULT_NODE_COLOR,
    NODE_COLOR_PALETTE,
    GraphWorkbookError,
    add_connection,
    graph_to_excel_bytes,
    load_graph_from_excel,
    load_graph_from_excel_bytes,
    rename_node,
    save_graph_to_excel,
    validate_color,
)


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = APP_DIR / "graph_database.xlsx"
AUTOSAVE_DATABASE = APP_DIR / "graph_autosave.xlsx"
AUTOSAVE_INTERVAL_SECONDS = 5 * 60
GRAPH_COMPONENT_KEY = "draggable_graph"
LAYOUT_WIDTH = 1000.0
LAYOUT_HEIGHT = 550.0
MIN_NODE_CENTER_DISTANCE = 194.0
MIN_NODE_EDGE_DISTANCE = 82.0
MIN_LAYOUT_COORDINATE = -0.96
MAX_LAYOUT_COORDINATE = 1.96


def initialize_state() -> None:
    if "graph" not in st.session_state:
        st.session_state.graph = nx.DiGraph()
    if "next_node_id" not in st.session_state:
        st.session_state.next_node_id = 1
    if "status" not in st.session_state:
        st.session_state.status = "Создайте первую вершину или загрузите Excel-файл."

    if "layout_seed" not in st.session_state:
        st.session_state.layout_seed = 42
    if "edge_editor_revision" not in st.session_state:
        st.session_state.edge_editor_revision = 0
    if "quick_edge_selection" not in st.session_state:
        st.session_state.quick_edge_selection = False
    if "quick_edge_source" not in st.session_state:
        st.session_state.quick_edge_source = None
    if "node_spacing" not in st.session_state:
        st.session_state.node_spacing = MIN_NODE_CENTER_DISTANCE
    if "last_autosave_monotonic" not in st.session_state:
        st.session_state.last_autosave_monotonic = time.monotonic()
    if "last_autosave_at" not in st.session_state:
        st.session_state.last_autosave_at = None

def next_node_id(graph: nx.DiGraph) -> str:
    while f"N{st.session_state.next_node_id:03d}" in graph:
        st.session_state.next_node_id += 1
    node_id = f"N{st.session_state.next_node_id:03d}"
    st.session_state.next_node_id += 1
    return node_id


def reset_next_node_id(graph: nx.DiGraph) -> None:
    numbers = [int(node[1:]) for node in graph if node.startswith("N") and node[1:].isdigit()]
    st.session_state.next_node_id = max(numbers, default=0) + 1


def ensure_node_colors(graph: nx.DiGraph) -> None:
    for index, node_id in enumerate(graph):
        default = NODE_COLOR_PALETTE[index % len(NODE_COLOR_PALETTE)]
        try:
            color = validate_color(graph.nodes[node_id].get("color"), default=default)
        except GraphWorkbookError:
            color = default
        graph.nodes[node_id]["color"] = color


def add_node(graph: nx.DiGraph, label: str, color: str = DEFAULT_NODE_COLOR) -> None:
    node_id = next_node_id(graph)
    index = len(graph)
    angle = index * 2.399963
    graph.add_node(
        node_id,
        label=label.strip(),
        color=validate_color(color),
        x=0.5 + 0.32 * math.cos(angle),
        y=0.5 + 0.32 * math.sin(angle),
    )
    st.session_state.status = f"Добавлена вершина «{label.strip()}» ({node_id})."

def apply_spring_layout(
    graph: nx.DiGraph,
    seed: int = 42,
    minimum_distance: float = MIN_NODE_CENTER_DISTANCE,
) -> int:
    if not graph:
        return 0
    if len(graph) == 1:
        node = next(iter(graph))
        graph.nodes[node]["x"] = graph.nodes[node]["y"] = 0.5
        return 0
    ideal_distance = max(0.25, 1.0 / math.sqrt(len(graph)))
    if len(graph) <= 25:
        candidate_count, finalist_count = 24, 8
    elif len(graph) <= 60:
        candidate_count, finalist_count = 16, 5
    else:
        candidate_count, finalist_count = 10, 3

    topology = nx.Graph()
    topology.add_nodes_from(graph.nodes)
    topology.add_edges_from(_unique_layout_edges(graph))
    base_layouts = _generate_base_layouts(
        topology,
        seed=seed,
        ideal_distance=ideal_distance,
        candidate_count=candidate_count,
    )
    candidates: list[tuple[tuple[int, float], dict[str, tuple[float, float]]]] = []
    for layout in base_layouts:
        positions = _normalize_layout(layout)
        candidate_graph = graph.copy()
        _apply_layout_positions(candidate_graph, positions)
        candidates.append((_rough_layout_score(candidate_graph), positions))

    finalists = sorted(candidates, key=lambda candidate: candidate[0])[:finalist_count]
    refined: list[tuple[tuple[int, int, float, float], nx.DiGraph]] = []
    for _rough_score, positions in finalists:
        candidate_graph = graph.copy()
        _apply_layout_positions(candidate_graph, positions)
        for _ in range(6):
            separate_close_nodes(
                candidate_graph,
                minimum_distance=minimum_distance,
                iterations=40,
            )
            separate_nodes_from_edges(candidate_graph, iterations=40)
        score = _layout_score(candidate_graph)
        refined.append((score, candidate_graph))

    refined.sort(key=lambda candidate: candidate[0])
    optimized = list(refined)
    for _score, candidate_graph in refined[:2]:
        optimized_graph = candidate_graph.copy()
        optimized_score = _optimize_layout_by_swapping(optimized_graph)
        optimized.append((optimized_score, optimized_graph))
    best_score, best_graph = min(optimized, key=lambda candidate: candidate[0])

    if best_graph is not None:
        for node_id in graph:
            graph.nodes[node_id]["x"] = float(best_graph.nodes[node_id]["x"])
            graph.nodes[node_id]["y"] = float(best_graph.nodes[node_id]["y"])
    return best_score[0]


def _generate_base_layouts(
    topology: nx.Graph,
    *,
    seed: int,
    ideal_distance: float,
    candidate_count: int,
) -> list[dict[str, object]]:
    layouts: list[dict[str, object]] = []
    planar, _embedding = nx.check_planarity(topology)
    if planar:
        layouts.append(nx.planar_layout(topology))
    try:
        layouts.append(nx.kamada_kawai_layout(topology, weight=None))
    except (nx.NetworkXException, ValueError, ModuleNotFoundError):
        pass
    if len(topology) >= 3:
        try:
            layouts.append(nx.spectral_layout(topology, weight=None))
        except (nx.NetworkXException, ValueError):
            pass

    degree_order = sorted(topology, key=lambda node: (-topology.degree[node], str(node)))
    layouts.append(_circular_layout(degree_order))
    layouts.append(_circular_layout(list(reversed(degree_order))))

    spring_count = max(1, candidate_count - len(layouts))
    for candidate_index in range(spring_count):
        layouts.append(
            nx.spring_layout(
                topology,
                seed=seed + candidate_index * 7919,
                weight=None,
                k=ideal_distance,
                iterations=250,
            )
        )
    return layouts[:candidate_count]


def _circular_layout(node_order: list[str]) -> dict[str, tuple[float, float]]:
    count = max(len(node_order), 1)
    return {
        str(node_id): (
            math.cos(2 * math.pi * index / count),
            math.sin(2 * math.pi * index / count),
        )
        for index, node_id in enumerate(node_order)
    }


def _normalize_layout(layout: dict[str, object]) -> dict[str, tuple[float, float]]:
    xs = [float(point[0]) for point in layout.values()]
    ys = [float(point[1]) for point in layout.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    return {
        str(node_id): (
            normalize(float(point[0]), x_min, x_max),
            normalize(float(point[1]), y_min, y_max),
        )
        for node_id, point in layout.items()
    }


def _apply_layout_positions(
    graph: nx.DiGraph,
    positions: dict[str, tuple[float, float]],
) -> None:
    for node_id in graph:
        x, y = positions[str(node_id)]
        graph.nodes[node_id]["x"] = x
        graph.nodes[node_id]["y"] = y


def _unique_layout_edges(graph: nx.DiGraph) -> list[tuple[str, str]]:
    unique_edges: list[tuple[str, str]] = []
    seen: set[frozenset[str]] = set()
    for source, target in graph.edges:
        pair = frozenset((str(source), str(target)))
        if pair in seen:
            continue
        seen.add(pair)
        unique_edges.append((str(source), str(target)))
    return unique_edges


def _pixel_position(graph: nx.DiGraph, node_id: str) -> tuple[float, float]:
    return (
        float(graph.nodes[node_id]["x"]) * LAYOUT_WIDTH,
        float(graph.nodes[node_id]["y"]) * LAYOUT_HEIGHT,
    )


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _point_on_segment(
    start: tuple[float, float],
    point: tuple[float, float],
    end: tuple[float, float],
    epsilon: float = 1e-7,
) -> bool:
    return (
        min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
        and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
    )


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    epsilon = 1e-7
    first_a = _orientation(first_start, first_end, second_start)
    first_b = _orientation(first_start, first_end, second_end)
    second_a = _orientation(second_start, second_end, first_start)
    second_b = _orientation(second_start, second_end, first_end)
    if first_a * first_b < -epsilon and second_a * second_b < -epsilon:
        return True
    return (
        (abs(first_a) <= epsilon and _point_on_segment(first_start, second_start, first_end))
        or (abs(first_b) <= epsilon and _point_on_segment(first_start, second_end, first_end))
        or (abs(second_a) <= epsilon and _point_on_segment(second_start, first_start, second_end))
        or (abs(second_b) <= epsilon and _point_on_segment(second_start, first_end, second_end))
    )


def count_edge_crossings(graph: nx.DiGraph) -> int:
    edges = _unique_layout_edges(graph)
    crossings = 0
    for first_index, (first_source, first_target) in enumerate(edges):
        first_nodes = {first_source, first_target}
        first_start = _pixel_position(graph, first_source)
        first_end = _pixel_position(graph, first_target)
        for second_source, second_target in edges[first_index + 1 :]:
            if first_nodes.intersection((second_source, second_target)):
                continue
            if _segments_intersect(
                first_start,
                first_end,
                _pixel_position(graph, second_source),
                _pixel_position(graph, second_target),
            ):
                crossings += 1
    return crossings


def _node_edge_conflicts(graph: nx.DiGraph) -> int:
    conflicts = 0
    for source, target in _unique_layout_edges(graph):
        source_x, source_y = _pixel_position(graph, source)
        target_x, target_y = _pixel_position(graph, target)
        edge_x, edge_y = target_x - source_x, target_y - source_y
        length_squared = edge_x * edge_x + edge_y * edge_y
        if length_squared < 1e-9:
            continue
        for node_id in graph:
            node_id = str(node_id)
            if node_id in (source, target):
                continue
            node_x, node_y = _pixel_position(graph, node_id)
            projection = ((node_x - source_x) * edge_x + (node_y - source_y) * edge_y) / length_squared
            if not 0.05 < projection < 0.95:
                continue
            closest_x = source_x + projection * edge_x
            closest_y = source_y + projection * edge_y
            if math.hypot(node_x - closest_x, node_y - closest_y) < MIN_NODE_EDGE_DISTANCE:
                conflicts += 1
    return conflicts


def _total_edge_length(graph: nx.DiGraph) -> float:
    return sum(
        math.dist(_pixel_position(graph, source), _pixel_position(graph, target))
        for source, target in _unique_layout_edges(graph)
    )


def _angular_resolution_penalty(graph: nx.DiGraph) -> float:
    neighbors: dict[str, set[str]] = {str(node_id): set() for node_id in graph}
    for source, target in _unique_layout_edges(graph):
        neighbors[source].add(target)
        neighbors[target].add(source)
    penalty = 0.0
    desired_angle = math.radians(20)
    for node_id, adjacent in neighbors.items():
        if len(adjacent) < 2:
            continue
        center_x, center_y = _pixel_position(graph, node_id)
        angles = sorted(
            math.atan2(
                _pixel_position(graph, adjacent_id)[1] - center_y,
                _pixel_position(graph, adjacent_id)[0] - center_x,
            )
            for adjacent_id in adjacent
        )
        gaps = [
            (angles[(index + 1) % len(angles)] - angle) % (2 * math.pi)
            for index, angle in enumerate(angles)
        ]
        penalty += sum(max(0.0, desired_angle - gap) ** 2 for gap in gaps)
    return penalty


def _rough_layout_score(graph: nx.DiGraph) -> tuple[int, float]:
    return count_edge_crossings(graph), _total_edge_length(graph)


def _layout_score(graph: nx.DiGraph) -> tuple[int, int, float, float]:
    return (
        count_edge_crossings(graph),
        _node_edge_conflicts(graph),
        _angular_resolution_penalty(graph),
        _total_edge_length(graph),
    )


def _optimize_layout_by_swapping(
    graph: nx.DiGraph,
    *,
    max_passes: int = 4,
    max_pair_checks: int = 800,
) -> tuple[int, int, float, float]:
    node_ids = sorted(graph, key=lambda node_id: (-graph.degree[node_id], str(node_id)))
    current_score = _layout_score(graph)
    for _ in range(max_passes):
        best_pair: tuple[str, str] | None = None
        best_score = current_score
        checks = 0
        for first_index, first in enumerate(node_ids):
            for second in node_ids[first_index + 1 :]:
                first_position = (graph.nodes[first]["x"], graph.nodes[first]["y"])
                second_position = (graph.nodes[second]["x"], graph.nodes[second]["y"])
                graph.nodes[first]["x"], graph.nodes[first]["y"] = second_position
                graph.nodes[second]["x"], graph.nodes[second]["y"] = first_position
                score = _layout_score(graph)
                graph.nodes[first]["x"], graph.nodes[first]["y"] = first_position
                graph.nodes[second]["x"], graph.nodes[second]["y"] = second_position
                checks += 1
                if score < best_score:
                    best_score = score
                    best_pair = (first, second)
                if checks >= max_pair_checks:
                    break
            if checks >= max_pair_checks:
                break
        if best_pair is None:
            break
        first, second = best_pair
        first_position = (graph.nodes[first]["x"], graph.nodes[first]["y"])
        second_position = (graph.nodes[second]["x"], graph.nodes[second]["y"])
        graph.nodes[first]["x"], graph.nodes[first]["y"] = second_position
        graph.nodes[second]["x"], graph.nodes[second]["y"] = first_position
        current_score = best_score
    return current_score


def separate_close_nodes(
    graph: nx.DiGraph,
    minimum_distance: float = MIN_NODE_CENTER_DISTANCE,
    iterations: int = 200,
) -> None:
    node_ids = list(graph)
    if len(node_ids) < 2:
        return
    for _ in range(iterations):
        changed = False
        for first_index, first in enumerate(node_ids):
            for second_index in range(first_index + 1, len(node_ids)):
                second = node_ids[second_index]
                first_x = float(graph.nodes[first]["x"])
                first_y = float(graph.nodes[first]["y"])
                second_x = float(graph.nodes[second]["x"])
                second_y = float(graph.nodes[second]["y"])
                dx = (second_x - first_x) * LAYOUT_WIDTH
                dy = (second_y - first_y) * LAYOUT_HEIGHT
                distance = math.hypot(dx, dy)
                if distance >= minimum_distance:
                    continue
                if distance < 1e-9:
                    angle = (first_index * len(node_ids) + second_index) * 2.399963
                    unit_x, unit_y = math.cos(angle), math.sin(angle)
                else:
                    unit_x, unit_y = dx / distance, dy / distance
                shift = (minimum_distance - distance) / 2 + 0.1
                graph.nodes[first]["x"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, first_x - unit_x * shift / LAYOUT_WIDTH))
                graph.nodes[first]["y"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, first_y - unit_y * shift / LAYOUT_HEIGHT))
                graph.nodes[second]["x"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, second_x + unit_x * shift / LAYOUT_WIDTH))
                graph.nodes[second]["y"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, second_y + unit_y * shift / LAYOUT_HEIGHT))
                changed = True
        if not changed:
            break


def separate_nodes_from_edges(
    graph: nx.DiGraph,
    minimum_distance: float = MIN_NODE_EDGE_DISTANCE,
    iterations: int = 120,
) -> None:
    node_ids = list(graph)
    edges = list(graph.edges)
    if len(node_ids) < 3 or not edges:
        return
    for _ in range(iterations):
        changed = False
        for edge_index, (source, target) in enumerate(edges):
            source_x = float(graph.nodes[source]["x"]) * LAYOUT_WIDTH
            source_y = float(graph.nodes[source]["y"]) * LAYOUT_HEIGHT
            target_x = float(graph.nodes[target]["x"]) * LAYOUT_WIDTH
            target_y = float(graph.nodes[target]["y"]) * LAYOUT_HEIGHT
            edge_x = target_x - source_x
            edge_y = target_y - source_y
            edge_length_squared = edge_x * edge_x + edge_y * edge_y
            if edge_length_squared < 1e-9:
                continue
            for node_index, node_id in enumerate(node_ids):
                if node_id in (source, target):
                    continue
                node_x = float(graph.nodes[node_id]["x"]) * LAYOUT_WIDTH
                node_y = float(graph.nodes[node_id]["y"]) * LAYOUT_HEIGHT
                projection = ((node_x - source_x) * edge_x + (node_y - source_y) * edge_y) / edge_length_squared
                if not 0.05 < projection < 0.95:
                    continue
                closest_x = source_x + projection * edge_x
                closest_y = source_y + projection * edge_y
                away_x = node_x - closest_x
                away_y = node_y - closest_y
                distance = math.hypot(away_x, away_y)
                if distance >= minimum_distance:
                    continue
                if distance < 1e-9:
                    edge_length = math.sqrt(edge_length_squared)
                    direction = -1.0 if (edge_index + node_index) % 2 else 1.0
                    unit_x = -edge_y / edge_length * direction
                    unit_y = edge_x / edge_length * direction
                else:
                    unit_x, unit_y = away_x / distance, away_y / distance
                shift = minimum_distance - distance + 0.2
                graph.nodes[node_id]["x"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, (node_x + unit_x * shift) / LAYOUT_WIDTH))
                graph.nodes[node_id]["y"] = max(MIN_LAYOUT_COORDINATE, min(MAX_LAYOUT_COORDINATE, (node_y + unit_y * shift) / LAYOUT_HEIGHT))
                changed = True
        if not changed:
            break


def ensure_positions(graph: nx.DiGraph) -> None:
    if any(graph.nodes[node].get("x") is None or graph.nodes[node].get("y") is None for node in graph):
        apply_spring_layout(graph)


def normalize(value: float, minimum: float, maximum: float) -> float:
    if math.isclose(minimum, maximum):
        return 0.5
    return 0.08 + 0.84 * (value - minimum) / (maximum - minimum)


def node_option(graph: nx.DiGraph, node_id: str) -> str:
    return f"{node_id} — {graph.nodes[node_id].get('label', node_id)}"


def node_id_from_option(option: str) -> str:
    return option.split(" — ", 1)[0]


def component_state_value(name: str) -> object | None:
    component_state = st.session_state.get(GRAPH_COMPONENT_KEY, {})
    if hasattr(component_state, "get"):
        return component_state.get(name)
    return getattr(component_state, name, None)


def sync_selected_node() -> None:
    graph: nx.DiGraph | None = st.session_state.get("graph")
    selected = component_state_value("selected_node")
    node_id = selected.get("id") if hasattr(selected, "get") else selected
    if graph is None or node_id not in graph:
        return
    node_id = str(node_id)
    if st.session_state.get("quick_edge_selection"):
        source = st.session_state.get("quick_edge_source")
        if source is None:
            st.session_state.quick_edge_source = node_id
            st.session_state.status = f"Первая вершина связи выбрана: {node_option(graph, node_id)}. Выберите вторую."
        elif source == node_id:
            st.session_state.status = "Выберите другую вершину для связи."
        else:
            st.session_state.edge_source = node_option(graph, str(source))
            st.session_state.edge_target = node_option(graph, node_id)
            st.session_state.edge_editor_revision += 1
            st.session_state.quick_edge_selection = False
            st.session_state.quick_edge_source = None
            st.session_state.status = f"Вершины связи выбраны: {source} → {node_id}."
        return
    st.session_state.node_editor = node_option(graph, str(node_id))
    st.session_state.status = f"Выбрана вершина «{graph.nodes[node_id].get('label', node_id)}»."


def sync_selected_edge() -> None:
    graph: nx.DiGraph | None = st.session_state.get("graph")
    selected = component_state_value("selected_edge")
    if graph is None or not hasattr(selected, "get"):
        return
    source = str(selected.get("source", ""))
    target = str(selected.get("target", ""))
    if not graph.has_edge(source, target):
        return
    st.session_state.edge_source = node_option(graph, source)
    st.session_state.edge_target = node_option(graph, target)
    st.session_state.edge_editor_revision += 1
    st.session_state.status = f"Выбрана связь {source} → {target}."


def swap_edge_nodes() -> None:
    """Меняет местами вершины в редакторе связи."""
    source = st.session_state.get("edge_source")
    target = st.session_state.get("edge_target")
    if not source or not target:
        return
    st.session_state.edge_source = target
    st.session_state.edge_target = source
    st.session_state.edge_editor_revision += 1


def toggle_quick_edge_selection() -> None:
    active = not st.session_state.get("quick_edge_selection", False)
    st.session_state.quick_edge_selection = active
    st.session_state.quick_edge_source = None
    st.session_state.status = (
        "Выберите на графе первую вершину связи."
        if active
        else "Быстрый выбор вершин отменён."
    )


@st.fragment(run_every=AUTOSAVE_INTERVAL_SECONDS)
def run_autosave(graph: nx.DiGraph) -> None:
    now = time.monotonic()
    if now - st.session_state.last_autosave_monotonic >= AUTOSAVE_INTERVAL_SECONDS:
        try:
            save_graph_to_excel(graph, AUTOSAVE_DATABASE)
        except (OSError, GraphWorkbookError, ValueError) as exc:
            st.error(f"Не удалось выполнить автосохранение: {exc}")
        else:
            st.session_state.last_autosave_monotonic = now
            st.session_state.last_autosave_at = datetime.now().strftime("%H:%M:%S")
    if st.session_state.last_autosave_at:
        st.caption(f"Последнее автосохранение: {st.session_state.last_autosave_at}")
    else:
        st.caption("Автосохранение выполняется каждые 5 минут.")


def render_sidebar(graph: nx.DiGraph) -> None:
    ensure_node_colors(graph)
    st.sidebar.header("Редактор")
    with st.sidebar.expander("1. Вершины", expanded=True):
        with st.form("add_node_form", clear_on_submit=True):
            label = st.text_input("Название вершины")
            new_color = st.color_picker(
                "Цвет новой вершины",
                value=NODE_COLOR_PALETTE[len(graph) % len(NODE_COLOR_PALETTE)],
                key=f"new_node_color_{len(graph)}",
            )
            submitted = st.form_submit_button("Добавить вершину", width="stretch")
            if submitted:
                if label.strip():
                    add_node(graph, label, new_color)
                    st.rerun()
                else:
                    st.error("Введите название вершины.")

        if graph:
            options = [node_option(graph, node) for node in graph]
            selected_node_option = st.selectbox("Выбранная вершина", options, key="node_editor")
            selected_node_id = node_id_from_option(selected_node_option)
            current_label = str(graph.nodes[selected_node_id].get("label", selected_node_id))
            with st.form(f"rename_node_form_{selected_node_id}"):
                edited_label = st.text_input("Новое название", value=current_label)
                rename_submitted = st.form_submit_button("Переименовать вершину", width="stretch")
                if rename_submitted:
                    try:
                        rename_node(graph, selected_node_id, edited_label)
                    except GraphWorkbookError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.status = f"Вершина «{current_label}» переименована в «{edited_label.strip()}»."
                        st.rerun()
            current_color = validate_color(graph.nodes[selected_node_id].get("color", DEFAULT_NODE_COLOR))
            edited_color = st.color_picker(
                "Цвет выбранной вершины",
                value=current_color,
                key=f"edit_color_{selected_node_id}",
            )
            if st.button("Применить цвет", width="stretch"):
                graph.nodes[selected_node_id]["color"] = validate_color(edited_color)
                label_value = graph.nodes[selected_node_id].get("label", selected_node_id)
                st.session_state.status = f"Цвет вершины «{label_value}» изменён."
                st.rerun()
            if st.button("Удалить выбранную вершину", width="stretch"):
                label_value = graph.nodes[selected_node_id].get("label", selected_node_id)
                graph.remove_node(selected_node_id)
                st.session_state.edge_editor_revision += 1
                st.session_state.status = f"Вершина «{label_value}» и её связи удалены."
                st.rerun()
    with st.sidebar.expander("2. Связи", expanded=True):
        if len(graph) < 2:
            st.caption("Для связи нужны минимум две вершины.")
        else:
            quick_selection_active = st.session_state.quick_edge_selection
            st.button(
                "Отменить выбор вершин" if quick_selection_active else "Выбрать 2 вершины на графе",
                width="stretch",
                key="toggle_quick_edge_selection",
                on_click=toggle_quick_edge_selection,
                type="primary" if quick_selection_active else "secondary",
            )
            if quick_selection_active:
                quick_source = st.session_state.quick_edge_source
                if quick_source in graph:
                    st.info(f"Первая: {node_option(graph, quick_source)}. Теперь выберите вторую вершину.")
                else:
                    st.info("Нажмите первую вершину на графе.")
            options = [node_option(graph, node) for node in graph]
            source_option = st.selectbox("Из вершины", options, index=0, key="edge_source")
            source = node_id_from_option(source_option)
            target_options = [option for option in options if node_id_from_option(option) != source]
            target_option = st.selectbox("В вершину", target_options, index=0, key="edge_target")
            target = node_id_from_option(target_option)
            st.button(
                "⇄ Поменять вершины местами",
                width="stretch",
                key="swap_edge_nodes",
                on_click=swap_edge_nodes,
                help="Меняет местами вершины «Из» и «В».",
            )

            forward_attrs = graph.get_edge_data(source, target, default={})
            reverse_attrs = graph.get_edge_data(target, source, default={})
            has_forward = graph.has_edge(source, target)
            has_reverse = graph.has_edge(target, source)
            editor_key = f"{source}_{target}_{st.session_state.edge_editor_revision}"

            if has_forward or has_reverse:
                existing_directions = []
                if has_forward:
                    existing_directions.append(f"{source} → {target}")
                if has_reverse:
                    existing_directions.append(f"{target} → {source}")
                st.caption(f"Загружены существующие связи: {', '.join(existing_directions)}.")

            connection_type = st.radio(
                "Направление связи",
                ("Прямая →", "Двусторонняя ↔", "Обратная ←"),
                index=1 if has_forward and has_reverse else (2 if has_reverse else 0),
                horizontal=True,
                key=f"edge_direction_{editor_key}",
            )
            with st.form(f"edge_form_{editor_key}"):
                forward_weight = st.number_input(
                    f"Вес {source} → {target}",
                    min_value=-1.0,
                    max_value=1.0,
                    value=float(forward_attrs.get("weight", 0.5)),
                    step=0.05,
                    format="%.2f",
                    disabled=connection_type.startswith("Обратная"),
                )
                reverse_weight = st.number_input(
                    f"Вес {target} → {source}",
                    min_value=-1.0,
                    max_value=1.0,
                    value=float(reverse_attrs.get("weight", 0.5)),
                    step=0.05,
                    format="%.2f",
                    disabled=connection_type.startswith("Прямая"),
                )
                bold_arrow = st.checkbox(
                    f"Жирная стрелка {source} → {target}",
                    value=bool(forward_attrs.get("bold", False)),
                    disabled=connection_type.startswith("Обратная"),
                )
                reverse_bold_arrow = st.checkbox(
                    f"Жирная стрелка {target} → {source}",
                    value=bool(reverse_attrs.get("bold", False)),
                    disabled=connection_type.startswith("Прямая"),
                )
                submitted = st.form_submit_button("Добавить / обновить связь", width="stretch")
                if submitted:
                    try:
                        if not connection_type.startswith("Обратная"):
                            add_connection(
                                graph,
                                source,
                                target,
                                forward_weight,
                                bold=bold_arrow,
                            )
                        if not connection_type.startswith("Прямая"):
                            add_connection(
                                graph,
                                target,
                                source,
                                reverse_weight,
                                bold=reverse_bold_arrow,
                            )
                        if connection_type.startswith("Прямая") and graph.has_edge(target, source):
                            graph.remove_edge(target, source)
                        elif connection_type.startswith("Обратная") and graph.has_edge(source, target):
                            graph.remove_edge(source, target)
                    except GraphWorkbookError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.edge_editor_revision += 1
                        st.session_state.status = f"Связь обновлена: {connection_type}."
                        st.rerun()

        if graph.number_of_edges():
            edge_records = list(graph.edges(data=True))
            edge_options = [
                f"{source} → {target}  ({float(attrs.get('weight', 0.0)):+.2f})"
                for source, target, attrs in edge_records
            ]
            selected_edge = st.selectbox("Удалить связь", edge_options, key="delete_edge")
            if st.button("Удалить выбранную связь", width="stretch"):
                edge_index = edge_options.index(selected_edge)
                source, target, _attrs = edge_records[edge_index]
                graph.remove_edge(source, target)
                st.session_state.edge_editor_revision += 1
                st.session_state.status = f"Связь {source} → {target} удалена."
                st.rerun()

    with st.sidebar.expander("3. Excel-база", expanded=True):
        uploaded = st.file_uploader("Загрузить .xlsx", type=("xlsx",))
        if st.button("Загрузить выбранный файл", width="stretch", disabled=uploaded is None):
            try:
                loaded = load_graph_from_excel_bytes(uploaded.getvalue())
            except GraphWorkbookError as exc:
                st.error(str(exc))
            else:
                st.session_state.graph = loaded
                reset_next_node_id(loaded)
                ensure_positions(loaded)
                st.session_state.edge_editor_revision += 1
                st.session_state.status = f"Загружен файл {uploaded.name}."
                st.rerun()

        if DEFAULT_DATABASE.exists() and st.button("Загрузить graph_database.xlsx", width="stretch"):
            try:
                loaded = load_graph_from_excel(DEFAULT_DATABASE)
            except GraphWorkbookError as exc:
                st.error(str(exc))
            else:
                st.session_state.graph = loaded
                reset_next_node_id(loaded)
                ensure_positions(loaded)
                st.session_state.edge_editor_revision += 1
                st.session_state.status = f"Загружено: {DEFAULT_DATABASE.name}."
                st.rerun()

        if st.button("Восстановить из автосохранения", width="stretch"):
            if not AUTOSAVE_DATABASE.exists():
                st.warning("Файл автосохранения ещё не создан.")
            else:
                try:
                    loaded = load_graph_from_excel(AUTOSAVE_DATABASE)
                except GraphWorkbookError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.graph = loaded
                    reset_next_node_id(loaded)
                    ensure_positions(loaded)
                    st.session_state.edge_editor_revision += 1
                    st.session_state.last_autosave_monotonic = time.monotonic()
                    st.session_state.status = f"Восстановлено из {AUTOSAVE_DATABASE.name}."
                    st.rerun()

        if st.button("Сохранить в папку проекта", width="stretch"):
            save_graph_to_excel(graph, DEFAULT_DATABASE)
            st.session_state.status = f"Сохранено: {DEFAULT_DATABASE}."
            st.rerun()

        st.download_button(
            "Скачать Excel",
            data=graph_to_excel_bytes(graph),
            file_name="graph_database.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
        run_autosave(graph)


@st.dialog("Перестроить граф?")
def confirm_graph_rebuild() -> None:
    st.warning("Текущее расположение вершин будет заменено новым.")
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Да, перестроить", type="primary", width="stretch"):
            graph: nx.DiGraph = st.session_state.graph
            st.session_state.layout_seed += 1
            crossings = apply_spring_layout(
                graph,
                seed=st.session_state.layout_seed,
                minimum_distance=float(st.session_state.node_spacing),
            )
            st.session_state.status = (
                "Выбран лучший вариант перестроения: "
                f"пересечений независимых линий — {crossings}."
            )
            st.rerun()
    with cancel_col:
        if st.button("Отмена", width="stretch"):
            st.rerun()


def render_main(graph: nx.DiGraph) -> None:
    title_col, action_col = st.columns([4, 1])
    with title_col:
        st.title("Когнитивный граф")
        st.caption("NetworkX · ориентированные связи · вес от −1 до 1, включая 0")
    with action_col:
        if st.button("Перестроить граф", width="stretch"):
            confirm_graph_rebuild()

    st.info(st.session_state.status)
    if graph:
        ensure_positions(graph)
        ensure_node_colors(graph)
        render_draggable_graph(
            graph,
            key=GRAPH_COMPONENT_KEY,
            on_positions_change=sync_dragged_positions,
            on_selected_node_change=sync_selected_node,
            on_selected_edge_change=sync_selected_edge,
            node_spacing=float(st.session_state.node_spacing),
            on_node_spacing_change=sync_node_spacing,
        )
        st.caption(
            "Перетаскивайте вершины мышью · двигайте поле за пустое место · меняйте масштаб колёсиком "
            "или кнопками · ненулевая стрелка окрашена в цвет вершины назначения · нулевая связь серая"
        )
    else:
        st.warning("Граф пока пуст. Добавьте вершину в панели слева.")

    positive = sum(1 for _, _, data in graph.edges(data=True) if float(data.get("weight", 0.0)) > 0)
    negative = sum(1 for _, _, data in graph.edges(data=True) if float(data.get("weight", 0.0)) < 0)
    zero = graph.number_of_edges() - positive - negative
    metric_cols = st.columns(4)
    metric_cols[0].metric("Вершины", graph.number_of_nodes())
    metric_cols[1].metric("Положительные", positive)
    metric_cols[2].metric("Отрицательные", negative)
    metric_cols[3].metric("Нулевые", zero)


def sync_dragged_positions() -> None:
    positions = component_state_value("positions")
    graph: nx.DiGraph | None = st.session_state.get("graph")
    if graph is not None and apply_position_updates(graph, positions):
        st.session_state.status = "Новое расположение вершин сохранено в текущем графе."


def sync_node_spacing() -> None:
    value = component_state_value("node_spacing")
    try:
        spacing = float(value)
    except (TypeError, ValueError):
        return
    st.session_state.node_spacing = max(60.0, min(500.0, spacing))
    st.session_state.status = (
        f"Интервал между вершинами: {st.session_state.node_spacing:.0f}. "
        "Нажмите «Перестроить граф», чтобы применить."
    )


def main() -> None:
    st.set_page_config(page_title="Когнитивный граф", page_icon="🕸️", layout="wide")
    initialize_state()
    graph: nx.DiGraph = st.session_state.graph
    render_sidebar(graph)
    render_main(graph)


if __name__ == "__main__":
    main()
