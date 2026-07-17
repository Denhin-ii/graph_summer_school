from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import networkx as nx
import streamlit as st

from graph_store import DEFAULT_NODE_COLOR, validate_color


GRAPH_EDITOR_HTML = """
<div class="graph-editor-root">
  <div class="graph-controls" aria-label="Управление видом графа">
    <button type="button" class="graph-zoom-out" title="Уменьшить" aria-label="Уменьшить">−</button>
    <span class="graph-zoom-value" aria-live="polite">100%</span>
    <button type="button" class="graph-zoom-in" title="Увеличить" aria-label="Увеличить">+</button>
    <button type="button" class="graph-reset-view" title="Сбросить масштаб и сдвиг">Сбросить вид</button>
    <button type="button" class="graph-toggle-grid" title="Включить или отключить сетку"
            aria-pressed="true">Сетка: вкл.</button>
  </div>
  <svg class="graph-editor-canvas" viewBox="0 0 1100 650" role="application"
       aria-label="Интерактивный редактор расположения вершин графа">
    <defs></defs>
    <rect class="graph-background" x="0" y="0" width="1100" height="650" />
    <g class="graph-viewport">
      <g class="graph-edges"></g>
      <g class="graph-nodes"></g>
    </g>
  </svg>
</div>
"""

GRAPH_EDITOR_CSS = """
.graph-editor-root {
  position: relative;
  width: 100%;
  height: 650px;
  overflow: hidden;
  border: 1px solid #D9E2EC;
  border-radius: 0.5rem;
  background-color: #F7F9FC;
  background-image:
    linear-gradient(to right, #D9E2EC 1px, transparent 1px),
    linear-gradient(to bottom, #D9E2EC 1px, transparent 1px);
  background-size: 25px 25px;
}

.graph-editor-root.graph-grid-hidden {
  background-image: none;
}

.graph-controls {
  position: absolute;
  z-index: 2;
  top: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px;
  border: 1px solid #BCCCDC;
  border-radius: 0.5rem;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 2px 6px rgba(36, 59, 83, 0.14);
}

.graph-controls button {
  min-width: 34px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #9FB3C8;
  border-radius: 0.35rem;
  background: #FFFFFF;
  color: #243B53;
  font: 600 14px sans-serif;
  cursor: pointer;
}

.graph-controls button:hover {
  border-color: #486581;
  background: #F0F4F8;
}

.graph-controls button:focus-visible {
  outline: 3px solid rgba(76, 120, 168, 0.35);
  outline-offset: 1px;
}

.graph-zoom-value {
  min-width: 48px;
  color: #486581;
  font: 600 12px sans-serif;
  text-align: center;
}

.graph-editor-canvas {
  display: block;
  width: 100%;
  height: 100%;
  touch-action: none;
  user-select: none;
}

.graph-background {
  fill: transparent;
  pointer-events: all;
  cursor: grab;
}

.graph-background:active {
  cursor: grabbing;
}

.graph-edge-label {
  paint-order: stroke;
  stroke: rgba(255, 255, 255, 0.92);
  stroke-width: 7px;
  stroke-linejoin: round;
  font: 700 13px sans-serif;
  text-anchor: middle;
  dominant-baseline: central;
  pointer-events: none;
}

.graph-node {
  cursor: grab;
  outline: none;
}

.graph-node:active {
  cursor: grabbing;
}

.graph-node circle {
  stroke: #243B53;
  stroke-width: 2.5px;
  filter: drop-shadow(0 2px 2px rgba(36, 59, 83, 0.18));
}

.graph-node:hover circle,
.graph-node:focus circle {
  stroke: #102A43;
  stroke-width: 4px;
}

.graph-node text {
  font: 600 11px sans-serif;
  text-anchor: middle;
  pointer-events: none;
}
"""

