const NS = "http://www.w3.org/2000/svg";
const W = 210, H = 80, DX = 270, DY = 108;
function svg(tag, attrs = {}, text) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

// Kahn layers give imports a consistent left-to-right direction. Cycles share a layer.
export function layout(nodes, edges, collapsed = new Set(), saved = new Map()) {
  const degree = new Map(nodes.map(n => [n.id, 0]));
  const next = new Map(nodes.map(n => [n.id, []]));
  const levels = new Map(nodes.map(n => [n.id, 0]));
  for (const e of edges) { degree.set(e.target, degree.get(e.target) + 1); next.get(e.source).push(e.target); }
  const queue = nodes.filter(n => !degree.get(n.id)).map(n => n.id).sort();
  const visited = new Set();
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i]; visited.add(id);
    for (const target of next.get(id)) {
      levels.set(target, Math.max(levels.get(target), levels.get(id) + 1));
      degree.set(target, degree.get(target) - 1);
      if (!degree.get(target)) queue.push(target);
    }
  }
  const finalLevel = Math.max(0, ...[...visited].map(id => levels.get(id))) + (visited.size ? 1 : 0);
  for (const n of nodes) if (!visited.has(n.id)) levels.set(n.id, finalLevel);
  const maxLevel = Math.max(0, ...levels.values());
  const groups = [], positions = new Map();
  let y = 24;
  for (const name of [...new Set(nodes.map(n => n.group))].sort()) {
    const members = nodes.filter(n => n.group === name).sort((a, b) => a.path.localeCompare(b.path));
    const rows = new Map();
    const isCollapsed = collapsed.has(name);
    for (const node of members) {
      const level = levels.get(node.id), row = rows.get(level) || 0;
      rows.set(level, row + 1);
      if (!isCollapsed) positions.set(node.id, saved.get(node.id + ":" + name) || { x: 44 + DX * level, y: y + 43 + row * DY });
    }
    const height = isCollapsed ? 44 : 48 + Math.max(1, ...rows.values()) * DY;
    groups.push({ name, x: 24, y, width: maxLevel * DX + W + 40, height, count: members.length, collapsed: isCollapsed });
    y += height + 22;
  }
  // Manual dragging may extend a group's boundary; the viewport still fits every node.
  for (const group of groups) {
    if (group.collapsed) continue;
    const members = nodes.filter(n => n.group === group.name).map(n => positions.get(n.id));
    const minX = Math.min(group.x, ...members.map(p => p.x - 16));
    const minY = Math.min(group.y, ...members.map(p => p.y - 40));
    const right = Math.max(group.x + group.width, ...members.map(p => p.x + W + 16));
    const bottom = Math.max(group.y + group.height, ...members.map(p => p.y + H + 16));
    Object.assign(group, { x: minX, y: minY, width: right - minX, height: bottom - minY });
  }
  const width = Math.max(300, ...groups.map(g => g.x + g.width + 24));
  const height = Math.max(160, ...groups.map(g => g.y + g.height + 24));
  return { positions, groups, width, height };
}

