import { GraphView } from './graph.mjs';
import { renderSource } from './source.mjs';

const $ = id => document.getElementById(id);
let repository = null, graph = null, selection = null, sourceRequest = 0, busy = false;
let uploadLimits = { files: 5000, fileBytes: 1000000, totalBytes: 64000000 };
let analysisTimeoutMs = 75000;
let directoryHandle = null, activeRun = null;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const expanded = new Set();
const view = new GraphView($('graph'), id => openSource(id));

async function api(path, body) {
  let response;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === '/api/analyze' || path === '/api/agent/analyze' ? analysisTimeoutMs : 20000);
  try {
    try {
      response = await fetch(path, body === undefined ? { signal: controller.signal } : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal,
      });
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('本地请求超时，请检查服务状态。');
      throw new Error('无法连接本地服务，请检查服务是否仍在运行。');
    }
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '请求失败，请重试。');
    return value;
  } finally { clearTimeout(timeout); }
}
function updateControls() {
  $('save-architecture').disabled = busy || !graph || graph.schema_version !== '0.2';
  $('cancel-analysis').hidden = !activeRun;
}
function notice(message, error = false) {
  $('notice').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = !message;
}
function keyState(configured, saved) {
  $('key-state').textContent = configured ? (saved ? '已安全保存' : '本次已设置') : '未配置';
  $('key-dot').textContent = configured ? '●' : '○';
}
async function task(action, message = '正在处理…') {
  if (busy) return;
  busy = true; notice(''); $('progress').textContent = message;
  const controls = ['pick-directory', 'folder-input', 'import', 'example', 'refresh', 'analyze', 'save-architecture', 'save-key', 'delete-key'];
  for (const id of controls) $(id).disabled = true;
  const timer = setInterval(async () => {
    if (activeRun) return;
    try { const state = await api('/api/state'); if (state.busy) $('progress').textContent = state.phase; } catch { /* Active operation reports errors. */ }
  }, 1000);
  try { await action(); }
  catch (error) { notice(error.message, true); $('settings-feedback').textContent = error.message; }
  finally { clearInterval(timer); busy = false; for (const id of controls) $(id).disabled = false; $('progress').textContent = '就绪'; updateControls(); }
}
function part(tag, cls, text) { const el = document.createElement(tag); el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function row(tag, label, icon, classes = '') {
  const el = part(tag, 'tree-row ' + classes);
  el.append(part('span', 'chevron', tag === 'summary' ? '›' : ''), part('span', 'tree-icon', icon), part('span', 'tree-label', label));
  return el;
}
function remember(details, key) {
  details.open = expanded.has(key) || Boolean($('file-filter').value.trim());
  details.addEventListener('toggle', () => { if (details.open) expanded.add(key); else expanded.delete(key); });
}
function renderTree() {
  $('file-list').replaceChildren();
  if (!repository) return;
  const needle = $('file-filter').value.trim().toLocaleLowerCase();
  const root = { folders: new Map(), files: [] };
  for (const file of repository.files) {
    if (needle && !file.path.toLocaleLowerCase().includes(needle) && !file.symbols.some(s => s.qualified_name.toLocaleLowerCase().includes(needle))) continue;
    const parts = file.path.split('/'); let branch = root;
    for (const name of parts.slice(0, -1)) { if (!branch.folders.has(name)) branch.folders.set(name, { folders: new Map(), files: [] }); branch = branch.folders.get(name); }
    branch.files.push(file);
  }
  const append = (branch, parent, prefix = '') => {
    for (const [name, folder] of [...branch.folders].sort(([a], [b]) => a.localeCompare(b))) {
      const details = document.createElement('details'), key = prefix + name + '/';
      details.dataset.folder = key; details.append(row('summary', name, '▱'));
      const children = part('div', 'tree-children'); append(folder, children, key); details.append(children); remember(details, key); parent.append(details);
    }
    for (const file of branch.files.sort((a, b) => a.path.localeCompare(b.path))) {
      const name = file.path.split('/').at(-1), hasSymbols = file.symbols.length > 0;
      const fileRow = row(hasSymbols ? 'summary' : 'button', name, name.endsWith('.py') ? 'py' : '≡', 'file-item');
      fileRow.dataset.id = file.id; fileRow.title = file.path + (file.analyzed ? '' : ' · 仅可浏览');
      fileRow.querySelector('.tree-icon').classList.toggle('python-icon', name.endsWith('.py'));
      fileRow.addEventListener('click', () => openSource(file.id));
      if (hasSymbols) {
        const details = document.createElement('details'); details.dataset.file = file.id; details.append(fileRow);
        const symbols = part('div', 'tree-children');
        for (const symbol of file.symbols) {
          const symbolRow = row('button', symbol.qualified_name, symbol.kind === 'class' ? 'C' : 'ƒ', 'symbol-item');
          symbolRow.dataset.id = symbol.id; symbolRow.querySelector('.tree-icon').classList.add('symbol-icon');
          symbolRow.append(part('span', 'tree-line', 'L' + symbol.start_line));
          symbolRow.addEventListener('click', () => openSource(symbol.id)); symbols.append(symbolRow);
        }
        details.append(symbols); remember(details, file.id); parent.append(details);
      } else parent.append(fileRow);
    }
  };
  append(root, $('file-list'));
  if (!root.files.length && !root.folders.size) $('file-list').append(part('p', 'tiny', '没有匹配的文件或符号'));
  updateSelection();
}
function updateSelection() {
  for (const el of document.querySelectorAll('.tree-row[data-id]')) {
    el.classList.toggle('selected', el.dataset.id === selection?.anchor || el.dataset.id === selection?.file);
  }
  view.select(selection?.node || selection?.file);
}
function revealFile() {
  if (!selection) return;
  // A filter may hide the selected item; reveal it without losing the source view.
  if ($('file-filter').value) { $('file-filter').value = ''; renderTree(); }
  const row = [...document.querySelectorAll('.file-item')].find(el => el.dataset.id === selection.file);
  if (!row) return;
  for (let parent = row.parentElement; parent && parent !== $('file-list'); parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
  row.scrollIntoView({ block: 'nearest' });
}
async function openSource(id, moduleId = null) {
  const request = ++sourceRequest;
  $('source-anchor').textContent = '正在读取源码…';
  try {
    const node = graph?.nodes.find(n => n.id === (moduleId || id));
    const sourceId = moduleId ? id : (node?.anchor_id || id);
    const source = await api('/api/source?id=' + encodeURIComponent(sourceId));
    if (request !== sourceRequest) return;
    selection = { file: source.id, anchor: source.anchor.id, node: node?.id };
    const file = repository?.files.find(f => f.id === source.id);
    $('source-title').textContent = source.path.split('/').at(-1);
    $('source-anchor').textContent = source.path + (sourceId !== source.id ? ' › ' + source.anchor.name : '');
    $('source-position').textContent = `L${source.anchor.start_line}–${source.anchor.end_line} · ${source.end_line} 行`;
    $('source-language').textContent = (source.language || '文本') + ' · 只读';
    $('selection-name').textContent = node?.name || (sourceId !== source.id ? source.anchor.name : source.path.split('/').at(-1));
    $('selection-location').textContent = `L${source.anchor.start_line}–${source.anchor.end_line} ↗`;
    $('node-summary').textContent = node?.summary || '当前文件可本地浏览，未纳入这张模块图。';
    $('symbol-shortcuts').replaceChildren();
    for (const anchor of node?.anchors || []) {
      const button = part('button', '', `${anchor.name} · L${anchor.start_line}`);
      button.title = `${anchor.path} · L${anchor.start_line}–${anchor.end_line}`;
      button.addEventListener('click', () => openSource(anchor.symbol_id || anchor.file_id, node.id));
      $('symbol-shortcuts').append(button);
    }
    for (const symbol of node ? [] : (file?.symbols || [])) {
      const button = part('button', '', (symbol.kind === 'class' ? 'C ' : 'ƒ ') + symbol.qualified_name);
      button.title = '定位到第 ' + symbol.start_line + ' 行'; button.addEventListener('click', () => openSource(symbol.id));
      $('symbol-shortcuts').append(button);
    }
    renderSource($('source'), source); updateSelection();
    if (file) for (const folder of document.querySelectorAll('details[data-folder]')) if (file.path.startsWith(folder.dataset.folder)) folder.open = true;
  } catch (error) { if (request === sourceRequest) { $('source-anchor').textContent = '源码未能打开'; notice(error.message, true); } }
}
function renderGraph(value) {
  graph = value; view.setGraph(value, repository?.path);
  $('graph-empty').hidden = Boolean(value);
  if (!value) return;
  $('graph-stats').textContent = `${value.nodes.length} 个模块 · ${value.edges.length} 条关系${value.incomplete ? ' · 部分覆盖' : ''}`;
  $('mode').textContent = value.mode === 'deepseek-agent' ? 'DeepSeek · Agent 语义图' : (value.mode === 'saved-agent' ? '已保存 · 语义图' : (value.mode === 'deepseek' ? 'DeepSeek · 语义分组' : '本地依赖预览'));
  $('project-type').textContent = value.project_type; $('project-summary').textContent = value.summary + (value.incomplete_regions?.length ? '\n未覆盖：' + value.incomplete_regions.join('；') : '');
  $('usage').textContent = value.usage ? `${value.usage.requests} 次请求${value.usage.tool_calls === undefined ? '' : ` · ${value.usage.tool_calls} 次工具`} · ${value.usage.estimated ? '估算 ' : ''}${value.usage.total_tokens.toLocaleString()} token` : '';
  if (value.warnings?.length) notice(value.warnings.join('；'));
  updateSelection();
  updateControls();
}
async function imported(result) {
  if (result.cancelled) { notice('已取消选择，当前项目保持不变。'); return; }
  const previous = selection;
  const sameRepository = repository?.path === result.repository.path;
  if (!sameRepository) { expanded.clear(); $('file-filter').value = ''; }
  repository = result.repository; selection = null; sourceRequest++;
  if (!activeRun) $('agent-progress').hidden = true;
  $('consent').checked = false; $('project-name').textContent = repository.name; $('tree-root').textContent = repository.name;
  $('save-architecture').disabled = !repository.architecture?.can_save || !result.graph || !['deepseek-agent', 'saved-agent'].includes(result.graph.mode);
  if (repository.architecture?.error && !repository.architecture.loaded) notice('未恢复已保存架构图：' + repository.architecture.error);
  $('file-count').textContent = repository.files.length; $('repo-path').value = repository.path;
  $('index-status').textContent = `${repository.analyzed_count} 个源文件已索引`;
  renderTree(); renderGraph(result.graph);
  const entry = result.graph?.nodes.find(n => n.entry);
  const fileId = sameRepository && repository.files.some(f => f.id === previous?.file) ? previous.file : (entry?.anchor_id || entry?.id || repository.files[0]?.id);
  if (fileId) await openSource(fileId);
}
$('path-toggle').addEventListener('click', () => { $('path-form').hidden = !$('path-form').hidden; $('path-toggle').setAttribute('aria-expanded', !$('path-form').hidden); if (!$('path-form').hidden) $('repo-path').focus(); });
async function importPath(path) { const result = await api('/api/repository', { path }); directoryHandle = null; await imported(result); }
$('path-form').addEventListener('submit', e => { e.preventDefault(); task(async () => { if (!$('repo-path').value.trim()) throw new Error('请输入项目完整路径。'); await importPath($('repo-path').value.trim()); }, '正在导入项目…'); });
const supportedSuffixes = new Set(['py','md','txt','json','toml','yaml','yml','ini','cfg','js','jsx','ts','tsx','html','css','sql','rs','go','java','c','h','cc','cxx','cpp','hh','hpp','cs','kt','kts','swift','php','rb','sh','m','mm','xml','mod']);
const skipDirectories = new Set(['.git','.venv','venv','env','node_modules','__pycache__','dist','build','target','.idea','.vs']);
function allowedFile(path) {
  const parts = path.replaceAll('\\', '/').split('/').slice(1), name = parts.at(-1).toLowerCase();
  if (parts.join('/') === '.repolens/architecture.json') return true;
  return !parts.some(p => p.startsWith('.') || skipDirectories.has(p)) && !/secret|credential|\.env|private_key/i.test(name) && (supportedSuffixes.has(name.split('.').at(-1)) || ['cmakelists.txt','dockerfile','makefile'].includes(name));
}
async function uploadFolder(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const eligible = files.filter(file => allowedFile(file.webkitRelativePath || file.name));
  if (eligible.length > uploadLimits.files + 1) throw new Error(`最多处理 ${uploadLimits.files.toLocaleString()} 个可浏览文本文件，请选择更小的目录或使用路径导入。`);
  const selected = [];
  let bytes = 0;
  for (const file of eligible) {
    const path = file.webkitRelativePath || file.name;
    if (file.size > (path.endsWith('/.repolens/architecture.json') ? 4000000 : uploadLimits.fileBytes)) continue;
    bytes += file.size;
    if (bytes > uploadLimits.totalBytes) throw new Error(`所选文本内容超过 ${(uploadLimits.totalBytes / 1000000).toFixed(0)} MB，请选择更小的项目目录。`);
    // Preserve a UTF-8 BOM so path and browser imports share fingerprints.
    let content;
    try { content = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer()); }
    catch { continue; }
    selected.push({ path, content });
  }
  if (!selected.length) throw new Error('目录中没有可浏览的文本文件。');
  await imported(await api('/api/repository/upload', { files: selected }));
}
async function handleFiles(handle, prefix = handle.name, found = []) {
  for await (const [name, entry] of handle.entries()) {
    if (entry.kind === 'directory') {
      if (name.startsWith('.') || skipDirectories.has(name)) continue;
      await handleFiles(entry, prefix + '/' + name, found);
    } else if (allowedFile(prefix + '/' + name)) {
      const file = await entry.getFile();
      Object.defineProperty(file, 'webkitRelativePath', { value: prefix + '/' + name }); found.push(file);
      if (found.length > uploadLimits.files) throw new Error('目录文件数超过配置上限，请选择更小的目录。');
    }
  }
  return found;
}
async function importHandle(handle) {
  const files = await handleFiles(handle);
  try {
    const folder = await handle.getDirectoryHandle('.repolens'), file = await (await folder.getFileHandle('architecture.json')).getFile();
    Object.defineProperty(file, 'webkitRelativePath', { value: handle.name + '/.repolens/architecture.json' }); files.push(file);
  } catch (error) { if (error.name !== 'NotFoundError') throw error; }
  await uploadFolder(files); directoryHandle = handle;
}
$('pick-directory').addEventListener('click', async () => {
  if (!window.showDirectoryPicker) { $('folder-input').click(); return; }
  try {
    // Called directly from the click gesture so the browser may show its picker.
    const handle = await window.showDirectoryPicker({ mode: 'read' });
    await task(() => importHandle(handle), '正在读取所选目录…');
  } catch (error) { if (error.name !== 'AbortError') notice('无法打开目录：' + error.message, true); }
});
$('folder-input').addEventListener('change', () => task(async () => {
  await uploadFolder($('folder-input').files); directoryHandle = null;
  $('folder-input').value = '';
}, '正在导入所选项目…'));
$('example').addEventListener('click', () => task(async () => { const result = await api('/api/example', {}); directoryHandle = null; await imported(result); }, '正在打开示例…'));
$('refresh').addEventListener('click', () => task(async () => { if (!repository) throw new Error('请先打开一个项目。'); if (directoryHandle) await importHandle(directoryHandle); else await importPath(repository.path); }, '正在重新索引…'));
$('file-filter').addEventListener('input', renderTree);
$('reveal-file').addEventListener('click', revealFile);
$('settings-open').addEventListener('click', () => { $('settings-feedback').textContent = ''; $('settings-dialog').showModal(); });
$('settings-close').addEventListener('click', () => $('settings-dialog').close());
$('settings-dialog').addEventListener('close', () => { $('api-key').value = ''; });
$('save-key').addEventListener('click', () => task(async () => {
  const input = $('api-key'), key = input.value; input.value = '';
  const result = await api('/api/key', { api_key: key, remember: $('remember').checked });
  keyState(result.key_configured, result.key_saved);
  $('settings-feedback').textContent = result.key_saved ? '已安全保存到 Windows 凭据管理器。' : '仅在本次服务内存中使用，关闭服务后清除。';
}));
$('delete-key').addEventListener('click', () => task(async () => { await api('/api/key/delete', {}); $('api-key').value = ''; keyState(false, false); $('settings-feedback').textContent = '内存 Key 与已保存凭据已清除。'; }));
async function followRun(id) {
  activeRun = id; updateControls();
  try {
    const final = await new Promise((resolve, reject) => {
      const stream = new EventSource('/api/agent/events?id=' + encodeURIComponent(id));
      const finish = value => { stream.close(); resolve(value); };
      stream.onmessage = event => {
        const run = JSON.parse(event.data); $('progress').textContent = run.message;
        $('agent-progress').hidden = false;
        $('agent-progress').textContent = run.events.slice(-5).map(item => item.message).join('\n');
        if (run.status !== 'running') finish(run);
      };
      stream.onerror = async () => {
        stream.close();
        // Fall back to a small status response if the event connection drops.
        try {
          let run;
          do { run = await api('/api/agent/run?id=' + encodeURIComponent(id)); $('progress').textContent = run.message; if (run.status === 'running') await sleep(1000); } while (run.status === 'running');
          resolve(run);
        } catch (error) { reject(error); }
      };
    });
    if (final.status !== 'complete') throw new Error(final.error || '生成已停止，原图保持不变。');
    const state = await api('/api/state'); repository = state.repository;
    const previous = selection; renderGraph(state.graph);
    if (previous) await openSource(previous.anchor, state.graph.nodes.some(n => n.id === previous.node) ? previous.node : null);
  } finally { activeRun = null; updateControls(); }
}
$('cancel-analysis').addEventListener('click', async () => {
  if (!activeRun) return;
  try { await api('/api/agent/cancel', { id: activeRun }); $('progress').textContent = '正在取消，等待当前模型请求结束…'; }
  catch (error) { notice(error.message, true); }
});
$('analyze').addEventListener('click', () => task(async () => {
  if (!repository) throw new Error('请先打开代码项目。');
  if (!$('consent').checked) throw new Error('请先勾选同意按需发送必要代码片段。');
  if (!window.confirm('确认启动 Agent 分析？模型会按需读取有限代码片段和项目文档，导入项目本身不会触发模型请求。')) return;
  const result = await api('/api/agent/analyze', { goal: $('goal').value, model: $('model').value, confirmed: true });
  await followRun(result.id);
}, '正在启动 Agent 探索…'));
$('save-architecture').addEventListener('click', () => task(async () => {
  if (!graph || !['deepseek-agent', 'saved-agent'].includes(graph.mode)) throw new Error('请先生成通过校验的语义架构图。');
  if (!window.confirm('确认将当前语义架构图保存到项目的 .repolens/architecture.json？')) return;
  if (directoryHandle) {
    const permission = await directoryHandle.requestPermission({ mode: 'readwrite' });
    if (permission !== 'granted') throw new Error('未获得写入权限，架构图尚未保存。');
    const payload = await api('/api/architecture/export', { confirmed: true, model: $('model').value, goal: $('goal').value });
    const folder = await directoryHandle.getDirectoryHandle('.repolens', { create: true });
    const file = await folder.getFileHandle('architecture.json', { create: true });
    const writer = await file.createWritable();
    try { await writer.write(JSON.stringify(payload, null, 2) + '\n'); await writer.close(); }
    catch (error) { await writer.abort().catch(() => {}); throw error; }
    notice('架构图已保存到所选项目 .repolens/architecture.json，下次打开时自动校验恢复。');
  } else if (repository?.architecture?.can_save) {
    const result = await api('/api/architecture/save', { confirmed: true, model: $('model').value, goal: $('goal').value });
    repository = result.repository; renderGraph(result.graph);
    notice('架构图已保存到项目 .repolens/architecture.json。');
  } else {
    const payload = await api('/api/architecture/export', { confirmed: true, model: $('model').value, goal: $('goal').value });
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2) + '\n'], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'architecture.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    notice('架构图已下载。请放入项目 .repolens/architecture.json；也可使用路径导入后直接保存。');
  }
}, '正在保存架构图…'));
$('zoom-in').addEventListener('click', () => view.zoom(1.2));
$('zoom-out').addEventListener('click', () => view.zoom(1 / 1.2));
$('zoom-fit').addEventListener('click', () => view.fit());
$('focus-graph').addEventListener('click', () => view.fit());
$('layout-reset').addEventListener('click', () => view.reset());
api('/api/state').then(async state => {
  if (state.limits) uploadLimits = {
    files: state.limits.browse_files,
    fileBytes: state.limits.browse_file_bytes,
    totalBytes: state.limits.browse_bytes,
  };
  if (state.agent?.request_timeout_seconds) analysisTimeoutMs = state.agent.request_timeout_seconds * 1000 + 15000;
  keyState(state.key_configured, state.key_saved); $('model').value = state.default_model;
  if (state.repository) await imported(state);
  if (state.key_error) notice(state.key_error, true);
  if (state.run?.status === 'running') await task(() => followRun(state.run.id), '正在恢复生成进度…');
  else if (state.busy) notice('当前有任务正在运行，请稍候再操作。');
}).catch(error => notice(error.message, true));
