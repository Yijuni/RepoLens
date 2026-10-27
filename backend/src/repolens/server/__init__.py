"""Loopback-only prototype HTTP server and application composition."""

import argparse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from repolens.analysis import analyze
from repolens.contracts import dependency_preview
from repolens.errors import RepoLensError
from repolens.indexing import scan_repository
from repolens.llm import DEFAULT_MODEL, DeepSeekClient, validate_key
from repolens import platform as credentials
from repolens.platform.dialogs import choose_directory

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PROJECT_ROOT = Path(__file__).resolve().parents[4]


class AppState:
    def __init__(self, repository: str | Path | None = None, *, client_factory=DeepSeekClient, restore_key=True,
                 directory_picker=choose_directory):
        self.index = scan_repository(repository) if repository else None
        self.graph = dependency_preview(self.index) if self.index else None
        self.lock = threading.Lock()
        self.phase = "就绪"
        self.api_key = None
        self.saved = False
        self.key_error = None
        self.client_factory = client_factory
        self.directory_picker = directory_picker
        if restore_key:
            try:
                self.api_key = credentials.load_key()
                self.saved = self.api_key is not None
            except RepoLensError as exc:
                self.key_error = str(exc)

    def summary(self) -> dict:
        return {"key_configured": bool(self.api_key), "key_saved": self.saved,
                "key_error": self.key_error, "default_model": DEFAULT_MODEL,
                "busy": self.lock.locked(), "phase": self.phase,
                "repository": self.repository_summary(), "graph": self.graph}

    def repository_summary(self) -> dict | None:
        if self.index is None:
            return None
        return {"name": self.index.root.name, "path": str(self.index.root),
                "files": [{"id": file.id, "path": file.path,
                           "analyzed": file.id in self.index.files,
                           "symbols": [symbol.summary() for symbol in getattr(file, "definitions", ())],
                           "line_count": file.line_count} for file in self.index.documents.values()],
                "analyzed_count": len(self.index.files),
                "edge_count": len(self.index.edges), "warnings": self.index.warnings,
                "snapshot": self.index.snapshot}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, state: AppState):
        super().__init__(("127.0.0.1", port), Handler)
        self.state = state
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, _format, *args):
        # Never log request bodies, source text, API keys or remote responses.
        pass

    def _send(self, status: int, payload: bytes, content_type="application/json; charset=utf-8", cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        if cookie:
            self.send_header("Set-Cookie", f"repolens_session={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value):
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _authorize(self, api=True):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            raise RepoLensError("拒绝非本机来源。", 403)
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            raise RepoLensError("拒绝跨来源请求。", 403)
        if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            raise RepoLensError("拒绝跨来源请求。", 403)
        if api:
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
                token = cookies["repolens_session"].value if "repolens_session" in cookies else ""
            except Exception:
                token = ""
            if not secrets.compare_digest(token, self.server.token):
                raise RepoLensError("本地会话已过期，请刷新页面。", 403)

    def _body(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise RepoLensError("请求必须为 JSON。", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RepoLensError("请求长度无效。") from None
        if not 0 < length <= 16_384:
            raise RepoLensError("请求内容过大或为空。", 413)
        try:
            result = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeError):
            raise RepoLensError("请求不是合法 JSON。") from None
        if not isinstance(result, dict):
            raise RepoLensError("请求必须是 JSON 对象。")
        return result

    def _dispatch(self):
        path = urlsplit(self.path).path
        self._authorize(api=path.startswith("/api/"))
        state = self.server.state
        if self.command == "GET" and path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if self.command == "GET" and path in ("/", "/app.js", "/graph.mjs", "/source.mjs", "/style.css"):
            name, content_type = {"/": ("index.html", "text/html; charset=utf-8"),
                                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                                  "/graph.mjs": ("graph.mjs", "text/javascript; charset=utf-8"),
                                  "/source.mjs": ("source.mjs", "text/javascript; charset=utf-8"),
                                  "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
            self._send(200, (WEB_ROOT / name).read_bytes(), content_type, cookie=path == "/")
            return
        if self.command == "GET" and path == "/api/state":
            self._json(200, state.summary())
            return
        if self.command == "GET" and path == "/api/source":
            if not state.index:
                raise RepoLensError("请先导入 Python 仓库。")
            file_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            self._json(200, state.index.source(file_id))
            return
        if self.command != "POST":
            raise RepoLensError("接口不存在。", 404)
        body = self._body()
        if not state.lock.acquire(blocking=False):
            raise RepoLensError("当前任务正在执行，请等待完成。", 409)
        try:
            if path == "/api/key":
                key = validate_key(body.get("api_key", ""))
                remember = body.get("remember", False)
                if type(remember) is not bool:
                    raise RepoLensError("保存选项无效。")
                if remember:
                    credentials.save_key(key)
                else:
                    # Don't leave an older saved key active after a memory-only update.
                    credentials.delete_key()
                state.api_key, state.saved, state.key_error = key, remember, None
                self._json(200, {"key_configured": True, "key_saved": remember})
            elif path == "/api/key/delete":
                credentials.delete_key()
                state.api_key, state.saved, state.key_error = None, False, None
                self._json(200, {"key_configured": False, "key_saved": False})
            elif path in ("/api/repository", "/api/example", "/api/repository/pick"):
                state.phase = "正在建立 Python 索引……"
                if path == "/api/example":
                    root = PROJECT_ROOT / "samples" / "mini_project"
                elif path == "/api/repository/pick":
                    state.phase = "请在 Windows 目录选择器中选择项目……"
                    root = state.directory_picker()
                    if root is None:
                        self._json(200, {"cancelled": True})
                        return
                else:
                    root = body.get("path")
                    if not isinstance(root, str) or not root.strip():
                        raise RepoLensError("请输入本地仓库目录。")
                candidate_index = scan_repository(root)
                candidate_graph = dependency_preview(candidate_index)
                state.index, state.graph = candidate_index, candidate_graph
                self._json(200, {"repository": state.repository_summary(), "graph": state.graph})
            elif path == "/api/analyze":
                if body.get("confirmed") is not True:
                    raise RepoLensError("请先确认将当前索引摘要发送至 DeepSeek。")
                if state.index is None or not state.api_key:
                    raise RepoLensError("请先导入仓库并配置 DeepSeek API Key。")
                client = state.client_factory(state.api_key, model=body.get("model", DEFAULT_MODEL))
                graph = analyze(state.index, body.get("goal", ""), client,
                                lambda message: setattr(state, "phase", message))
                state.graph = graph
                self._json(200, {"graph": graph})
            else:
                raise RepoLensError("接口不存在。", 404)
        finally:
            state.phase = "就绪"
            state.lock.release()

    def _handle(self):
        try:
            self._dispatch()
        except RepoLensError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._json(500, {"error": "本地处理失败，请检查目录后重试或重新启动。"})

    do_GET = _handle
    do_POST = _handle


def main():
    parser = argparse.ArgumentParser(description="RepoLens 最小 DeepSeek 试用版")
    parser.add_argument("--repo", help="启动时导入的 Python 仓库；默认使用内置示例")
    parser.add_argument("--port", type=int, default=0, help="本机端口，默认随机")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口必须为 0–65535")
    try:
        state = AppState(args.repo or PROJECT_ROOT / "samples" / "mini_project")
        with LocalServer(args.port, state) as server:
            print(f"RepoLens: {server.origin}", flush=True)
            print("仅本机访问；Ctrl+C 退出。API Key 请在页面输入，不要放入命令行。", flush=True)
            if args.open:
                webbrowser.open(server.origin)
            server.serve_forever()
    except RepoLensError as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        print("\nRepoLens 已停止。", flush=True)
