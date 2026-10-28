"""Loopback-only prototype HTTP server and application composition."""

import argparse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
import traceback
import uuid
from urllib.parse import parse_qs, urlsplit
import webbrowser

from repolens.analysis import analyze
from repolens.agent import explore
from repolens.config import get_config
from repolens.contracts import dependency_preview
from repolens.diagnostics import configure_logging, error_text, get_logger
from repolens.errors import RepoLensError
from repolens.indexing import (
    MAX_BROWSE_BYTES,
    MAX_BROWSE_FILE_BYTES,
    MAX_BROWSE_FILES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    scan_repository,
)
from repolens.llm import DEFAULT_MODEL, DeepSeekClient, validate_key
from repolens.persistence import decode_graph, encode_graph, load_graph, save_graph, MAX_ARCHITECTURE_BYTES
from repolens import platform as credentials
from repolens.platform.dialogs import choose_directory

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
MAX_UPLOAD_REQUEST_BYTES = MAX_BROWSE_BYTES * 2 + 8_000_000
LOGGER = get_logger()


class AppState:
    def __init__(self, repository: str | Path | None = None, *, client_factory=DeepSeekClient, restore_key=True,
                 directory_picker=choose_directory):
        LOGGER.info("state.init repository=%s", Path(repository).name if repository else "none")
        self.index = scan_repository(repository) if repository else None
        self.graph = None
        self.repository_label = None
        self.persistence_enabled = bool(repository)
        self.architecture_error = None
        self.architecture_path = None
        self.upload_root = PROJECT_ROOT / ".repolens" / "uploads" / uuid.uuid4().hex
        self.upload_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.picker_lock = threading.Lock()
        self.picker_thread = None
        self.picker_result = None
        self.picker_error = None
        self.picker_cancelled = False
        self.phase = "就绪"
        self.agent_run = None
        self.agent_guard = threading.Lock()
        self.api_key = None
        self.saved = False
        self.key_error = None
        self.client_factory = client_factory
        self.directory_picker = directory_picker
        if self.index:
            self._set_index(self.index, persistent=self.persistence_enabled)
        if restore_key:
            try:
                self.api_key = credentials.load_key()
                self.saved = self.api_key is not None
            except RepoLensError as exc:
                self.key_error = str(exc)

    def summary(self) -> dict:
        config = get_config()
        return {"key_configured": bool(self.api_key), "key_saved": self.saved,
                "key_error": self.key_error, "default_model": DEFAULT_MODEL,
                "limits": {"browse_files": MAX_BROWSE_FILES,
                            "browse_file_bytes": MAX_BROWSE_FILE_BYTES,
                            "browse_bytes": MAX_BROWSE_BYTES,
                            "analysis_files": MAX_FILES,
                            "analysis_file_bytes": config.limits.analysis_file_bytes,
                            "analysis_bytes": MAX_TOTAL_BYTES},
                "agent": {"max_rounds": config.agent.max_rounds,
                          "max_read_lines": config.agent.max_read_lines,
                          "request_timeout_seconds": config.agent.request_timeout_seconds,
                          "wall_time_seconds": config.agent.wall_time_seconds},
                "busy": self.lock.locked() or self.picker_pending(), "phase": self.phase,
                "run": self.run_status(),
                "repository": self.repository_summary(), "graph": self.graph}

    def _set_index(self, index, *, persistent: bool) -> None:
        self.index = index
        self.persistence_enabled = persistent
        if persistent:
            self.repository_label = None
        self.architecture_error = None
        self.architecture_path = None
        self.graph = dependency_preview(index)
        if persistent:
            loaded, issue = load_graph(index)
            self.architecture_error = issue
            if loaded:
                self.graph = loaded
                self.architecture_path = str(index.root / ".repolens" / "architecture.json")
                LOGGER.info("architecture.loaded snapshot=%s", index.snapshot[:12])
            elif issue:
                LOGGER.info("architecture.unavailable reason=%s", issue[:160])

    def run_status(self, run_id=None) -> dict | None:
        with self.agent_guard:
            run = self.agent_run
            if not run or run_id and run_id != run["id"]:
                return None
            return {key: run[key] for key in ("id", "status", "phase", "message", "error", "events", "usage")}

    def start_agent(self, body: dict) -> dict:
        if body.get("confirmed") is not True:
            raise RepoLensError("请先确认启动 Agent 读取必要代码片段。")
        if self.index is None or not self.api_key:
            raise RepoLensError("请先导入仓库并配置 DeepSeek API Key。")
        goal = body.get("goal", "")
        if not isinstance(goal, str) or len(goal) > 1000:
            raise RepoLensError("学习目标最多 1000 字符。")
        if self.picker_pending() or not self.lock.acquire(blocking=False):
            raise RepoLensError("当前有任务正在运行。", 409)
        try:
            config = get_config().agent
            client = self.client_factory(self.api_key, model=body.get("model", DEFAULT_MODEL),
                                         timeout=config.request_timeout_seconds)
            run = {"id": uuid.uuid4().hex, "status": "running", "phase": "INDEXING", "message": "正在准备分析……",
                   "error": None, "events": [], "cancel": threading.Event(),
                   "usage": {"requests": 0, "tool_calls": 0, "total_tokens": 0, "estimated": False}}
            with self.agent_guard:
                self.agent_run = run
            self.phase = run["message"]
            worker = threading.Thread(target=self._agent_worker, args=(run, self.index, goal, client, config), daemon=True)
            worker.start()
        except Exception:
            self.lock.release()
            raise
        return {"id": run["id"], "status": "running"}

    def _agent_worker(self, run, index, goal, client, config):
        status, failure = "failed", None
        started = time.monotonic()
        def event(value):
            with self.agent_guard:
                run["phase"] = value["phase"]
                run["message"] = value["message"]
                run["usage"] = value.get("usage", run["usage"])
                run["events"] = (run["events"] + [value])[-200:]
                self.phase = value["message"]
        try:
            LOGGER.info("agent.start run=%s files=%d", run["id"], len(index.documents))
            graph = explore(index, goal, client, settings=config, cancelled=run["cancel"].is_set, on_event=event)
            status = "complete"
        except RepoLensError as exc:
            status = "cancelled" if run["cancel"].is_set() else "failed"
            failure = str(exc)
            LOGGER.warning("agent.stopped run=%s status=%s phase=%s requests=%d tools=%d tokens=%d error=%s",
                           run["id"], status, run["phase"], run["usage"]["requests"],
                           run["usage"]["tool_calls"], run["usage"]["total_tokens"], error_text(exc))
        except Exception as exc:
            failure = "本地分析失败，请查看运行日志后重试。"
            # Keep locations and exception type, never exception payloads or locals.
            locations = " > ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                                   for frame in traceback.extract_tb(exc.__traceback__))
            LOGGER.error("agent.failed run=%s phase=%s requests=%d tools=%d tokens=%d error_type=%s locations=%s",
                         run["id"], run["phase"], run["usage"]["requests"], run["usage"]["tool_calls"],
                         run["usage"]["total_tokens"], type(exc).__name__, locations)
        finally:
            with self.agent_guard:
                if status == "complete":
                    if run["cancel"].is_set():
                        status, failure = "cancelled", "已取消生成，原架构图保持不变。"
                        LOGGER.info("agent.stopped run=%s status=cancelled reason=cancel_before_publish", run["id"])
                    else:
                        self.graph = graph
                        LOGGER.info("agent.complete run=%s requests=%d tools=%d tokens=%d", run["id"],
                                    graph["usage"]["requests"], graph["usage"]["tool_calls"], graph["usage"]["total_tokens"])
                run["status"], run["error"] = status, failure
                run["phase"] = "IDLE"
                run["usage"] = {**run["usage"], "elapsed_seconds": round(time.monotonic() - started, 2)}
                if run["error"]:
                    run["message"] = run["error"]
                run["events"] = (run["events"] + [{"phase": "IDLE", "status": run["status"],
                                                   "message": run["message"], "usage": run["usage"]}])[-200:]
                self.phase = "就绪"
                self.lock.release()

    def cancel_agent(self, run_id: str) -> dict:
        with self.agent_guard:
            run = self.agent_run
            if not run or run["id"] != run_id:
                raise RepoLensError("分析任务不存在。", 404)
            if run["status"] == "running":
                run["cancel"].set()
                run["message"] = "正在取消；等待当前请求返回后停止。"
                self.phase = run["message"]
        return {"cancel_requested": True}

    def picker_pending(self) -> bool:
        return bool(self.picker_thread and self.picker_thread.is_alive())

    def begin_picker(self) -> None:
        with self.picker_lock:
            if self.lock.locked() or self.picker_pending():
                raise RepoLensError("当前已有导入或分析任务，请等待完成。", 409)
            self.picker_result = None
            self.picker_error = None
            self.picker_cancelled = False
            self.phase = "请在 Windows 目录选择器中选择项目……"
            LOGGER.info("picker.begin")
            self.picker_thread = threading.Thread(target=self._pick_worker, daemon=True)
            self.picker_thread.start()

    def _pick_worker(self) -> None:
        started = time.monotonic()
        LOGGER.info("picker.worker_started")
        try:
            root = self.directory_picker()
            if root is None:
                with self.picker_lock:
                    self.picker_cancelled = True
                LOGGER.info("picker.cancelled elapsed_ms=%d", int((time.monotonic() - started) * 1000))
                return
            LOGGER.info("picker.selected name=%s", Path(root).name)
            candidate_index = scan_repository(root)
            candidate_graph = dependency_preview(candidate_index)
            with self.picker_lock:
                self.picker_result = (candidate_index, candidate_graph)
            LOGGER.info("picker.indexed files=%d edges=%d elapsed_ms=%d",
                        len(candidate_index.documents), len(candidate_index.edges),
                        int((time.monotonic() - started) * 1000))
        except RepoLensError as exc:
            with self.picker_lock:
                self.picker_error = str(exc)
            LOGGER.warning("picker.failed error=%s", error_text(exc))
        except Exception:
            with self.picker_lock:
                self.picker_error = "无法完成目录选择，请改用输入路径或重新启动。"
            LOGGER.exception("picker.unhandled")
        finally:
            self.phase = "就绪"

    def picker_status(self) -> dict:
        with self.picker_lock:
            pending = self.picker_pending()
            if pending:
                return {"pending": True}
            if self.picker_error:
                error = self.picker_error
                self.picker_error = None
                return {"pending": False, "error": error}
            if self.picker_cancelled:
                self.picker_cancelled = False
                return {"pending": False, "cancelled": True}
            if self.picker_result:
                candidate_index, candidate_graph = self.picker_result
                self._set_index(candidate_index, persistent=True)
                self.picker_result = None
                LOGGER.info("picker.result_delivered")
                return {"pending": False, "repository": self.repository_summary(), "graph": self.graph}
            return {"pending": False}

    def repository_summary(self) -> dict | None:
        if self.index is None:
            return None
        return {"name": self.repository_label or self.index.root.name, "path": str(self.index.root),
                "files": [{"id": file.id, "path": file.path,
                           "analyzed": file.id in self.index.files,
                           "language": getattr(file, "language", "文本"),
                           "symbols": [symbol.summary() for symbol in getattr(file, "definitions", ())],
                           "line_count": file.line_count} for file in self.index.documents.values()],
                "analyzed_count": len(self.index.files),
                "edge_count": len(self.index.edges), "warnings": self.index.warnings,
                "snapshot": self.index.snapshot,
                "architecture": {"can_save": self.persistence_enabled,
                                  "loaded": self.graph.get("mode") == "saved-agent" if self.graph else False,
                                  "path": self.architecture_path,
                                  "error": self.architecture_error}}

    def save_architecture(self, *, model: str = "unknown", goal: str = "") -> str:
        if not self.persistence_enabled or not self.index:
            raise RepoLensError("浏览器上传的临时快照不能持久保存，请使用输入路径导入项目后再保存。")
        if not self.graph or self.graph.get("schema_version") != "0.2":
            raise RepoLensError("请先生成通过校验的语义架构图。")
        path = save_graph(self.index, self.graph, model=model, goal=goal,
                          usage=self.graph.get("usage"))
        self.graph["mode"] = "saved-agent"
        self.architecture_path = str(path)
        self.architecture_error = None
        LOGGER.info("architecture.saved snapshot=%s", self.index.snapshot[:12])
        return str(path)

    def import_uploaded(self, files: list[dict]) -> tuple[object, dict]:
        if not files or len(files) > MAX_BROWSE_FILES + 1:
            raise RepoLensError(f"请选择不超过 {MAX_BROWSE_FILES} 个可浏览文本文件的目录。")
        total = 0
        root_name = "上传项目"
        staging = self.upload_root / uuid.uuid4().hex
        staging.mkdir()
        saved_data = None
        try:
            for item in files:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
                    raise RepoLensError("目录文件数据无效，请重新选择。")
                raw_path = item["path"].replace("\\", "/")
                parts = [part for part in raw_path.split("/") if part]
                if not parts or any(part in {".", ".."} for part in parts):
                    raise RepoLensError("目录包含无效的文件路径。")
                if len(parts) > 1:
                    root_name = root_name if root_name != "上传项目" else parts[0]
                    parts = parts[1:]
                relative = Path(*parts)
                if relative.is_absolute() or relative.name == "" or any(":" in part for part in parts):
                    raise RepoLensError("目录包含无效的文件路径。")
                content = item["content"]
                if relative.as_posix() == ".repolens/architecture.json":
                    saved_data = content.encode("utf-8")
                    if len(saved_data) > MAX_ARCHITECTURE_BYTES:
                        raise RepoLensError("架构文件超过 4 MB。")
                    continue
                encoded_size = len(content.encode("utf-8"))
                if encoded_size > MAX_BROWSE_FILE_BYTES:
                    raise RepoLensError(f"单个文件不能超过 {MAX_BROWSE_FILE_BYTES // 1_000_000} MB。")
                if total + encoded_size > MAX_BROWSE_BYTES:
                    raise RepoLensError(f"上传文本总量不能超过 {MAX_BROWSE_BYTES // 1_000_000} MB。")
                target = staging / relative
                target.resolve().relative_to(staging.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                encoded = content.encode("utf-8")
                for attempt in range(3):
                    try:
                        with target.open("wb") as stream:
                            stream.write(encoded)
                        break
                    except PermissionError:
                        if attempt == 2:
                            raise
                        time.sleep(0.05 * (attempt + 1))
                total += encoded_size
            candidate_index = scan_repository(staging)
            candidate_graph = dependency_preview(candidate_index)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.repository_label = root_name
        self._set_index(candidate_index, persistent=False)
        if saved_data is not None:
            try:
                self.graph = decode_graph(candidate_index, saved_data)
            except RepoLensError as exc:
                self.architecture_error = str(exc)
        LOGGER.info("repository.upload name=%s files=%d bytes=%d edges=%d", root_name,
                    len(candidate_index.documents), total, len(candidate_index.edges))
        return candidate_index, self.graph

    def cleanup_uploads(self) -> None:
        for child in self.upload_root.iterdir() if self.upload_root.exists() else ():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, state: AppState):
        super().__init__(("127.0.0.1", port), Handler)
        self.state = state
        self.token = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"

    def server_close(self):
        run = self.state.run_status()
        if run and run["status"] == "running":
            self.state.cancel_agent(run["id"])
        self.state.cleanup_uploads()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, _format, *args):
        # Request bodies, source text, API keys and remote responses never enter logs.
        LOGGER.info("http.request method=%s path=%s", self.command, urlsplit(self.path).path)

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

    def _body(self, max_bytes: int = 16_384) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise RepoLensError("请求必须为 JSON。", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RepoLensError("请求长度无效。") from None
        if not 0 < length <= max_bytes:
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
        request_started = time.monotonic()
        LOGGER.info("route.start method=%s path=%s", self.command, path)
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
        if self.command == "GET" and path in {"/api/agent/run", "/api/agent/events"}:
            run_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            run = state.run_status(run_id)
            if not run_id or run is None:
                raise RepoLensError("分析任务不存在。", 404)
            if path == "/api/agent/run":
                self._json(200, run)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            previous = None
            while run:
                encoded = json.dumps(run, ensure_ascii=False)
                if encoded != previous:
                    self.wfile.write(("data: " + encoded + "\n\n").encode("utf-8"))
                    previous = encoded
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                if run["status"] != "running":
                    break
                time.sleep(.5)
                run = state.run_status(run_id)
            return
        if self.command == "GET" and path == "/api/repository/pick/status":
            self._json(200, state.picker_status())
            return
        if self.command == "GET" and path == "/api/source":
            if not state.index:
                raise RepoLensError("请先导入代码仓库。")
            file_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            self._json(200, state.index.source(file_id))
            return
        if self.command != "POST":
            raise RepoLensError("接口不存在。", 404)
        body = self._body(MAX_UPLOAD_REQUEST_BYTES if path == "/api/repository/upload" else 16_384)
        if path == "/api/agent/cancel":
            self._json(200, state.cancel_agent(body.get("id", "")))
            return
        if path == "/api/agent/analyze":
            self._json(202, state.start_agent(body))
            return
        if path == "/api/repository/pick":
            state.begin_picker()
            self._json(202, {"pending": True})
            return
        if state.picker_pending():
            raise RepoLensError("目录选择正在进行，请先完成或取消选择。", 409)
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
                LOGGER.info("key.configured saved=%s", remember)
                self._json(200, {"key_configured": True, "key_saved": remember})
            elif path == "/api/key/delete":
                credentials.delete_key()
                state.api_key, state.saved, state.key_error = None, False, None
                LOGGER.info("key.deleted")
                self._json(200, {"key_configured": False, "key_saved": False})
            elif path in ("/api/repository", "/api/example"):
                state.phase = "正在建立代码索引……"
                if path == "/api/example":
                    root = PROJECT_ROOT / "samples" / "mini_project"
                else:
                    root = body.get("path")
                    if not isinstance(root, str) or not root.strip():
                        raise RepoLensError("请输入本地仓库目录。")
                candidate_index = scan_repository(root)
                state._set_index(candidate_index, persistent=True)
                LOGGER.info("repository.import name=%s files=%d edges=%d elapsed_ms=%d",
                            Path(root).name, len(candidate_index.documents), len(candidate_index.edges),
                            int((time.monotonic() - request_started) * 1000))
                self._json(200, {"repository": state.repository_summary(), "graph": state.graph})
            elif path == "/api/repository/upload":
                files = body.get("files")
                if not isinstance(files, list):
                    raise RepoLensError("请选择一个项目目录后再导入。")
                state.phase = "正在读取浏览器选择的项目……"
                state.import_uploaded(files)
                self._json(200, {"repository": state.repository_summary(), "graph": state.graph})
            elif path == "/api/architecture/save":
                if body.get("confirmed") is not True:
                    raise RepoLensError("请先确认保存当前已校验的架构图。")
                saved_path = state.save_architecture(model=body.get("model", DEFAULT_MODEL),
                                                     goal=body.get("goal", ""))
                self._json(200, {"saved": True, "path": saved_path,
                                 "repository": state.repository_summary(), "graph": state.graph})
            elif path == "/api/architecture/export":
                if body.get("confirmed") is not True or not state.index or not state.graph:
                    raise RepoLensError("请先确认保存当前架构图。")
                payload = encode_graph(state.index, state.graph, model=body.get("model", DEFAULT_MODEL),
                                       goal=body.get("goal", ""), usage=state.graph.get("usage"))
                self._send(200, payload)
            elif path == "/api/analyze":
                if body.get("confirmed") is not True:
                    raise RepoLensError("请先确认将当前索引摘要发送至 DeepSeek。")
                if state.index is None or not state.api_key:
                    raise RepoLensError("请先导入仓库并配置 DeepSeek API Key。")
                agent_config = get_config().agent
                client = state.client_factory(state.api_key, model=body.get("model", DEFAULT_MODEL),
                                              timeout=agent_config.request_timeout_seconds)
                model_name = getattr(client, "model", body.get("model", DEFAULT_MODEL))
                LOGGER.info("analysis.start model=%s goal_chars=%d files=%d",
                            model_name, len(body.get("goal", "")) if isinstance(body.get("goal", ""), str) else -1,
                            len(state.index.files))
                graph = analyze(state.index, body.get("goal", ""), client,
                                lambda message: setattr(state, "phase", message), settings=agent_config)
                state.graph = graph
                LOGGER.info("analysis.complete model=%s requests=%d tokens=%d elapsed_ms=%d",
                            model_name, graph["usage"]["requests"], graph["usage"]["total_tokens"],
                            int((time.monotonic() - request_started) * 1000))
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
            LOGGER.warning("route.failed status=%d error=%s", exc.status, error_text(exc))
            self._json(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOGGER.exception("route.unhandled")
            self._json(500, {"error": "本地处理失败，请检查目录后重试或重新启动。"})

    do_GET = _handle
    do_POST = _handle


def main():
    config = get_config()
    parser = argparse.ArgumentParser(description="RepoLens 本地 DeepSeek 代码理解服务")
    parser.add_argument("--repo", help="启动时导入的现有项目目录，路径请用双引号包围；默认使用内置示例")
    parser.add_argument("--port", type=int, default=None, help="本机端口；默认读取 config.toml")
    parser.add_argument("--log-dir", help="日志目录；默认写入仓库根目录 logs，也可用 REPOLENS_LOG_DIR")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()
    port = config.server.port if args.port is None else args.port
    if not 0 <= port <= 65535:
        parser.error("端口必须为 0–65535")
    configured_log_dir = args.log_dir or os.environ.get("REPOLENS_LOG_DIR") or config.server.log_dir
    log_path = configure_logging(configured_log_dir)
    for warning in config.warnings:
        LOGGER.warning("config.invalid detail=%s", warning)
    LOGGER.info("config.loaded path=%s log_dir=%s max_rounds=%d browse_files=%d analysis_files=%d",
                config.path.name, configured_log_dir, config.agent.max_rounds,
                config.limits.browse_files, config.limits.analysis_files)
    try:
        state = AppState(args.repo or PROJECT_ROOT / "samples" / "mini_project")
        with LocalServer(port, state) as server:
            print(f"RepoLens: {server.origin}", flush=True)
            print(f"日志文件: {log_path or '仅终端输出'}", flush=True)
            LOGGER.info("server.started origin=%s log=%s", server.origin, log_path or "console")
            print("仅本机访问；Ctrl+C 退出。API Key 请在页面输入，不要放入命令行。", flush=True)
            if args.open or config.server.open_browser:
                webbrowser.open(server.origin)
            server.serve_forever()
    except RepoLensError as exc:
        LOGGER.warning("server.start_failed error=%s", error_text(exc))
        message = str(exc)
        if args.repo and (args.repo.startswith("'") or args.repo.endswith("'")):
            message += (
                "\n检测到 --repo 路径包含单引号。Windows CMD 会将单引号视为路径字符，请改用双引号。"
                "\n请确认目录实际存在；也可省略 --repo，启动后在网页中选择项目。"
            )
        parser.exit(1, f"{message}\n")
    except KeyboardInterrupt:
        LOGGER.info("server.stopped reason=keyboard_interrupt")
        print("\nRepoLens 已停止。", flush=True)
