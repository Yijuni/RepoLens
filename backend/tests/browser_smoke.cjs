// Optional developer smoke test. Uses Node >=22 and installed Chrome/Edge; no npm packages.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const net = require("node:net");

const root = path.resolve(__dirname, "../..");
const profile = path.join(root, ".repolens", "browser-check", randomUUID());
const screenshot = path.join(root, ".repolens", "preview.png");
const browserLog = path.join(root, ".repolens", "browser-check.log");
const python = path.join(root, ".conda", "python.exe");
const browser = [
  process.env.REPOLENS_BROWSER,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
].find((candidate) => candidate && fs.existsSync(candidate));
const pause = (duration) => new Promise((resolve) => setTimeout(resolve, duration));

async function until(action, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const value = await action();
    if (value) return value;
    await pause(100);
  }
  throw new Error("Timeout: " + label);
}

async function main() {
  if (!browser || !fs.existsSync(python)) throw new Error("Requires .conda Python and installed Chrome/Edge.");
  fs.mkdirSync(profile, { recursive: true });
  const portReservation = net.createServer();
  await new Promise((resolve) => portReservation.listen(0, "127.0.0.1", resolve));
  const serverPort = portReservation.address().port;
  await new Promise((resolve) => portReservation.close(resolve));
  const server = spawn(python, ["-X", "utf8", "backend/tests/browser_server.py", "--port", String(serverPort)],
    { cwd: root, windowsHide: true, stdio: "ignore" });
  let chrome;
  let socket, evaluatePage;
  try {
    const url = `http://127.0.0.1:${serverPort}`;
    await until(async () => { try { return (await fetch(url)).ok; } catch { return false; } }, "server ready");
    const logDescriptor = fs.openSync(browserLog, "w");
    chrome = spawn(browser, ["--headless=new", "--disable-gpu", "--disable-breakpad", "--disable-crash-reporter", "--no-first-run", "--no-default-browser-check", "--enable-logging=stderr",
      "--disable-background-networking", "--remote-debugging-port=0", "--user-data-dir=" + profile, "about:blank"],
    { windowsHide: true, stdio: ["ignore", "ignore", logDescriptor] });
    fs.closeSync(logDescriptor);
    const activePort = path.join(profile, "DevToolsActivePort");
    const port = await until(() => fs.existsSync(activePort) && fs.readFileSync(activePort, "utf8").split("\n")[0], "browser debug port");
    const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const tab = tabs.find((item) => item.type === "page");
    socket = new WebSocket(tab.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let nextId = 0;
    const pending = new Map();
    const errors = [];
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id && pending.has(message.id)) {
        const { resolve, reject, timer } = pending.get(message.id);
        clearTimeout(timer); pending.delete(message.id);
        message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
      }
      if (message.method === "Page.javascriptDialogOpening") command("Page.handleJavaScriptDialog", { accept: true }).catch(() => {});
      if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails.text);
      if (message.method === "Log.entryAdded" && message.params.entry.level === "error") errors.push(message.params.entry.text);
    };
    const command = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++nextId;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("CDP timeout: " + method)); }, 10000);
      pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expression) => {
      const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
      return result.result.value;
    };
    evaluatePage = evaluate;
    await command("Runtime.enable");
    await command("Log.enable");
    await command("Page.enable");
    await command("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1050, deviceScaleFactor: 1, mobile: false });
    await command("Page.navigate", { url });
    await until(() => evaluate("document.querySelectorAll('.graph-node').length === 4"), "four graph nodes");
    await evaluate("document.querySelector('.graph-node').dispatchEvent(new MouseEvent('click', { bubbles: true }))");
    await until(() => evaluate("document.querySelectorAll('.code-line').length > 0"), "source opens");
    await evaluate("document.getElementById('analyze').click()");
    await until(() => evaluate("document.getElementById('notice').textContent.includes('勾选')"), "upload consent guard");
    const fixturePath = await evaluate("document.getElementById('repo-path').value");
    await evaluate("document.getElementById('refresh').click()");
    await until(() => evaluate("document.getElementById('notice').hidden && document.querySelectorAll('.graph-node').length === 4 && !document.getElementById('refresh').disabled"), "fixture reimport");
    await evaluate("document.querySelector('.file-item').click()");
    await until(() => evaluate("document.querySelectorAll('.code-line').length > 0"), "file list source opens");
    const before = await evaluate("document.getElementById('graph').lastElementChild.getAttribute('transform')");
    await evaluate("document.getElementById('zoom-in').click()");
    const after = await evaluate("document.getElementById('graph').lastElementChild.getAttribute('transform')");
    if (after === before) throw new Error("Zoom did not change the canvas");
    await evaluate("document.getElementById('zoom-fit').click()");
    await evaluate(`document.getElementById('repo-path').value = ${JSON.stringify(fixturePath)}; document.getElementById('path-form').requestSubmit()`);
    await until(() => evaluate("!document.getElementById('import').disabled"), "fixture import");
    await evaluate("document.getElementById('consent').checked = true; document.getElementById('analyze').click()");
    await until(() => evaluate("document.getElementById('mode').textContent.includes('Agent') && !document.getElementById('analyze').disabled"), "semantic Agent finishes", 20000);
    if (await evaluate("document.querySelectorAll('.edge-label').length") !== 1) throw new Error("Semantic relation missing");
    await evaluate("document.querySelector('.graph-node').dispatchEvent(new MouseEvent('click', { bubbles: true }))");
    await until(() => evaluate("document.querySelectorAll('#symbol-shortcuts button').length >= 2"), "multiple module anchors");
    await evaluate("document.querySelectorAll('#symbol-shortcuts button')[1].click()");
    await until(() => evaluate("document.querySelectorAll('.anchor-line').length > 0"), "secondary source anchor");
    await evaluate("document.getElementById('save-architecture').click()");
    await until(() => evaluate("document.getElementById('notice').textContent.includes('已保存') && !document.getElementById('save-architecture').disabled"), "save current repository");
    await evaluate("document.getElementById('refresh').click()");
    await until(() => evaluate("document.getElementById('mode').textContent.includes('已保存') && !document.getElementById('refresh').disabled"), "restore saved graph");
    // Exercise the browser directory-handle branch with a controlled in-memory
    // handle. Opening the OS picker itself remains a manual browser check.
    await evaluate(`(() => {
      const missing = () => new DOMException('missing', 'NotFoundError');
      function folder(name) {
        const entries = new Map();
        return { name, kind:'directory', entries:async function*(){ yield* entries; },
          requestPermission:async () => 'granted',
          async getDirectoryHandle(name, options={}) {
            if (!entries.has(name)) { if (!options.create) throw missing(); entries.set(name,folder(name)); }
            return entries.get(name);
          },
          async getFileHandle(name, options={}) {
            if (!entries.has(name)) {
              if (!options.create) throw missing();
              let text='';
              entries.set(name,{name,kind:'file',getFile:async()=>new File([text],name),createWritable:async()=>({write:async value=>{text=value;},close:async()=>{},abort:async()=>{}})});
            }
            return entries.get(name);
          },
        };
      }
      window.testHandle = folder('browser-fixture');
      window.showDirectoryPicker = async () => window.testHandle;
      window.prepareHandle = async () => {
        for (const [name, text] of [['main.py','from service import run\\nrun()\\n'],['service.py','def run():\\n    return 1\\n']]) {
          const file=await testHandle.getFileHandle(name,{create:true}), writer=await file.createWritable(); await writer.write(text); await writer.close();
        }
      };
    })()`);
    await evaluate('window.prepareHandle()');
    await evaluate("document.getElementById('pick-directory').click()");
    await until(() => evaluate("document.getElementById('project-name').textContent === 'browser-fixture' && !document.getElementById('pick-directory').disabled"), "browser handle import");
    await evaluate("document.getElementById('consent').checked=true; document.getElementById('analyze').click()");
    await until(() => evaluate("document.getElementById('mode').textContent.includes('Agent') && !document.getElementById('analyze').disabled"), "browser handle generation");
    await evaluate("document.getElementById('save-architecture').click()");
    await until(() => evaluate("document.getElementById('notice').textContent.includes('已保存到所选项目') && !document.getElementById('save-architecture').disabled"), "browser handle save");
    const saved = await evaluate("(async()=>JSON.parse(await (await (await testHandle.getDirectoryHandle('.repolens')).getFileHandle('architecture.json')).getFile().then(f=>f.text())))()");
    if (saved.format_version !== '2') throw new Error('Browser handle did not save architecture');
    await evaluate("document.getElementById('pick-directory').click()");
    await until(() => evaluate("document.getElementById('mode').textContent.includes('已保存') && !document.getElementById('pick-directory').disabled"), "browser handle restore");
    for (const scale of [1.25, 1.5, 2]) {
      await command("Emulation.setDeviceMetricsOverride", { width: 1500, height: 1050, deviceScaleFactor: scale, mobile: false });
      if (!await evaluate("document.getElementById('graph').getBoundingClientRect().width > 250")) throw new Error("Graph hidden at DPI " + scale);
    }
    const shot = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    fs.writeFileSync(screenshot, Buffer.from(shot.data, "base64"));
    if (errors.length) throw new Error("Browser errors: " + errors.join("; "));
    console.log("PASS: graph/source/consent/zoom, mock Agent, relation styles, multiple anchors, save/reimport, DPI emulation; no JS/CSP errors.");
    console.log("Screenshot: " + screenshot);
    await command("Browser.close").catch(() => {});
  } catch (error) {
    if (socket && socket.readyState === 1) {
      console.error("Check failed: " + error.message);
      console.error(await evaluatePage("JSON.stringify({notice:document.getElementById('notice').textContent, progress:document.getElementById('progress').textContent, disabled:document.getElementById('save-architecture').disabled})"));
    }
    throw error;
  } finally {
    if (socket) socket.close();
    if (chrome && chrome.exitCode === null) chrome.kill();
    if (server.exitCode === null) server.kill();
    // The profile is a unique directory inside this workspace, never a user's browser profile.
    if (!profile.startsWith(path.join(root, ".repolens", "browser-check") + path.sep)) throw new Error("Unsafe cleanup target");
    await pause(500);
    fs.rmSync(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 300 });
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
