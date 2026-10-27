import { GraphView } from './graph.mjs';
import { renderSource } from './source.mjs';

const $ = id => document.getElementById(id);
let repository = null, graph = null, selection = null, sourceRequest = 0, busy = false;
let uploadLimits = { files: 5000, fileBytes: 1000000, totalBytes: 64000000 };
let analysisTimeoutMs = 75000;
const expanded = new Set();
const view = new GraphView($('graph'), id => openSource(id));

async function api(path, body) {
  let response;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === '/api/analyze' ? analysisTimeoutMs : 20000);
  try {
    response = await fetch(path, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(path === '/api/analyze' ? `模型请求超过 ${Math.round(analysisTimeoutMs / 1000)} 秒，已停止等待；请检查模型名、网络和余额后重试。` : '本地操作超时，请关闭选择器后重试。');
    throw new Error('无法连接本地服务，请检查服务是否仍在运行。');
  } finally { clearTimeout(timeout); }
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败，请重试。');
  return value;
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
  const controls = ['pick-directory', 'folder-input', 'import', 'example', 'refresh', 'analyze', 'save-key', 'delete-key'];
  for (const id of controls) $(id).disabled = true;
  const timer = setInterval(async () => {
    try { const state = await api('/api/state'); if (state.busy) $('progress').textContent = state.phase; } catch { /* The active request reports connection errors. */ }
  }, 1000);
  try { await action(); }
  catch (error) { notice(error.message, true); $('settings-feedback').textContent = error.message; }
  finally { clearInterval(timer); busy = false; for (const id of controls) $(id).disabled = false; $('progress').textContent = '就绪'; }
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
  view.select(selection?.file);
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
async function openSource(id) {
  const request = ++sourceRequest;
  $('source-anchor').textContent = '正在读取源码…';
  try {
    const source = await api('/api/source?id=' + encodeURIComponent(id));
    if (request !== sourceRequest) return;
    selection = { file: source.id, anchor: source.anchor.id };
    const node = graph?.nodes.find(n => n.id === source.id);
    const file = repository?.files.find(f => f.id === source.id);
    $('source-title').textContent = source.path.split('/').at(-1);
    $('source-anchor').textContent = source.path + (id !== source.id ? ' › ' + source.anchor.name : '');
    $('source-position').textContent = `L${source.anchor.start_line}–${source.anchor.end_line} · ${source.end_line} 行`;
    $('source-language').textContent = (source.language || '文本') + ' · 只读';
    $('selection-name').textContent = id !== source.id ? source.anchor.name : source.path.split('/').at(-1);
    $('selection-location').textContent = `L${source.anchor.start_line}–${source.anchor.end_line} ↗`;
    $('node-summary').textContent = node?.summary || '当前文件可本地浏览，未纳入这张模块图。';
    $('symbol-shortcuts').replaceChildren();
    for (const symbol of file?.symbols || []) {
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
  $('graph-stats').textContent = `${value.nodes.length} 个模块 · ${value.edges.length} 条依赖${value.incomplete ? ' · 部分覆盖' : ''}`;
  $('mode').textContent = value.mode === 'deepseek' ? 'DeepSeek · 语义分组' : '本地依赖预览';
  $('project-type').textContent = value.project_type; $('project-summary').textContent = value.summary;
  $('usage').textContent = value.usage ? `${value.usage.requests} 次请求 · ${value.usage.estimated ? '估算 ' : ''}${value.usage.total_tokens.toLocaleString()} token` : '';
  if (value.warnings.length) notice(value.warnings.join('；'));
  updateSelection();
}
async function imported(result) {
  if (result.cancelled) { notice('已取消选择，当前项目保持不变。'); return; }
  const previous = selection;
  const sameRepository = repository?.path === result.repository.path;
  if (!sameRepository) { expanded.clear(); $('file-filter').value = ''; }
  repository = result.repository; selection = null; sourceRequest++;
  $('consent').checked = false; $('project-name').textContent = repository.name; $('tree-root').textContent = repository.name;
  $('file-count').textContent = repository.files.length; $('repo-path').value = repository.path;
  $('index-status').textContent = `${repository.analyzed_count} 个源文件已索引`;
  renderTree(); renderGraph(result.graph);
  const entry = result.graph?.nodes.find(n => n.entry);
  const fileId = sameRepository && repository.files.some(f => f.id === previous?.file) ? previous.file : (entry?.id || repository.files[0]?.id);
  if (fileId) await openSource(fileId);
}
$('path-toggle').addEventListener('click', () => { $('path-form').hidden = !$('path-form').hidden; $('path-toggle').setAttribute('aria-expanded', !$('path-form').hidden); if (!$('path-form').hidden) $('repo-path').focus(); });
$('path-form').addEventListener('submit', e => { e.preventDefault(); task(async () => { if (!$('repo-path').value.trim()) throw new Error('请输入项目完整路径。'); await imported(await api('/api/repository', { path: $('repo-path').value.trim() })); }, '正在导入项目…'); });
async function uploadFolder(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const supportedSuffixes = ['py','md','txt','json','toml','yaml','yml','ini','cfg','js','jsx','ts','tsx','html','css','sql','rs','go','java','c','h','cpp','hpp'];
  const eligible = files.filter(file => supportedSuffixes.includes((file.webkitRelativePath || file.name).toLowerCase().split('.').pop()));
  if (eligible.length > uploadLimits.files) throw new Error(`最多处理 ${uploadLimits.files.toLocaleString()} 个可浏览文本文件，请选择更小的目录或使用路径导入。`);
  const selected = [];
  let bytes = 0;
  for (const file of eligible) {
    const path = file.webkitRelativePath || file.name;
    if (file.size > uploadLimits.fileBytes) continue;
    bytes += file.size;
    if (bytes > uploadLimits.totalBytes) throw new Error(`所选文本内容超过 ${(uploadLimits.totalBytes / 1000000).toFixed(0)} MB，请选择更小的项目目录。`);
    selected.push({ path, content: await file.text() });
  }
  if (!selected.length) throw new Error('目录中没有可浏览的文本文件。');
  await imported(await api('/api/repository/upload', { files: selected }));
}
$('pick-directory').addEventListener('click', () => $('folder-input').click());
$('folder-input').addEventListener('change', () => task(async () => {
  await uploadFolder($('folder-input').files);
  $('folder-input').value = '';
}, '正在导入所选项目…'));
$('example').addEventListener('click', () => task(async () => imported(await api('/api/example', {})), '正在打开示例…'));
$('refresh').addEventListener('click', () => task(async () => { if (!repository) throw new Error('请先打开一个项目。'); await imported(await api('/api/repository', { path: repository.path })); }, '正在重新索引…'));
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
$('analyze').addEventListener('click', () => task(async () => {
  if (!repository) throw new Error('请先打开代码项目。');
  if (!$('consent').checked) throw new Error('请先勾选同意发送当前索引摘要。');
  const result = await api('/api/analyze', { goal: $('goal').value, model: $('model').value, confirmed: true });
  renderGraph(result.graph); if (selection) await openSource(selection.anchor);
}, '正在请求模型分析…'));
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
  if (state.busy) notice('当前有任务正在运行，请稍候再操作。');
}).catch(error => notice(error.message, true));
