from __future__ import annotations

import math
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
    save_graph_to_excel,
    validate_color,
)


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = APP_DIR / "graph_database.xlsx"
GRAPH_COMPONENT_KEY = "draggable_graph"


def initialize_state() -> None:
    if "graph" not in st.session_state:
        st.session_state.graph = nx.DiGraph()
    if "next_node_id" not in st.session_state:
        st.session_state.next_node_id = 1
    if "status" not in st.session_state:
        st.session_state.status = "Создайте первую вершину или загрузите Excel-файл."

    if "layout_seed" not in st.session_state:
        st.session_state.layout_seed = 42

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
    layout = nx.spring_layout(graph, seed=seed, weight=None)
    xs = [float(point[0]) for point in layout.values()]
    ys = [float(point[1]) for point in layout.values()]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    for node_id, point in layout.items():
        graph.nodes[node_id]["x"] = normalize(float(point[0]), x_min, x_max)
        graph.nodes[node_id]["y"] = normalize(float(point[1]), y_min, y_max)


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
                st.session_state.status = f"Вершина «{label_value}» и её связи удалены."
                st.rerun()
    with st.sidebar.expander("2. Связи", expanded=True):
        if len(graph) < 2:
            st.caption("Для связи нужны минимум две вершины.")
        else:
            options = [node_option(graph, node) for node in graph]
            with st.form("edge_form"):
                source_option = st.selectbox("Из вершины", options, index=0)
                target_option = st.selectbox("В вершину", options, index=1)
                connection_type = st.radio("Направление связи", ("Односторонняя →", "Двусторонняя ↔"), horizontal=True)
                forward_weight = st.number_input(
                    "Вес вперёд", min_value=-1.0, max_value=1.0, value=0.5, step=0.05, format="%.2f"
                )
                reverse_weight = st.number_input(
                    "Вес обратно", min_value=-1.0, max_value=1.0, value=0.5, step=0.05, format="%.2f",
                    disabled=connection_type.startswith("Односторонняя"),
                )
                bold_arrow = st.checkbox("Жирная стрелка вперёд")
                reverse_bold_arrow = st.checkbox(
                    "Жирная стрелка обратно",
                    disabled=connection_type.startswith("Односторонняя"),
                )
                submitted = st.form_submit_button("Добавить / обновить связь", width="stretch")
                if submitted:
                    source = node_id_from_option(source_option)
                    target = node_id_from_option(target_option)
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
                st.session_state.status = f"Загружено: {DEFAULT_DATABASE.name}."
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


def render_main(graph: nx.DiGraph) -> None:
    title_col, action_col = st.columns([4, 1])
    with title_col:
        st.title("Когнитивный граф")
        st.caption("NetworkX · ориентированные связи · вес от −1 до 1, включая 0")
    with action_col:
        if st.button("Перестроить граф", width="stretch"):
            st.session_state.layout_seed += 1
            apply_spring_layout(graph, seed=st.session_state.layout_seed)
            st.session_state.status = "Расположение пересчитано алгоритмом spring_layout."
            st.rerun()

    st.info(st.session_state.status)
    if graph:
        ensure_positions(graph)
        ensure_node_colors(graph)
        render_draggable_graph(
            graph,
            key=GRAPH_COMPONENT_KEY,
            on_positions_change=sync_dragged_positions,
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
    component_state = st.session_state.get(GRAPH_COMPONENT_KEY, {})
    if hasattr(component_state, "get"):
        positions = component_state.get("positions")
    else:
        positions = getattr(component_state, "positions", None)
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
