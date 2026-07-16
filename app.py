from __future__ import annotations

import math
from pathlib import Path

import networkx as nx
import plotly.graph_objects as go
import streamlit as st

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
ZERO_EDGE_COLOR = "#7A7F87"\


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


def contrast_text_color(color: str) -> str:
    normalized = validate_color(color)
    red = int(normalized[1:3], 16)
    green = int(normalized[3:5], 16)
    blue = int(normalized[5:7], 16)
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#17212B" if luminance >= 155 else "#FFFFFF"


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


def quadratic_point(
    start: tuple[float, float],
    control: tuple[float, float],
    end: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    remaining = 1.0 - t
    return (
        remaining * remaining * start[0] + 2.0 * remaining * t * control[0] + t * t * end[0],
        remaining * remaining * start[1] + 2.0 * remaining * t * control[1] + t * t * end[1],
    )


def graph_figure(graph: nx.DiGraph) -> go.Figure:
    ensure_positions(graph)
    ensure_node_colors(graph)
    figure = go.Figure()
    shapes: list[dict] = []
    annotations: list[dict] = []

    for source, target, attrs in graph.edges(data=True):
        x1 = float(graph.nodes[source]["x"])
        y1 = float(graph.nodes[source]["y"])
        x2 = float(graph.nodes[target]["x"])
        y2 = float(graph.nodes[target]["y"])
        dx, dy = x2 - x1, y2 - y1
        distance = max(math.hypot(dx, dy), 0.001)
        normal_x, normal_y = -dy / distance, dx / distance
        offset = 0.055 if graph.has_edge(target, source) else 0.0
        curve_x = (x1 + x2) / 2 + normal_x * offset
        curve_y = (y1 + y2) / 2 + normal_y * offset
        control = (curve_x, curve_y)
        node_radius = 0.052
        start_tangent = (curve_x - x1, curve_y - y1)
        end_tangent = (x2 - curve_x, y2 - curve_y)
        start_length = max(math.hypot(*start_tangent), 0.001)
        end_length = max(math.hypot(*end_tangent), 0.001)
        start = (
            x1 + node_radius * start_tangent[0] / start_length,
            y1 + node_radius * start_tangent[1] / start_length,
        )
        end = (
            x2 - node_radius * end_tangent[0] / end_length,
            y2 - node_radius * end_tangent[1] / end_length,
        )
        weight = float(attrs.get("weight", 0.0))
        is_zero = math.isclose(weight, 0.0, abs_tol=1e-12)
        color = ZERO_EDGE_COLOR if is_zero else validate_color(
            graph.nodes[target].get("color", DEFAULT_NODE_COLOR)
        )
        width = 5.0 if bool(attrs.get("bold", False)) else 2.0
        shapes.append(
            {
                "type": "path",
                "path": f"M {start[0]},{start[1]} Q {control[0]},{control[1]} {end[0]},{end[1]}",
                "line": {"color": color, "width": width, "dash": "solid"},
                "layer": "below",
            }
        )
        if not is_zero:
            arrow_start = quadratic_point(start, control, end, 0.91)
            arrow_end = quadratic_point(start, control, end, 1.0)
            annotations.append(
                {
                    "x": arrow_end[0],
                    "y": arrow_end[1],
                    "ax": arrow_start[0],
                    "ay": arrow_start[1],
                    "xref": "x",
                    "yref": "y",
                    "axref": "x",
                    "ayref": "y",
                    "text": "",
                    "showarrow": True,
                    "arrowhead": 3,
                    "arrowsize": 2,
                    "arrowwidth": width,
                    "arrowcolor": color,
                }
            )
        label_point = quadratic_point(start, control, end, 0.5)
        annotations.append(
            {
                "x": label_point[0] + normal_x * 0.025,
                "y": label_point[1] + normal_y * 0.025,
                "text": f"<b>{weight:+.2f}</b>",
                "showarrow": False,
                "font": {"size": 12, "color": color},
                "bgcolor": "rgba(255,255,255,0.82)",
                "borderpad": 2,
            }
        )

    node_ids = list(graph.nodes)
    node_labels = [str(graph.nodes[node].get("label", node)) for node in node_ids]
    node_colors = [validate_color(graph.nodes[node].get("color", DEFAULT_NODE_COLOR)) for node in node_ids]
    node_text_colors = [contrast_text_color(color) for color in node_colors]
    if node_ids:
        figure.add_trace(
            go.Scatter(
                x=[float(graph.nodes[node]["x"]) for node in node_ids],
                y=[float(graph.nodes[node]["y"]) for node in node_ids],
                mode="markers+text",
                text=node_labels,
                textposition="middle center",
                customdata=node_ids,
                hovertemplate="<b>%{text}</b><br>ID: %{customdata}<extra></extra>",
                marker={"size": 62, "color": node_colors, "line": {"color": "#243B53", "width": 2}},
                textfont={"size": 11, "color": node_text_colors},
                name="Вершины",
            )
        )

    figure.update_layout(
        height=650,
        margin={"l": 15, "r": 15, "t": 15, "b": 15},
        showlegend=False,
        shapes=shapes,
        annotations=annotations,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#f7f9fc",
        xaxis={"range": [-0.05, 1.05], "visible": False, "fixedrange": True},
        yaxis={"range": [-0.05, 1.05], "visible": False, "fixedrange": True, "scaleanchor": "x", "scaleratio": 1},
        hovermode="closest",
        dragmode="pan",
    )
    return figure


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
                bold_arrow = st.checkbox("Жирная стрелка")
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
                            bidirectional=connection_type.startswith("Двусторонняя"),
                            reverse_weight=reverse_weight,
                            bold=bold_arrow,
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
        st.plotly_chart(graph_figure(graph), width="stretch", config={"displayModeBar": False})
        st.caption("Ненулевая стрелка окрашена в цвет вершины назначения · нулевая связь серая и без наконечника")
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


def main() -> None:
    st.set_page_config(page_title="Когнитивный граф", page_icon="🕸️", layout="wide")
    initialize_state()
    graph: nx.DiGraph = st.session_state.graph
    render_sidebar(graph)
    render_main(graph)


if __name__ == "__main__":
    main()
