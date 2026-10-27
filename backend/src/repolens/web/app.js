"use strict";

const $ = (id) => document.getElementById(id);
let graph = null;
let repository = null;
let selected = null;
let zoom = 1;
let dimensions = { width: 600, height: 400 };
let busy = false;
let sourceRequest = 0;
const SVG_NS = "http://www.w3.org/2000/svg";

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败，请重试。");
  return value;
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = !message;
}

function setBusy(value) {
  busy = value;
  for (const id of ["import", "example", "analyze", "save-key", "delete-key"]) $(id).disabled = value;
  $("analyze").textContent = value ? "正在处理……" : "用 DeepSeek 理解项目 ↗";
}

async function task(action) {
  notice("");
  setBusy(true);
  try { await action(); } catch (error) { notice(error.message, true); }
  finally { setBusy(false); $("progress").textContent = "就绪"; }
}

function keyState(configured, saved) {
  $("key-state").textContent = configured ? (saved ? "已安全保存" : "本次已设置") : "未配置";
}

function setRepository(value) {
  repository = value;
  selected = null;
  sourceRequest++;
  $("consent").checked = false;
  $("source").replaceChildren();
  $("source-title").textContent = "源码核对";
  $("source-anchor").textContent = "点击文件或图节点，打开只读源码";
  $("node-summary").textContent = "文件与行号来自当前本地索引。";
  $("file-list").replaceChildren();
  $("file-count").textContent = value ? value.files.length : 0;
  if (!value) return;
  $("project-name").textContent = value.name;
  $("repo-path").value = value.path;
  for (const file of value.files) {
    const button = document.createElement("button");
    button.className = "file-item";
    button.textContent = "◇  " + file.path;
    button.title = file.path;
    button.dataset.id = file.id;
    button.addEventListener("click", () => openSource(file.id));
    $("file-list").append(button);
  }
}

function svg(name, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== null) element.textContent = text;
  return element;
}

// Stable Kahn layering. Cyclic leftovers share a final layer; no model coordinates.
function layout(nodes, edges) {
  const degrees = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const levels = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    degrees.set(edge.target, degrees.get(edge.target) + 1);
    outgoing.get(edge.source).push(edge.target);
  }
  const queue = nodes.filter((node) => degrees.get(node.id) === 0).map((node) => node.id).sort();
  const processed = new Set();
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    processed.add(id);
    for (const target of outgoing.get(id)) {
      levels.set(target, Math.max(levels.get(target), levels.get(id) + 1));
      degrees.set(target, degrees.get(target) - 1);
      if (degrees.get(target) === 0) queue.push(target);
    }
  }
  const last = Math.max(0, ...[...processed].map((id) => levels.get(id))) + (processed.size ? 1 : 0);
  for (const node of nodes) if (!processed.has(node.id)) levels.set(node.id, last);
  const columns = new Map();
  for (const node of nodes) {
    const level = levels.get(node.id);
    if (!columns.has(level)) columns.set(level, []);
    columns.get(level).push(node);
  }
  const positions = new Map();
  let rows = 1;
  for (const [level, items] of columns) {
    items.sort((left, right) => left.group.localeCompare(right.group) || left.path.localeCompare(right.path));
    rows = Math.max(rows, items.length);
    items.forEach((node, index) => positions.set(node.id, { x: 35 + level * 265, y: 48 + index * 112 }));
  }
  return { positions, width: Math.max(340, columns.size * 265 + 55), height: Math.max(340, rows * 112 + 75) };
}

function resizeGraph() {
  $("graph").setAttribute("width", dimensions.width * zoom);
  $("graph").setAttribute("height", dimensions.height * zoom);
}

function fit() {
  zoom = Math.min(1.1, Math.max(.35, ($("graph-scroll").clientWidth - 15) / dimensions.width));
  resizeGraph();
}