export class GraphView {
  constructor(element, onSelect) {
    this.element = element; this.onSelect = onSelect;
    this.saved = new Map(); this.collapsed = new Set(); this.cache = new Map();
    this.camera = { x: 0, y: 0, scale: 1 }; this.selected = null;
    element.addEventListener("pointerdown", e => this.pointerDown(e));
    element.addEventListener("pointermove", e => this.pointerMove(e));
    const finish = e => {
      if (!this.gesture || e.pointerId !== this.gesture.pointerId) return;
      const gesture = this.gesture; this.gesture = null;
      if (element.hasPointerCapture(e.pointerId)) element.releasePointerCapture(e.pointerId);
      if (!gesture.moved && gesture.id) this.onSelect(gesture.id);
    };
    element.addEventListener("pointerup", finish);
    element.addEventListener("pointercancel", () => { this.gesture = null; });
    element.addEventListener("wheel", e => { e.preventDefault(); this.zoom(Math.exp(-e.deltaY * .0015), e); }, { passive: false });
    new ResizeObserver(() => { if (!this.fitted && this.graph) this.fit(); }).observe(element);
  }
  setGraph(graph, repositoryKey) {
    if (this.repositoryKey !== repositoryKey) {
      if (this.repositoryKey) this.cache.set(this.repositoryKey, { saved: this.saved, collapsed: this.collapsed });
      const previous = this.cache.get(repositoryKey);
      this.saved = previous?.saved || new Map(); this.collapsed = previous?.collapsed || new Set();
      this.repositoryKey = repositoryKey; this.selected = null;
    }
    this.graph = graph; this.draw(); this.fit();
  }
  draw() {
    const root = this.element; root.replaceChildren();
    if (!this.graph) return;
    this.arrangement = layout(this.graph.nodes, this.graph.edges, this.collapsed, this.saved);
    const defs = svg("defs"), marker = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" });
    marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#a9bfc8" })); defs.append(marker); root.append(defs);
    this.viewport = svg("g"); root.append(this.viewport);
    this.groupElements = new Map(); this.nodeElements = new Map(); this.edgeElements = [];
    for (const group of this.arrangement.groups) {
      const box = svg("rect", { x: group.x, y: group.y, width: group.width, height: group.height, rx: 10, class: "group-box" });
      const toggle = svg("g", { class: "group-toggle", tabindex: 0, role: "button", "aria-expanded": !group.collapsed, "aria-label": `折叠或展开 ${group.name}` });
      toggle.append(svg("text", { x: group.x + 15, y: group.y + 24, class: "group-label" }, `${group.collapsed ? "▸" : "▾"}  ${group.name.slice(0, 50)}`));
      toggle.append(svg("text", { x: group.x + group.width - 28, y: group.y + 24, class: "group-count" }, group.count));
      const action = () => { this.collapsed.has(group.name) ? this.collapsed.delete(group.name) : this.collapsed.add(group.name); this.draw(); this.fit(); };
      toggle.addEventListener("click", action);
      toggle.addEventListener("keydown", e => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); action(); } });
      this.viewport.append(box, toggle); this.groupElements.set(group.name, { box, toggle });
    }
    for (const edge of this.graph.edges) {
      if (!this.arrangement.positions.has(edge.source) || !this.arrangement.positions.has(edge.target)) continue;
      const path = svg("path", { class: "edge", "marker-end": "url(#arrow)", opacity: Math.max(.25, edge.confidence) });
      path.append(svg("title", {}, `import · ${Math.round(edge.confidence * 100)}%`));
      this.edgeElements.push({ edge, path }); this.viewport.append(path);
    }
    for (const node of this.graph.nodes) {
      if (!this.arrangement.positions.has(node.id)) continue;
      const element = svg("g", { class: "graph-node", "data-id": node.id, tabindex: 0, role: "button", "aria-label": `打开 ${node.path}` });
      element.append(svg("rect", { width: W, height: H, rx: 7 }));
      element.append(svg("text", { x: 13, y: 23, class: "node-name" }, `${node.entry ? "↗" : "◇"}  ${node.name.slice(0, 24)}`));
      element.append(svg("text", { x: 13, y: 42, class: "node-description" }, node.summary.slice(0, 23) + (node.summary.length > 23 ? "…" : "")));
      element.append(svg("line", { x1: 0, y1: 53, x2: W, y2: 53, class: "node-divider" }));
      element.append(svg("text", { x: 13, y: 70, class: "node-type" }, node.entry ? "ENTRY POINT" : (node.kind || "MODULE").toUpperCase()));
      element.append(svg("title", {}, `${node.path}\n${node.summary}\nL${node.start_line}–${node.end_line}`));
      element.addEventListener("keydown", e => { if (["Enter", " "].includes(e.key)) { e.preventDefault(); this.onSelect(node.id); } });
      // A click triggered by assistive technology has no pointer sequence.
      element.addEventListener("click", e => { if (e.detail === 0) this.onSelect(node.id); });
      this.nodeElements.set(node.id, element); this.viewport.append(element);
    }
    this.updatePositions(); this.select(this.selected); this.applyCamera();
  }
  updatePositions() {
    for (const [id, element] of this.nodeElements) { const p = this.arrangement.positions.get(id); element.setAttribute("transform", `translate(${p.x},${p.y})`); }
    for (const { edge, path } of this.edgeElements) {
      const a = this.arrangement.positions.get(edge.source), b = this.arrangement.positions.get(edge.target);
      const x = a.x + W, y = a.y + H / 2, tx = b.x, ty = b.y + H / 2, bend = Math.max(45, Math.abs(tx - x) / 2);
      path.setAttribute("d", `M ${x} ${y} C ${x + bend} ${y}, ${tx - bend} ${ty}, ${tx} ${ty}`);
    }
    // Recompute group bounds after dragging, without replacing interactive elements.
    const updated = layout(this.graph.nodes, this.graph.edges, this.collapsed, this.saved);
    for (const group of updated.groups) {
      const elements = this.groupElements.get(group.name);
      for (const k of ["x", "y", "width", "height"]) elements.box.setAttribute(k, group[k]);
      const labels = elements.toggle.querySelectorAll("text");
      labels[0].setAttribute("x", group.x + 15); labels[0].setAttribute("y", group.y + 24);
      labels[1].setAttribute("x", group.x + group.width - 28); labels[1].setAttribute("y", group.y + 24);
    }
    this.arrangement = updated;
  }
  select(id) {
    this.selected = id;
    const neighbors = new Set([id]);
    for (const edge of this.graph?.edges || []) { if (edge.source === id) neighbors.add(edge.target); if (edge.target === id) neighbors.add(edge.source); }
    const active = this.nodeElements?.has(id);
    for (const [nodeId, element] of this.nodeElements || []) {
      element.classList.toggle("selected", nodeId === id); element.classList.toggle("dimmed", Boolean(active && !neighbors.has(nodeId)));
    }
    for (const { edge, path } of this.edgeElements || []) {
      const related = edge.source === id || edge.target === id;
      path.classList.toggle("active", related); path.classList.toggle("dimmed", Boolean(active && !related));
    }
  }
  applyCamera() { const c = this.camera; this.viewport?.setAttribute("transform", `translate(${c.x},${c.y}) scale(${c.scale})`); }
  fit() {
    if (!this.arrangement || !this.element.clientWidth || !this.element.clientHeight) return;
    const { width, height } = this.arrangement;
    const scale = Math.min(1.25, Math.max(.12, Math.min((this.element.clientWidth - 30) / width, (this.element.clientHeight - 42) / height)));
    this.camera = { scale, x: (this.element.clientWidth - width * scale) / 2, y: (this.element.clientHeight - height * scale) / 2 - 4 };
    this.fitted = true; this.applyCamera();
  }
  zoom(factor, event) {
    const box = this.element.getBoundingClientRect();
    const x = event ? event.clientX - box.left : box.width / 2, y = event ? event.clientY - box.top : box.height / 2;
    const c = this.camera, scale = Math.min(3, Math.max(.12, c.scale * factor)), ratio = scale / c.scale;
    this.camera = { scale, x: x - (x - c.x) * ratio, y: y - (y - c.y) * ratio }; this.applyCamera();
  }
  reset() { this.saved.clear(); this.draw(); this.fit(); }
  pointerDown(e) {
    if (e.button !== 0 || e.target.closest(".group-toggle") || !this.graph) return;
    const node = e.target.closest(".graph-node"), id = node?.dataset.id;
    this.gesture = { id, pointerId: e.pointerId, x: e.clientX, y: e.clientY, camera: { ...this.camera }, position: id ? { ...this.arrangement.positions.get(id) } : null, moved: false };
    this.element.setPointerCapture(e.pointerId);
  }
  pointerMove(e) {
    const g = this.gesture;
    if (!g || e.pointerId !== g.pointerId) return;
    const dx = e.clientX - g.x, dy = e.clientY - g.y;
    if (!g.moved && Math.hypot(dx, dy) < 4) return;
    g.moved = true;
    if (g.id) {
      const node = this.graph.nodes.find(n => n.id === g.id);
      const position = { x: Math.max(36, g.position.x + dx / this.camera.scale), y: Math.max(64, g.position.y + dy / this.camera.scale) };
      this.saved.set(g.id + ":" + node.group, position); this.arrangement.positions.set(g.id, position); this.updatePositions();
    } else { this.camera.x = g.camera.x + dx; this.camera.y = g.camera.y + dy; this.applyCamera(); }
  }
}
