const NS = "http://www.w3.org/2000/svg";
const W = 210, H = 80, DX = 270, DY = 108;
function svg(tag, attrs = {}, text) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

// Condense strongly connected components before layering; cycles do not push
// unrelated downstream modules into one final column.
export function layers(ids, edges) {
  const adjacency = new Map(ids.map(id => [id, []]));
  for (const e of edges) if (adjacency.has(e.source) && adjacency.has(e.target)) adjacency.get(e.source).push(e.target);
  let serial = 0; const indices = new Map(), low = new Map(), stack = [], active = new Set(), components = [];
  function visit(id) {
    indices.set(id, serial); low.set(id, serial++); stack.push(id); active.add(id);
    for (const to of adjacency.get(id)) {
      if (!indices.has(to)) { visit(to); low.set(id, Math.min(low.get(id), low.get(to))); }
      else if (active.has(to)) low.set(id, Math.min(low.get(id), indices.get(to)));
    }
    if (low.get(id) === indices.get(id)) {
      const group = []; let item;
      do { item = stack.pop(); active.delete(item); group.push(item); } while (item !== id);
      components.push(group.sort());
    }
  }
  for (const id of [...ids].sort()) if (!indices.has(id)) visit(id);
  const component = new Map(); components.forEach((members, i) => members.forEach(id => component.set(id, i)));
  const outgoing = components.map(() => new Set()), degrees = components.map(() => 0), levels = components.map(() => 0);
  for (const e of edges) {
    const a = component.get(e.source), b = component.get(e.target);
    if (a !== undefined && b !== undefined && a !== b && !outgoing[a].has(b)) { outgoing[a].add(b); degrees[b]++; }
  }
  const queue = degrees.flatMap((degree, i) => degree === 0 ? [i] : []);
  for (let i = 0; i < queue.length; i++) for (const to of outgoing[queue[i]]) {
    levels[to] = Math.max(levels[to], levels[queue[i]] + 1); if (--degrees[to] === 0) queue.push(to);
  }
  return new Map(ids.map(id => [id, levels[component.get(id)]]));
}

export function layout(nodes, edges, collapsed = new Set(), saved = new Map()) {
  const names = [...new Set(nodes.map(n => n.group))].sort();
  const byId = new Map(nodes.map(n => [n.id, n])), groupLevels = layers(names, edges.map(e => ({ source: byId.get(e.source)?.group, target: byId.get(e.target)?.group })));
  const sizes = new Map(), innerPositions = new Map();
  for (const name of names) {
    const members = nodes.filter(n => n.group === name).sort((a,b) => a.id.localeCompare(b.id));
    if (collapsed.has(name)) { sizes.set(name, { width: W + 40, height: 48, count: members.length }); continue; }
    const ids = new Set(members.map(n => n.id)), levels = layers([...ids], edges.filter(e => ids.has(e.source) && ids.has(e.target))), rows = new Map();
    for (const n of members) {
      const level = levels.get(n.id), row = rows.get(level) || 0; rows.set(level, row + 1);
      innerPositions.set(n.id, { x: 20 + level * DX, y: 44 + row * DY });
    }
    sizes.set(name, { width: 40 + W + Math.max(0,...levels.values()) * DX, height: 48 + Math.max(1,...rows.values()) * DY, count: members.length });
  }
  const columns = new Map();
  for (const name of names) { const level = groupLevels.get(name); if (!columns.has(level)) columns.set(level, []); columns.get(level).push(name); }
  const positions = new Map(), groups = []; let x = 24;
  for (const [, column] of [...columns].sort(([a],[b]) => a-b)) {
    let y = 24;
    for (const name of column) {
      const size = sizes.get(name), isCollapsed = collapsed.has(name);
      const group = { name, x, y, ...size, collapsed: isCollapsed };
      groups.push(group);
      if (isCollapsed) positions.set('@group:' + name, { x, y, width: size.width, height: size.height });
      else for (const n of nodes.filter(n => n.group === name)) {
        const p = innerPositions.get(n.id); positions.set(n.id, saved.get(n.id + ':' + name) || { x: x + p.x, y: y + p.y });
      }
      y += size.height + 44;
    }
    x += Math.max(...column.map(name => sizes.get(name).width)) + 100;
  }
  for (const group of groups) if (!group.collapsed) {
    const points = nodes.filter(n => n.group === group.name).map(n => positions.get(n.id));
    const minX = Math.min(group.x,...points.map(p => p.x-16)), minY = Math.min(group.y,...points.map(p => p.y-40));
    const right = Math.max(group.x + group.width,...points.map(p => p.x+W+16)), bottom = Math.max(group.y+group.height,...points.map(p => p.y+H+16));
    Object.assign(group, {x:minX, y:minY, width:right-minX, height:bottom-minY});
  }
  // Folded containers retain their incoming/outgoing relationships.
  const aggregated = new Map();
  for (const e of edges) {
    const from = byId.get(e.source), to = byId.get(e.target); if (!from || !to) continue;
    const source = collapsed.has(from.group) ? '@group:' + from.group : e.source, target = collapsed.has(to.group) ? '@group:' + to.group : e.target;
    if (source === target) continue;
    const key = source + '|' + target + '|' + e.type;
    if (aggregated.has(key)) aggregated.get(key).count++;
    else aggregated.set(key, { ...e, source, target, count:1 });
  }
  return { positions, groups, visibleEdges:[...aggregated.values()], width:Math.max(300,...groups.map(g => g.x+g.width+24)), height:Math.max(160,...groups.map(g => g.y+g.height+24)) };
}

