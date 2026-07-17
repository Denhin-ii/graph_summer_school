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
MIN_NODE_CENTER_DISTANCE = 96.0
MIN_NODE_EDGE_DISTANCE = 82.0


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

def apply_spring_layout(graph: nx.DiGraph, seed: int = 42) -> None:
    if not graph:
        return
    if len(graph) == 1:
        node = next(iter(graph))
        graph.nodes[node]["x"] = graph.nodes[node]["y"] = 0.5
        return
    ideal_distance = max(0.25, 1.0 / math.sqrt(len(graph)))
    layout = nx.spring_layout(
        graph,
        seed=seed,
        weight=None,
        k=ideal_distance,
        iterations=200,
    )
    xs = [float(point[0]) for point in layout.values()]
    ys = [float(point[1]) for point in layout.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    for node_id, point in layout.items():
        graph.nodes[node_id]["x"] = normalize(float(point[0]), x_min, x_max)
        graph.nodes[node_id]["y"] = normalize(float(point[1]), y_min, y_max)
    for _ in range(6):
        separate_close_nodes(graph, iterations=40)
        separate_nodes_from_edges(graph, iterations=40)


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
                graph.nodes[first]["x"] = max(0.04, min(0.96, first_x - unit_x * shift / LAYOUT_WIDTH))
                graph.nodes[first]["y"] = max(0.04, min(0.96, first_y - unit_y * shift / LAYOUT_HEIGHT))
                graph.nodes[second]["x"] = max(0.04, min(0.96, second_x + unit_x * shift / LAYOUT_WIDTH))
                graph.nodes[second]["y"] = max(0.04, min(0.96, second_y + unit_y * shift / LAYOUT_HEIGHT))
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
                graph.nodes[node_id]["x"] = max(0.04, min(0.96, (node_x + unit_x * shift) / LAYOUT_WIDTH))
                graph.nodes[node_id]["y"] = max(0.04, min(0.96, (node_y + unit_y * shift) / LAYOUT_HEIGHT))
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
    node_id = component_state_value("selected_node")
    if graph is None or node_id not in graph:
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
            options = [node_option(graph, node) for node in graph]
            source_option = st.selectbox("Из вершины", options, index=0, key="edge_source")
            source = node_id_from_option(source_option)
            target_options = [option for option in options if node_id_from_option(option) != source]
            target_option = st.selectbox("В вершину", target_options, index=0, key="edge_target")
            target = node_id_from_option(target_option)

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
                ("Односторонняя →", "Двусторонняя ↔"),
                index=1 if has_reverse else 0,
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
                )
                reverse_weight = st.number_input(
                    f"Вес {target} → {source}",
                    min_value=-1.0,
                    max_value=1.0,
                    value=float(reverse_attrs.get("weight", 0.5)),
                    step=0.05,
                    format="%.2f",
                    disabled=connection_type.startswith("Односторонняя"),
                )
                bold_arrow = st.checkbox(
                    f"Жирная стрелка {source} → {target}",
                    value=bool(forward_attrs.get("bold", False)),
                )
                reverse_bold_arrow = st.checkbox(
                    f"Жирная стрелка {target} → {source}",
                    value=bool(reverse_attrs.get("bold", False)),
                    disabled=connection_type.startswith("Односторонняя"),
                )
                submitted = st.form_submit_button("Добавить / обновить связь", width="stretch")
                if submitted:
                    try:
                        add_connection(
                            graph,
                            source,
                            target,
                            forward_weight,
                            bold=bold_arrow,
                        )
                        if connection_type.startswith("Двусторонняя"):
                            add_connection(
                                graph,
                                target,
                                source,
                                reverse_weight,
                                bold=reverse_bold_arrow,
                            )
                    except GraphWorkbookError as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.edge_editor_revision += 1
                        st.session_state.status = "Связь добавлена или обновлена."
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
            apply_spring_layout(graph, seed=st.session_state.layout_seed)
            st.session_state.status = "Расположение пересчитано алгоритмом spring_layout."
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


def main() -> None:
    st.set_page_config(page_title="Когнитивный граф", page_icon="🕸️", layout="wide")
    initialize_state()
    graph: nx.DiGraph = st.session_state.graph
    render_sidebar(graph)
    render_main(graph)


if __name__ == "__main__":
    main()