GRAPH_EDITOR_JS = r"""
const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 1100;
const HEIGHT = 650;
const PAD_X = 50;
const PAD_Y = 50;
const PLOT_WIDTH = WIDTH - 2 * PAD_X;
const PLOT_HEIGHT = HEIGHT - 2 * PAD_Y;
const NODE_RADIUS = 40;
const LABEL_LINE_LENGTH = 11;
const LABEL_MAX_LINES = 4;

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function screenPosition(position) {
  return {
    x: PAD_X + position.x * PLOT_WIDTH,
    y: HEIGHT - PAD_Y - position.y * PLOT_HEIGHT,
  };
}

function localPosition(element, event) {
  const canvas = element.ownerSVGElement || element;
  const point = canvas.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(element.getScreenCTM().inverse());
}

function graphPosition(viewport, event) {
  const local = localPosition(viewport, event);
  return {
    x: Math.max(0, Math.min(1, (local.x - PAD_X) / PLOT_WIDTH)),
    y: Math.max(0, Math.min(1, (HEIGHT - PAD_Y - local.y) / PLOT_HEIGHT)),
  };
}

function contrastColor(color) {
  const red = parseInt(color.slice(1, 3), 16);
  const green = parseInt(color.slice(3, 5), 16);
  const blue = parseInt(color.slice(5, 7), 16);
  return 0.299 * red + 0.587 * green + 0.114 * blue >= 155
    ? "#17212B"
    : "#FFFFFF";
}

function labelLines(label) {
  const words = String(label).trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [""];
  const pieces = words.flatMap((word) => {
    const chunks = [];
    for (let index = 0; index < word.length; index += LABEL_LINE_LENGTH) {
      chunks.push(word.slice(index, index + LABEL_LINE_LENGTH));
    }
    return chunks;
  });
  const lines = [];
  let current = "";
  for (const word of pieces) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= LABEL_LINE_LENGTH || !current) {
      current = candidate;
    } else {
      lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);
  if (lines.length <= LABEL_MAX_LINES) return lines;
  const visible = lines.slice(0, LABEL_MAX_LINES);
  visible[LABEL_MAX_LINES - 1] = `${visible[LABEL_MAX_LINES - 1].slice(0, LABEL_LINE_LENGTH - 1)}…`;
  return visible;
}

export default function(component) {
  const { parentElement, data, setStateValue } = component;
  const root = parentElement.querySelector(".graph-editor-root");
  const svg = parentElement.querySelector(".graph-editor-canvas");
  const viewport = parentElement.querySelector(".graph-viewport");
  const edgesLayer = parentElement.querySelector(".graph-edges");
  const nodesLayer = parentElement.querySelector(".graph-nodes");
  const background = parentElement.querySelector(".graph-background");
  const zoomOutButton = parentElement.querySelector(".graph-zoom-out");
  const zoomInButton = parentElement.querySelector(".graph-zoom-in");
  const resetViewButton = parentElement.querySelector(".graph-reset-view");
  const toggleGridButton = parentElement.querySelector(".graph-toggle-grid");
  const zoomValue = parentElement.querySelector(".graph-zoom-value");
  const nodes = new Map(
    (data.nodes || []).map((node) => [node.id, { ...node, x: Number(node.x), y: Number(node.y) }])
  );
  const edges = data.edges || [];
  let activeDrag = null;
  let activePan = null;
  let zoom = Number(svg.dataset.zoom || 1);
  let panX = Number(svg.dataset.panX || 0);
  let panY = Number(svg.dataset.panY || 0);
  let gridVisible = svg.dataset.gridVisible !== "false";

  function applyGridVisibility() {
    root.classList.toggle("graph-grid-hidden", !gridVisible);
    toggleGridButton.textContent = gridVisible ? "Сетка: вкл." : "Сетка: выкл.";
    toggleGridButton.setAttribute("aria-pressed", String(gridVisible));
    svg.dataset.gridVisible = String(gridVisible);
  }

  function applyView() {
    viewport.setAttribute("transform", `translate(${panX} ${panY}) scale(${zoom})`);
    zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    svg.dataset.zoom = String(zoom);
    svg.dataset.panX = String(panX);
    svg.dataset.panY = String(panY);
  }

  function setZoom(nextZoom, centerX = WIDTH / 2, centerY = HEIGHT / 2) {
    const boundedZoom = Math.max(0.5, Math.min(3, nextZoom));
    if (Math.abs(boundedZoom - zoom) < 0.0001) return;
    const contentX = (centerX - panX) / zoom;
    const contentY = (centerY - panY) / zoom;
    panX = centerX - contentX * boundedZoom;
    panY = centerY - contentY * boundedZoom;
    zoom = boundedZoom;
    applyView();
  }

  function onWheel(event) {
    event.preventDefault();
    const center = localPosition(svg, event);
    setZoom(zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), center.x, center.y);
  }

  function startPan(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    const point = localPosition(svg, event);
    activePan = {
      pointerId: event.pointerId,
      startX: point.x,
      startY: point.y,
      initialPanX: panX,
      initialPanY: panY,
    };
    svg.setPointerCapture(event.pointerId);
  }

  function clearLayer(layer) {
    while (layer.firstChild) layer.removeChild(layer.firstChild);
  }

  function edgeGeometry(edge) {
    const source = screenPosition(nodes.get(edge.source));
    const target = screenPosition(nodes.get(edge.target));
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 0.001);
    const normalX = -dy / distance;
    const normalY = dx / distance;
    const offset = edge.reciprocal ? 38 : 0;
    const control = {
      x: (source.x + target.x) / 2 + normalX * offset,
      y: (source.y + target.y) / 2 + normalY * offset,
    };
    const startVector = { x: control.x - source.x, y: control.y - source.y };
    const endVector = { x: target.x - control.x, y: target.y - control.y };
    const startLength = Math.max(Math.hypot(startVector.x, startVector.y), 0.001);
    const endLength = Math.max(Math.hypot(endVector.x, endVector.y), 0.001);
    const start = {
      x: source.x + NODE_RADIUS * startVector.x / startLength,
      y: source.y + NODE_RADIUS * startVector.y / startLength,
    };
    const end = {
      x: target.x - NODE_RADIUS * endVector.x / endLength,
      y: target.y - NODE_RADIUS * endVector.y / endLength,
    };
    return { start, control, end, normalX, normalY };
  }

  function drawEdges() {
    clearLayer(edgesLayer);
    edges.forEach((edge, index) => {
      if (!nodes.has(edge.source) || !nodes.has(edge.target)) return;
      const geometry = edgeGeometry(edge);
      const color = edge.zero ? "#7A7F87" : edge.color;
      const width = edge.bold ? 5 : 2;
      const group = svgElement("g");
      const path = svgElement("path", {
        d: `M ${geometry.start.x},${geometry.start.y} Q ${geometry.control.x},${geometry.control.y} ${geometry.end.x},${geometry.end.y}`,
        fill: "none",
        stroke: color,
        "stroke-width": width,
      });
      group.appendChild(path);

      if (!edge.zero) {
        const markerId = `graph-arrow-${component.key}-${index}`.replace(/[^a-zA-Z0-9_-]/g, "-");
        const defs = svg.querySelector("defs");
        const oldMarker = defs.querySelector(`#${CSS.escape(markerId)}`);
        if (oldMarker) oldMarker.remove();
        const marker = svgElement("marker", {
          id: markerId,
          viewBox: "0 0 12 12",
          refX: 10,
          refY: 6,
          markerWidth: 12,
          markerHeight: 12,
          orient: "auto-start-reverse",
          markerUnits: "userSpaceOnUse",
        });
        marker.appendChild(svgElement("path", { d: "M 0 0 L 12 6 L 0 12 z", fill: color }));
        defs.appendChild(marker);
        path.setAttribute("marker-end", `url(#${markerId})`);
      }

      const midX = 0.25 * geometry.start.x + 0.5 * geometry.control.x + 0.25 * geometry.end.x;
      const midY = 0.25 * geometry.start.y + 0.5 * geometry.control.y + 0.25 * geometry.end.y;
      const label = svgElement("text", {
        x: midX + geometry.normalX * 18,
        y: midY + geometry.normalY * 18,
        fill: color,
        class: "graph-edge-label",
      });
      label.textContent = `${Number(edge.weight) >= 0 ? "+" : ""}${Number(edge.weight).toFixed(2)}`;
      group.appendChild(label);
      edgesLayer.appendChild(group);
    });
  }

  function positionNode(group, node) {
    const point = screenPosition(node);
    group.setAttribute("transform", `translate(${point.x} ${point.y})`);
  }

  function drawNodes() {
    clearLayer(nodesLayer);
    for (const node of nodes.values()) {
      const group = svgElement("g", {
        class: "graph-node",
        tabindex: "0",
        role: "button",
        "aria-label": `${node.label}. Перетащите вершину мышью.`,
        "data-node-id": node.id,
      });
      group.appendChild(svgElement("circle", { r: NODE_RADIUS, fill: node.color }));
      const title = svgElement("title");
      title.textContent = `${node.label} (${node.id})`;
      group.appendChild(title);
      const lines = labelLines(node.label);
      const text = svgElement("text", { fill: contrastColor(node.color) });
      lines.forEach((line, index) => {
        const tspan = svgElement("tspan", {
          x: 0,
          y: (index - (lines.length - 1) / 2) * 13,
        });
        tspan.textContent = line;
        text.appendChild(tspan);
      });
      group.appendChild(text);
      positionNode(group, node);
      group.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        activeDrag = { id: node.id, pointerId: event.pointerId, group };
        svg.setPointerCapture(event.pointerId);
        group.style.cursor = "grabbing";
      });
      nodesLayer.appendChild(group);
    }
  }

  function persistPositions() {
    const positions = {};
    for (const [id, node] of nodes) {
      positions[id] = {
        x: Number(node.x.toFixed(6)),
        y: Number(node.y.toFixed(6)),
      };
    }
    setStateValue("positions", positions);
  }

  function onPointerMove(event) {
    if (activePan && event.pointerId === activePan.pointerId) {
      event.preventDefault();
      const point = localPosition(svg, event);
      panX = activePan.initialPanX + point.x - activePan.startX;
      panY = activePan.initialPanY + point.y - activePan.startY;
      applyView();
      return;
    }
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    event.preventDefault();
    const position = graphPosition(viewport, event);
    const node = nodes.get(activeDrag.id);
    node.x = position.x;
    node.y = position.y;
    positionNode(activeDrag.group, node);
    drawEdges();
  }

  function finishDrag(event) {
    if (activePan && event.pointerId === activePan.pointerId) {
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      activePan = null;
      return;
    }
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    activeDrag.group.style.cursor = "grab";
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    activeDrag = null;
    persistPositions();
  }

  applyView();
  applyGridVisibility();
  drawEdges();
  drawNodes();
  background.addEventListener("pointerdown", startPan);
  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", finishDrag);
  svg.addEventListener("pointercancel", finishDrag);
  zoomOutButton.onclick = () => setZoom(zoom / 1.25);
  zoomInButton.onclick = () => setZoom(zoom * 1.25);
  resetViewButton.onclick = () => {
    zoom = 1;
    panX = 0;
    panY = 0;
    applyView();
  };
  toggleGridButton.onclick = () => {
    gridVisible = !gridVisible;
    applyGridVisibility();
  };

  return () => {
    background.removeEventListener("pointerdown", startPan);
    svg.removeEventListener("wheel", onWheel);
    svg.removeEventListener("pointermove", onPointerMove);
    svg.removeEventListener("pointerup", finishDrag);
    svg.removeEventListener("pointercancel", finishDrag);
    zoomOutButton.onclick = null;
    zoomInButton.onclick = null;
    resetViewButton.onclick = null;
    toggleGridButton.onclick = null;
  };
}
"""


