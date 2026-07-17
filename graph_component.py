from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import networkx as nx
import streamlit as st

from graph_store import (
    DEFAULT_NODE_COLOR,
    MAX_NODE_COORDINATE,
    MIN_NODE_COORDINATE,
    validate_color,
)


GRAPH_EDITOR_HTML = """
<div class="graph-editor-root">
  <div class="graph-controls" aria-label="Управление видом графа">
    <button type="button" class="graph-zoom-out" title="Уменьшить" aria-label="Уменьшить">−</button>
    <span class="graph-zoom-value" aria-live="polite">100%</span>
    <button type="button" class="graph-zoom-in" title="Увеличить" aria-label="Увеличить">+</button>
    <button type="button" class="graph-reset-view" title="Показать весь граф">Весь граф</button>
    <button type="button" class="graph-toggle-grid" title="Включить или отключить сетку"
            aria-pressed="true">Сетка: вкл.</button>
    <button type="button" class="graph-toggle-fullscreen" title="Открыть граф на весь экран">На весь экран</button>
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

.graph-editor-root:fullscreen {
  width: 100vw;
  height: 100vh;
  border: 0;
  border-radius: 0;
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

.graph-edge-hit {
  cursor: pointer;
  pointer-events: stroke;
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
const MIN_NODE_COORDINATE = -1;
const MAX_NODE_COORDINATE = 2;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 3;
const FIT_PADDING = 70;

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
    x: Math.max(MIN_NODE_COORDINATE, Math.min(MAX_NODE_COORDINATE, (local.x - PAD_X) / PLOT_WIDTH)),
    y: Math.max(MIN_NODE_COORDINATE, Math.min(MAX_NODE_COORDINATE, (HEIGHT - PAD_Y - local.y) / PLOT_HEIGHT)),
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
  const zoomOutButton = parentElement.querySelector(".graph-zoom-out");
  const zoomInButton = parentElement.querySelector(".graph-zoom-in");
  const resetViewButton = parentElement.querySelector(".graph-reset-view");
  const toggleGridButton = parentElement.querySelector(".graph-toggle-grid");
  const toggleFullscreenButton = parentElement.querySelector(".graph-toggle-fullscreen");
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

  function getFullscreenElement() {
    const rootNode = root.getRootNode();
    return rootNode.fullscreenElement || document.fullscreenElement;
  }

  function updateFullscreenButton() {
    const fullscreen = getFullscreenElement() === root;
    toggleFullscreenButton.textContent = fullscreen ? "Выйти из полного экрана" : "На весь экран";
    toggleFullscreenButton.title = fullscreen ? "Выйти из полноэкранного режима" : "Открыть граф на весь экран";
  }

  async function toggleFullscreen() {
    try {
      if (getFullscreenElement() === root) {
        await document.exitFullscreen();
      } else {
        await root.requestFullscreen();
      }
    } catch (error) {
      console.warn("Не удалось изменить полноэкранный режим", error);
    }
  }

  function applyView() {
    viewport.setAttribute("transform", `translate(${panX} ${panY}) scale(${zoom})`);
    zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    svg.dataset.zoom = String(zoom);
    svg.dataset.panX = String(panX);
    svg.dataset.panY = String(panY);
  }

  function setZoom(nextZoom, centerX = WIDTH / 2, centerY = HEIGHT / 2) {
    const boundedZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, nextZoom));
    if (Math.abs(boundedZoom - zoom) < 0.0001) return;
    const contentX = (centerX - panX) / zoom;
    const contentY = (centerY - panY) / zoom;
    panX = centerX - contentX * boundedZoom;
    panY = centerY - contentY * boundedZoom;
    zoom = boundedZoom;
    applyView();
  }

  function fitGraphToView() {
    if (!nodes.size) {
      zoom = 1;
      panX = 0;
      panY = 0;
      applyView();
      return;
    }
    const points = Array.from(nodes.values(), screenPosition);
    const minX = Math.min(...points.map((point) => point.x)) - NODE_RADIUS;
    const maxX = Math.max(...points.map((point) => point.x)) + NODE_RADIUS;
    const minY = Math.min(...points.map((point) => point.y)) - NODE_RADIUS;
    const maxY = Math.max(...points.map((point) => point.y)) + NODE_RADIUS;
    const contentWidth = Math.max(maxX - minX, 1);
    const contentHeight = Math.max(maxY - minY, 1);
    zoom = Math.max(
      MIN_ZOOM,
      Math.min(
        MAX_ZOOM,
        (WIDTH - 2 * FIT_PADDING) / contentWidth,
        (HEIGHT - 2 * FIT_PADDING) / contentHeight,
      ),
    );
    panX = WIDTH / 2 - ((minX + maxX) / 2) * zoom;
    panY = HEIGHT / 2 - ((minY + maxY) / 2) * zoom;
    applyView();
  }

  function onWheel(event) {
    event.preventDefault();
    const center = localPosition(svg, event);
    const deltaScale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? HEIGHT : 1;
    const normalizedDelta = Math.max(-120, Math.min(120, event.deltaY * deltaScale));
    const zoomFactor = Math.exp(-normalizedDelta * 0.001);
    setZoom(zoom * zoomFactor, center.x, center.y);
  }

  function startPan(event) {
    if (event.button !== 0) return;
    if (event.target.closest(".graph-node")) return;
    event.preventDefault();
    const point = localPosition(svg, event);
    const edgeGroup = event.target.closest(".graph-edge");
    activePan = {
      pointerId: event.pointerId,
      startX: point.x,
      startY: point.y,
      initialPanX: panX,
      initialPanY: panY,
      moved: false,
      edgeSource: edgeGroup?.dataset.source || null,
      edgeTarget: edgeGroup?.dataset.target || null,
    };
    svg.setPointerCapture(event.pointerId);
  }

  function clearLayer(layer) {
    while (layer.firstChild) layer.removeChild(layer.firstChild);
  }

  function quadraticPoint(start, control, end, t) {
    const oneMinusT = 1 - t;
    return {
      x: oneMinusT * oneMinusT * start.x + 2 * oneMinusT * t * control.x + t * t * end.x,
      y: oneMinusT * oneMinusT * start.y + 2 * oneMinusT * t * control.y + t * t * end.y,
    };
  }

  function geometryWithOffset(source, target, normalX, normalY, offset) {
    const control = {
      x: (source.x + target.x) / 2 + normalX * offset,
      y: (source.y + target.y) / 2 + normalY * offset,
    };
    const startVector = { x: control.x - source.x, y: control.y - source.y };
    const endVector = { x: target.x - control.x, y: target.y - control.y };
    const startLength = Math.max(Math.hypot(startVector.x, startVector.y), 0.001);
    const endLength = Math.max(Math.hypot(endVector.x, endVector.y), 0.001);
    return {
      start: {
        x: source.x + NODE_RADIUS * startVector.x / startLength,
        y: source.y + NODE_RADIUS * startVector.y / startLength,
      },
      control,
      end: {
        x: target.x - NODE_RADIUS * endVector.x / endLength,
        y: target.y - NODE_RADIUS * endVector.y / endLength,
      },
    };
  }

  function straightGeometryWithLane(source, target, lane) {
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 0.001);
    const unitX = dx / distance;
    const unitY = dy / distance;
    const normalX = -unitY;
    const normalY = unitX;
    const boundedLane = Math.max(-NODE_RADIUS + 2, Math.min(NODE_RADIUS - 2, lane));
    const radialDistance = Math.sqrt(Math.max(NODE_RADIUS * NODE_RADIUS - boundedLane * boundedLane, 1));
    const start = {
      x: source.x + unitX * radialDistance + normalX * boundedLane,
      y: source.y + unitY * radialDistance + normalY * boundedLane,
    };
    const end = {
      x: target.x - unitX * radialDistance + normalX * boundedLane,
      y: target.y - unitY * radialDistance + normalY * boundedLane,
    };
    return {
      start,
      control: { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 },
      end,
      labelSide: 1,
    };
  }

  function edgeClearance(geometry, edge) {
    let clearance = Number.POSITIVE_INFINITY;
    for (const [nodeId, node] of nodes) {
      if (nodeId === edge.source || nodeId === edge.target) continue;
      const center = screenPosition(node);
      for (let step = 1; step < 24; step += 1) {
        const point = quadraticPoint(geometry.start, geometry.control, geometry.end, step / 24);
        clearance = Math.min(clearance, Math.hypot(point.x - center.x, point.y - center.y));
      }
    }
    return clearance;
  }

  function reciprocalPairKey(edge) {
    return [edge.source, edge.target].sort().join("\u0000");
  }

  function chooseReciprocalPlan(edge) {
    const source = screenPosition(nodes.get(edge.source));
    const target = screenPosition(nodes.get(edge.target));
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 0.001);
    const normalX = -dy / distance;
    const normalY = dx / distance;
    const straightGeometry = straightGeometryWithLane(source, target, 12);
    const reverseGeometry = straightGeometryWithLane(target, source, 12);
    const reverseEdge = { ...edge, source: edge.target, target: edge.source };
    if (
      edgeClearance(straightGeometry, edge) >= NODE_RADIUS + 16
      && edgeClearance(reverseGeometry, reverseEdge) >= NODE_RADIUS + 16
    ) {
      return { curved: false, side: 1 };
    }
    const directionFactor = edge.source.localeCompare(edge.target) <= 0 ? 1 : -1;
    let bestSide = 1;
    let bestClearance = -1;
    for (const side of [1, -1]) {
      const geometry = geometryWithOffset(
        source,
        target,
        normalX,
        normalY,
        side * directionFactor * 54,
      );
      const clearance = edgeClearance(geometry, edge);
      if (clearance > bestClearance) {
        bestSide = side;
        bestClearance = clearance;
      }
    }
    return { curved: true, side: bestSide };
  }

  function edgeGeometry(edge, reciprocalPlan = { curved: false, side: 1 }) {
    const source = screenPosition(nodes.get(edge.source));
    const target = screenPosition(nodes.get(edge.target));
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 0.001);
    const normalX = -dy / distance;
    const normalY = dx / distance;
    const directionFactor = edge.source.localeCompare(edge.target) <= 0 ? 1 : -1;
    if (edge.reciprocal && !reciprocalPlan.curved) {
      return straightGeometryWithLane(source, target, 12);
    }
    const reciprocalLane = directionFactor > 0 ? 38 : 68;
    const baseOffset = edge.reciprocal
      ? reciprocalPlan.side * directionFactor * reciprocalLane
      : 0;
    const offsets = [baseOffset];
    for (let extra = 60; extra <= 300; extra += 60) {
      if (edge.reciprocal) {
        offsets.push(reciprocalPlan.side * directionFactor * (reciprocalLane + extra));
      } else {
        offsets.push(extra, -extra);
      }
    }
    let bestGeometry = null;
    let bestClearance = -1;
    for (const offset of offsets) {
      const geometry = geometryWithOffset(source, target, normalX, normalY, offset);
      const clearance = edgeClearance(geometry, edge);
      if (clearance > bestClearance) {
        bestGeometry = geometry;
        bestClearance = clearance;
      }
      if (clearance >= NODE_RADIUS + 16) {
        geometry.labelSide = edge.reciprocal ? -reciprocalPlan.side : 0;
        return geometry;
      }
    }
    bestGeometry.labelSide = edge.reciprocal ? -reciprocalPlan.side : 0;
    return bestGeometry;
  }

  function distanceToGeometry(point, geometry) {
    let distance = Number.POSITIVE_INFINITY;
    for (let step = 0; step <= 32; step += 1) {
      const curvePoint = quadraticPoint(geometry.start, geometry.control, geometry.end, step / 32);
      distance = Math.min(distance, Math.hypot(point.x - curvePoint.x, point.y - curvePoint.y));
    }
    return distance;
  }

  function edgeLabelPosition(geometry, occupiedLabels, allGeometries) {
    const candidates = [];
    const offsets = geometry.labelSide
      ? [20, 28, 36, 48, 60].map((offset) => offset * geometry.labelSide)
      : [20, -20, 28, -28, 36, -36, 48, -48, 60, -60];
    const positionsAlongEdge = [0.5, 0.45, 0.55, 0.4, 0.6, 0.35, 0.65, 0.3, 0.7, 0.25, 0.75];
    for (const offset of offsets) {
      for (const t of positionsAlongEdge) {
        const point = quadraticPoint(geometry.start, geometry.control, geometry.end, t);
        const tangentX = 2 * (1 - t) * (geometry.control.x - geometry.start.x)
          + 2 * t * (geometry.end.x - geometry.control.x);
        const tangentY = 2 * (1 - t) * (geometry.control.y - geometry.start.y)
          + 2 * t * (geometry.end.y - geometry.control.y);
        const tangentLength = Math.max(Math.hypot(tangentX, tangentY), 0.001);
        candidates.push({
          x: point.x - tangentY / tangentLength * offset,
          y: point.y + tangentX / tangentLength * offset,
        });
      }
    }
    let best = candidates[0];
    let bestClearance = -1;
    for (const candidate of candidates) {
      const nodeClearance = Math.min(
        ...Array.from(nodes.values(), (node) => {
          const center = screenPosition(node);
          return Math.hypot(candidate.x - center.x, candidate.y - center.y) - NODE_RADIUS;
        }),
      );
      const labelClearance = occupiedLabels.length
        ? Math.min(...occupiedLabels.map((label) => Math.hypot(candidate.x - label.x, candidate.y - label.y)))
        : Number.POSITIVE_INFINITY;
      const edgeClearance = Math.min(
        ...allGeometries
          .filter((otherGeometry) => otherGeometry !== geometry)
          .map((otherGeometry) => distanceToGeometry(candidate, otherGeometry)),
        Number.POSITIVE_INFINITY,
      );
      const clearance = Math.min(nodeClearance, labelClearance - 64, edgeClearance - 30);
      if (clearance > bestClearance) {
        best = candidate;
        bestClearance = clearance;
      }
      if (nodeClearance >= 24 && labelClearance >= 64 && edgeClearance >= 30) return candidate;
    }
    return best;
  }

  function drawEdges() {
    clearLayer(edgesLayer);
    const occupiedLabels = [];
    const reciprocalPlans = new Map();
    const edgeLayouts = edges.map((edge, index) => {
      if (!nodes.has(edge.source) || !nodes.has(edge.target)) return null;
      let reciprocalPlan = { curved: false, side: 1 };
      if (edge.reciprocal) {
        const pairKey = reciprocalPairKey(edge);
        if (!reciprocalPlans.has(pairKey)) {
          reciprocalPlans.set(pairKey, chooseReciprocalPlan(edge));
        }
        reciprocalPlan = reciprocalPlans.get(pairKey);
      }
      return { edge, index, geometry: edgeGeometry(edge, reciprocalPlan) };
    }).filter(Boolean);
    const allGeometries = edgeLayouts.map((layout) => layout.geometry);
    edgeLayouts.forEach(({ edge, index, geometry }) => {
      const color = edge.zero ? "#7A7F87" : edge.color;
      const width = edge.bold ? 5 : 2;
      const group = svgElement("g", {
        class: "graph-edge",
        role: "button",
        tabindex: "0",
        "aria-label": `Связь ${edge.source} → ${edge.target}`,
        "data-source": edge.source,
        "data-target": edge.target,
      });
      const hitPath = svgElement("path", {
        d: `M ${geometry.start.x},${geometry.start.y} Q ${geometry.control.x},${geometry.control.y} ${geometry.end.x},${geometry.end.y}`,
        fill: "none",
        stroke: "transparent",
        "stroke-width": Math.max(16, width),
        class: "graph-edge-hit",
      });
      const path = svgElement("path", {
        d: `M ${geometry.start.x},${geometry.start.y} Q ${geometry.control.x},${geometry.control.y} ${geometry.end.x},${geometry.end.y}`,
        fill: "none",
        stroke: color,
        "stroke-width": width,
      });
      group.appendChild(hitPath);
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

      const labelPosition = edgeLabelPosition(geometry, occupiedLabels, allGeometries);
      occupiedLabels.push(labelPosition);
      const label = svgElement("text", {
        x: labelPosition.x,
        y: labelPosition.y,
        fill: color,
        class: "graph-edge-label",
      });
      label.textContent = `${Number(edge.weight) >= 0 ? "+" : ""}${Number(edge.weight).toFixed(2)}`;
      group.appendChild(label);
      const selectEdge = (event) => {
        event.stopPropagation();
        setStateValue("selected_edge", { source: edge.source, target: edge.target });
      };
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") selectEdge(event);
      });
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
        const start = localPosition(svg, event);
        activeDrag = {
          id: node.id,
          pointerId: event.pointerId,
          group,
          startX: start.x,
          startY: start.y,
          moved: false,
        };
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
      if (Math.hypot(point.x - activePan.startX, point.y - activePan.startY) > 4) {
        activePan.moved = true;
      }
      panX = activePan.initialPanX + point.x - activePan.startX;
      panY = activePan.initialPanY + point.y - activePan.startY;
      applyView();
      return;
    }
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    event.preventDefault();
    const pointer = localPosition(svg, event);
    if (Math.hypot(pointer.x - activeDrag.startX, pointer.y - activeDrag.startY) > 4) {
      activeDrag.moved = true;
    }
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
      if (!activePan.moved && activePan.edgeSource && activePan.edgeTarget) {
        setStateValue("selected_edge", {
          source: activePan.edgeSource,
          target: activePan.edgeTarget,
        });
      }
      activePan = null;
      return;
    }
    if (!activeDrag || event.pointerId !== activeDrag.pointerId) return;
    activeDrag.group.style.cursor = "grab";
    const selectedNodeId = activeDrag.id;
    const nodeMoved = activeDrag.moved;
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    activeDrag = null;
    if (nodeMoved) {
      persistPositions();
    } else {
      setStateValue("selected_node", selectedNodeId);
    }
  }

  applyView();
  applyGridVisibility();
  updateFullscreenButton();
  drawEdges();
  drawNodes();
  svg.addEventListener("pointerdown", startPan);
  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", finishDrag);
  svg.addEventListener("pointercancel", finishDrag);
  zoomOutButton.onclick = () => setZoom(zoom / 1.25);
  zoomInButton.onclick = () => setZoom(zoom * 1.25);
  resetViewButton.onclick = fitGraphToView;
  toggleGridButton.onclick = () => {
    gridVisible = !gridVisible;
    applyGridVisibility();
  };
  toggleFullscreenButton.onclick = toggleFullscreen;
  document.addEventListener("fullscreenchange", updateFullscreenButton);

  return () => {
    svg.removeEventListener("pointerdown", startPan);
    svg.removeEventListener("wheel", onWheel);
    svg.removeEventListener("pointermove", onPointerMove);
    svg.removeEventListener("pointerup", finishDrag);
    svg.removeEventListener("pointercancel", finishDrag);
    zoomOutButton.onclick = null;
    zoomInButton.onclick = null;
    resetViewButton.onclick = null;
    toggleGridButton.onclick = null;
    toggleFullscreenButton.onclick = null;
    document.removeEventListener("fullscreenchange", updateFullscreenButton);
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
        x = max(MIN_NODE_COORDINATE, min(MAX_NODE_COORDINATE, x))
        y = max(MIN_NODE_COORDINATE, min(MAX_NODE_COORDINATE, y))
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
    on_selected_node_change: Callable[[], None],
    on_selected_edge_change: Callable[[], None],
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
        default={"positions": positions, "selected_node": None, "selected_edge": None},
        height=650,
        on_positions_change=on_positions_change,
        on_selected_node_change=on_selected_node_change,
        on_selected_edge_change=on_selected_edge_change,
    )