const RELATIONS = {
  import: ['依赖', '#7d929e', ''], imports: ['依赖', '#7d929e', ''], calls: ['调用', '#347fbd', ''],
  serves: ['提供服务', '#258b79', ''], dispatches: ['异步分发', '#8c70b7', '7 4'], uses_cache: ['访问缓存', '#c19249', '3 3'],
  configures: ['配置', '#7d929e', '2 4'], supports: ['辅助', '#7d929e', '6 4'], contains: ['包含', '#258b79', '2 3'], external: ['外部交互', '#7d929e', '7 4']
};
const ACCENTS = { teal:'#258b79', blue:'#347fbd', purple:'#8c70b7', orange:'#c19249', gray:'#7d929e' };

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
    const oldGroupNames = new Set(this.graph?.nodes.map(n => n.group) || []);
    for (const name of new Set(graph?.nodes.filter(n => n.kind === 'external').map(n => n.group) || [])) {
      if (!oldGroupNames.has(name) && graph.nodes.filter(n => n.group === name).every(n => n.kind === 'external')) this.collapsed.add(name);
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
    for (const edge of this.arrangement.visibleEdges) {
      if (!this.arrangement.positions.has(edge.source) || !this.arrangement.positions.has(edge.target)) continue;
      const [type, color, dash] = RELATIONS[edge.type] || RELATIONS.import;
      const line = svg("g", { class: "relation" });
      const path = svg("path", { class: "edge", "marker-end": "url(#arrow)", stroke: color, "stroke-dasharray": dash, opacity: Math.max(.35, edge.confidence) });
      path.append(svg("title", {}, `${edge.label || type} · ${edge.basis === 'inferred' ? '语义推断 · ' : ''}${Math.round(edge.confidence * 100)}%`));
      const label = svg("text", { class: "edge-label", "text-anchor": "middle" }, (edge.label || type).slice(0, 20) + (edge.count > 1 ? ` ×${edge.count}` : ''));
      line.append(path, label); this.edgeElements.push({ edge, path, label }); this.viewport.append(line);
    }
    for (const node of this.graph.nodes) {
      if (!this.arrangement.positions.has(node.id)) continue;
      const element = svg("g", { class: "graph-node", "data-id": node.id, tabindex: 0, role: "button", "aria-label": `打开 ${node.path}` });
      const spec = node.render_spec || {}, shape = spec.shape || node.kind || 'module', color = ACCENTS[spec.accent] || ACCENTS.teal;
      element.classList.add('shape-' + shape);
      if (node.confidence < .6 || spec.emphasis === 'low') element.classList.add('low-confidence');
      element.append(svg("rect", { width: W, height: H, rx: shape === 'service' ? 18 : 7, class: 'node-body' }));
      element.append(svg("path", { d: `M 0 10 H 4 V ${H - 10} H 0 Z`, fill: color, class: 'node-accent' }));
      const icon = {service:'◎',worker:'↝',storage:'▤',adapter:'⇄',library:'▦',external:'↗',config:'⚙'}[shape] || '◇';
      element.append(svg("text", { x: 13, y: 23, class: "node-name" }, `${node.entry ? "↗" : icon}  ${node.name.slice(0, 22)}`));
      element.append(svg("text", { x: 13, y: 42, class: "node-description" }, node.summary.slice(0, 23) + (node.summary.length > 23 ? "…" : "")));
      element.append(svg("line", { x1: 0, y1: 53, x2: W, y2: 53, class: "node-divider" }));
      element.append(svg("text", { x: 13, y: 70, class: "node-type" }, `${spec.badge || (node.kind || 'module').toUpperCase()} · ${node.anchors?.length || 1} 处源码`));
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
    for (const { edge, path, label } of this.edgeElements) {
      const a = this.arrangement.positions.get(edge.source), b = this.arrangement.positions.get(edge.target);
      const x = a.x + (a.width || W), y = a.y + (a.height || H) / 2, tx = b.x, ty = b.y + (b.height || H) / 2, bend = Math.max(45, Math.abs(tx - x) / 2);
      path.setAttribute("d", `M ${x} ${y} C ${x + bend} ${y}, ${tx - bend} ${ty}, ${tx} ${ty}`);
      label.setAttribute('x', (x + tx) / 2); label.setAttribute('y', (y + ty) / 2 - 7);
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
    for (const { edge, path, label } of this.edgeElements || []) {
      const related = edge.source === id || edge.target === id;
      path.classList.toggle("active", related); path.classList.toggle("dimmed", Boolean(active && !related));
      label.classList.toggle('dimmed', Boolean(active && !related));
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