draggable_graph_component = st.components.v2.component(
    "draggable_graph_editor",
    html=GRAPH_EDITOR_HTML,
    css=GRAPH_EDITOR_CSS,
    js=GRAPH_EDITOR_JS,
)


def apply_position_updates(graph: nx.DiGraph, positions: Any) -> int:
    """Apply validated coordinates received from the browser component."""
    if not isinstance(positions, Mapping):
        return 0

    changed = 0
    for node_id, position in positions.items():
        if node_id not in graph or not isinstance(position, Mapping):
            continue
        try:
            x = float(position["x"])
            y = float(position["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        try:
            old_x = float(graph.nodes[node_id].get("x", x))
            old_y = float(graph.nodes[node_id].get("y", y))
        except (TypeError, ValueError):
            old_x, old_y = x, y
        if not math.isclose(old_x, x, abs_tol=1e-9) or not math.isclose(old_y, y, abs_tol=1e-9):
            graph.nodes[node_id]["x"] = x
            graph.nodes[node_id]["y"] = y
            changed += 1
    return changed


def render_draggable_graph(
    graph: nx.DiGraph,
    *,
    key: str,
    on_positions_change: Callable[[], None],
) -> None:
    nodes = [
        {
            "id": str(node_id),
            "label": str(attrs.get("label", node_id)),
            "color": validate_color(attrs.get("color", DEFAULT_NODE_COLOR)),
            "x": float(attrs["x"]),
            "y": float(attrs["y"]),
        }
        for node_id, attrs in graph.nodes(data=True)
    ]
    edges = [
        {
            "source": str(source),
            "target": str(target),
            "weight": float(attrs.get("weight", 0.0)),
            "bold": bool(attrs.get("bold", False)),
            "zero": math.isclose(float(attrs.get("weight", 0.0)), 0.0, abs_tol=1e-12),
            "reciprocal": graph.has_edge(target, source),
            "color": validate_color(graph.nodes[target].get("color", DEFAULT_NODE_COLOR)),
        }
        for source, target, attrs in graph.edges(data=True)
    ]
    positions = {node["id"]: {"x": node["x"], "y": node["y"]} for node in nodes}
    draggable_graph_component(
        key=key,
        data={"nodes": nodes, "edges": edges},
        default={"positions": positions},
        height=650,
        on_positions_change=on_positions_change,
    )