function renderGraph(value) {
  graph = value;
  $("graph").replaceChildren();
  $("graph-empty").hidden = Boolean(value);
  if (!value) return;
  const { positions, width, height } = layout(value.nodes, value.edges);
  dimensions = { width, height };
  $("graph").setAttribute("viewBox", `0 0 ${width} ${height}`);
  const defs = svg("defs");
  const marker = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
  marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#acbec3" }));
  defs.append(marker); $("graph").append(defs);
  for (const edge of value.edges) {
    const from = positions.get(edge.source), to = positions.get(edge.target);
    const x1 = from.x + 205, y1 = from.y + 39, x2 = to.x, y2 = to.y + 39;
    const bend = Math.max(35, Math.abs(x2 - x1) / 2);
    const path = svg("path", { d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`, class: "edge", "marker-end": "url(#arrow)", opacity: Math.max(.25, edge.confidence) });
    path.append(svg("title", {}, `import · 置信度 ${Math.round(edge.confidence * 100)}%`));
    $("graph").append(path);
  }
  for (const node of value.nodes) {
    const position = positions.get(node.id);
    const group = svg("g", { transform: `translate(${position.x},${position.y})`, class: "graph-node", "data-id": node.id, role: "button", tabindex: "0", "aria-label": `查看 ${node.path} 源码` });
    group.append(svg("rect", { width: 205, height: 78, rx: 8 }));
    group.append(svg("text", { x: 13, y: 20, class: "node-group" }, node.group.slice(0, 24)));
    group.append(svg("text", { x: 13, y: 40, class: "node-name" }, (node.entry ? "↗ " : "◇ ") + node.name.slice(0, 22)));
    group.append(svg("text", { x: 13, y: 61, class: "node-description" }, node.summary.slice(0, 18) + (node.summary.length > 18 ? "…" : "")));
    group.append(svg("title", {}, `${node.path}\n${node.summary}\nL${node.start_line}–${node.end_line}`));
    group.addEventListener("click", () => openSource(node.id));
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openSource(node.id); } });
    $("graph").append(group);
  }
  $("graph-stats").textContent = `${value.nodes.length} 个文件 · ${value.edges.length} 条依赖${value.incomplete ? " · 范围未完整覆盖" : ""}`;
  $("mode").textContent = value.mode === "deepseek" ? "DeepSeek 分析" : "本地依赖预览 · 未调用模型";
  $("project-type").textContent = value.project_type;
  $("project-summary").textContent = value.summary;
  $("usage").textContent = value.usage ? `${value.usage.requests} 次请求 · ${value.usage.estimated ? "保守估算 " : ""}${value.usage.total_tokens.toLocaleString()} token` : "";
  if (value.warnings.length) notice(value.warnings.join("；"));
  fit();
}

async function openSource(id) {
  const serial = ++sourceRequest;
  try {
    const source = await api("/api/source?id=" + encodeURIComponent(id));
    if (serial !== sourceRequest) return;
    selected = id;
    const node = graph?.nodes.find((item) => item.id === id);
    $("source-title").textContent = source.path;
    $("source-anchor").textContent = `L${source.start_line}–${source.end_line} · 只读 · 当前索引已核对`;
    $("node-summary").textContent = node?.summary || "当前文件未包含在模型生成的图中。";
    const fragment = document.createDocumentFragment();
    const lines = source.content.split(/\r\n|\n|\r/);
    if (lines.length > 1 && lines.at(-1) === "") lines.pop();
    lines.forEach((line, index) => {
      const row = document.createElement("div"); row.className = "code-line";
      const number = document.createElement("span"); number.className = "line-number"; number.textContent = index + 1;
      const text = document.createElement("span"); text.className = "code-text"; text.textContent = line || " ";
      row.append(number, text); fragment.append(row);
    });
    $("source").replaceChildren(fragment);
    $("source").scrollTop = 0;
    for (const item of document.querySelectorAll(".graph-node,.file-item")) item.classList.toggle("selected", item.dataset.id === selected);
  } catch (error) { if (serial === sourceRequest) notice(error.message, true); }
}

$("import").addEventListener("click", () => task(async () => {
  const result = await api("/api/repository", { path: $("repo-path").value });
  setRepository(result.repository); renderGraph(result.graph);
}));
$("example").addEventListener("click", () => task(async () => {
  const result = await api("/api/example", {});
  setRepository(result.repository); renderGraph(result.graph);
}));
$("save-key").addEventListener("click", () => task(async () => {
  const result = await api("/api/key", { api_key: $("api-key").value, remember: $("remember").checked });
  $("api-key").value = "";
  keyState(result.key_configured, result.key_saved);
  notice(result.key_saved ? "Key 已保存到 Windows 凭据管理器。" : "Key 仅在本次服务内存中使用，关闭后清除。");
}));
$("delete-key").addEventListener("click", () => task(async () => {
  await api("/api/key/delete", {}); $("api-key").value = ""; keyState(false, false); notice("本次 Key 与已保存凭据均已清除。");
}));
$("analyze").addEventListener("click", () => task(async () => {
  if (!repository) throw new Error("请先导入 Python 仓库。");
  if (!$("consent").checked) throw new Error("请先勾选同意发送当前索引摘要。");
  const timer = setInterval(async () => {
    try { const state = await api("/api/state"); $("progress").textContent = state.phase; } catch { /* main request reports errors */ }
  }, 1000);
  try {
    $("progress").textContent = "正在请求 DeepSeek……";
    const result = await api("/api/analyze", { goal: $("goal").value, model: $("model").value, confirmed: true });
    renderGraph(result.graph);
    if (selected) await openSource(selected);
  } finally { clearInterval(timer); }
}));
$("zoom-in").addEventListener("click", () => { zoom = Math.min(2, zoom * 1.2); resizeGraph(); });
$("zoom-out").addEventListener("click", () => { zoom = Math.max(.25, zoom / 1.2); resizeGraph(); });
$("zoom-fit").addEventListener("click", fit);

api("/api/state").then((state) => {
  setRepository(state.repository); renderGraph(state.graph);
  keyState(state.key_configured, state.key_saved); $("model").value = state.default_model;
  if (state.key_error) notice(state.key_error, true);
  if (state.busy) notice("其他页面正在运行任务，请稍候再操作。");
}).catch((error) => notice(error.message, true));
