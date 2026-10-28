import copy
import io
import json
import os
from pathlib import Path
import sys
import shutil
import threading
import unittest
from unittest.mock import patch
from urllib import error, request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from repolens.analysis import analyze
from repolens.agent import explore
from repolens.config import AgentConfig, load_config
from repolens.contracts import dependency_preview, schema_for, semantic_schema_for, validate_graph, validate_semantic_graph
from repolens.errors import RepoLensError
from repolens.indexing import scan_repository
from repolens.llm import Completion, DeepSeekClient, ENDPOINT
from repolens import platform as credentials
from repolens.platform import dialogs
from repolens.server import AppState, LocalServer
from repolens.tools import build_default_registry
from repolens.persistence import architecture_path, load_graph, save_graph


class ConfigTests(unittest.TestCase):
    def test_reads_limits_agent_and_relative_log_path(self):
        root = Path(__file__).resolve().parents[2] / ".repolens" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("config_" + uuid.uuid4().hex + ".toml")
        path.write_text(
            "[server]\nlog_dir = 'custom-logs'\nport = 1234\n"
            "[limits]\nbrowse_files = 900\n[agent]\nmax_rounds = 4\ntoken_budget = 512\nmax_tool_calls = 1\n",
            encoding="utf-8",
        )
        try:
            config = load_config(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(config.limits.browse_files, 900)
        self.assertEqual(config.agent.max_rounds, 4)
        self.assertEqual(config.server.port, 1234)
        self.assertTrue(config.server.log_dir.is_absolute())
        self.assertFalse(hasattr(config.agent, "token_budget"))
        self.assertFalse(hasattr(config.agent, "max_tool_calls"))
        self.assertTrue(any("agent.token_budget" in warning for warning in config.warnings))
        self.assertTrue(any("agent.max_tool_calls" in warning for warning in config.warnings))


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[2] / ".repolens" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        # Python's Windows mode=0700 temp directories can exclude restricted tokens.
        temporary = test_root / ("repolens_" + uuid.uuid4().hex)
        temporary.mkdir()
        self.addCleanup(shutil.rmtree, temporary)
        self.root = temporary / "中文 仓库"
        self.root.mkdir()
        (self.root / "main.py").write_text("from service import run\n\ndef main():\n    return run()\n", encoding="utf-8")
        (self.root / "service.py").write_text("DO_NOT_SEND_BODY = 'local-only'\n\ndef run():\n    return 42\n", encoding="utf-8")
        self.index = scan_repository(self.root)

    def draft(self):
        return {
            "project_type": "Python 测试项目", "summary": "入口调用服务模块。",
            "nodes": [{"id": file.id, "summary": "来自结构索引的模块说明", "group": "核心"} for file in self.index.files.values()],
            "edges": [{"source": source, "target": target, "type": "import", "confidence": .9} for source, target in sorted(self.index.edges)],
            "incomplete": False,
        }


class IndexTests(RepositoryCase):
    def test_imports_unicode_paths_and_no_source_in_map(self):
        self.assertEqual(len(self.index.files), 2)
        self.assertEqual(len(self.index.edges), 1)
        serialized = json.dumps(self.index.model_map())
        self.assertNotIn("DO_NOT_SEND_BODY", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertIn("run", serialized)

    def test_stable_ids_across_line_changes(self):
        old = set(self.index.files)
        file = self.root / "main.py"
        file.write_text("\n" + file.read_text(encoding="utf-8"), encoding="utf-8")
        new = scan_repository(self.root)
        self.assertEqual(old, set(new.files))
        self.assertNotEqual(self.index.snapshot, new.snapshot)

    def test_stale_source_is_rejected(self):
        file = next(iter(self.index.files.values()))
        (self.root / file.path).write_text("changed = True\n", encoding="utf-8")
        with self.assertRaisesRegex(RepoLensError, "改变"):
            self.index.source(file.id)

    def test_unknown_source_cannot_traverse(self):
        with self.assertRaises(RepoLensError):
            self.index.source("../../credentials.py")

    def test_skip_sensitive_generated_and_broken_files(self):
        (self.root / "secrets.py").write_text("secret = 'not-real'", encoding="utf-8")
        (self.root / "broken.py").write_text("def :", encoding="utf-8")
        generated = self.root / "node_modules"
        generated.mkdir()
        (generated / "generated.py").write_text("pass", encoding="utf-8")
        index = scan_repository(self.root)
        self.assertEqual(len(index.files), 2)
        self.assertTrue(index.warnings)

    def test_relative_import_and_src_layout(self):
        package = self.root / "src" / "demo"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "a.py").write_text("from . import b", encoding="utf-8")
        (package / "b.py").write_text("def useful(): return 1", encoding="utf-8")
        index = scan_repository(self.root)
        paths = {file.id: file.path for file in index.files.values()}
        self.assertIn(("src/demo/a.py", "src/demo/b.py"), {(paths[a], paths[b]) for a, b in index.edges})

    def test_static_language_adapters_build_local_edges(self):
        (self.root / "lib.h").write_text("class Widget {};\n", encoding="utf-8")
        (self.root / "main.cpp").write_text('#include "lib.h"\nint main() { return 0; }\n', encoding="utf-8")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "helper.go").write_text("package pkg\nfunc Help() {}\n", encoding="utf-8")
        (self.root / "main.go").write_text('package main\nimport "pkg/helper"\nfunc main() { pkg.Help() }\n', encoding="utf-8")
        (self.root / "helper.js").write_text("export function help() {}\n", encoding="utf-8")
        (self.root / "main.js").write_text("import { help } from './helper.js';\nhelp();\n", encoding="utf-8")
        index = scan_repository(self.root)
        languages = {file.path: file.language for file in index.files.values()}
        self.assertEqual(languages["main.cpp"], "C++")
        self.assertEqual(languages["main.go"], "Go")
        self.assertEqual(languages["main.js"], "JavaScript")
        paths = {file.id: file.path for file in index.files.values()}
        edge_paths = {(paths[source], paths[target]) for source, target in index.edges}
        self.assertIn(("main.cpp", "lib.h"), edge_paths)
        self.assertIn(("main.go", "pkg/helper.go"), edge_paths)
        self.assertIn(("main.js", "helper.js"), edge_paths)

    def test_file_limit_marks_incomplete(self):
        for number in range(205):
            (self.root / f"extra_{number}.py").write_text("pass", encoding="utf-8")
        index = scan_repository(self.root)
        self.assertEqual(len(index.files), 200)
        self.assertTrue(dependency_preview(index)["incomplete"])

    def test_does_not_execute_target(self):
        (self.root / "dangerous.py").write_text("raise RuntimeError('must never execute')", encoding="utf-8")
        self.assertEqual(len(scan_repository(self.root).files), 3)

    def test_symbols_have_real_ranges_and_documents_are_browsable(self):
        (self.root / "README.md").write_text("# local\n\n说明\n", encoding="utf-8")
        (self.root / "service.py").write_text(
            "class Service:\n    def run(self):\n        return 42\n\n\ndef helper():\n    return 1\n", encoding="utf-8"
        )
        index = scan_repository(self.root)
        service = next(file for file in index.files.values() if file.path == "service.py")
        names = {symbol.qualified_name: symbol for symbol in service.definitions}
        self.assertEqual((names["Service"].start_line, names["Service"].end_line), (1, 3))
        self.assertEqual((names["Service.run"].start_line, names["Service.run"].end_line), (2, 3))
        self.assertEqual((names["helper"].start_line, names["helper"].end_line), (6, 7))
        source = index.source(names["Service.run"].id)
        self.assertEqual(source["anchor"]["start_line"], 2)
        readme = next(document for document in index.documents.values() if document.path == "README.md")
        self.assertEqual(index.source(readme.id)["content"], "# local\r\n\r\n说明\r\n")


class ContractTests(RepositoryCase):
    def test_real_anchors_are_added_locally(self):
        graph = validate_graph(self.draft(), self.index)
        for node in graph["nodes"]:
            file = self.index.files[node["id"]]
            self.assertEqual(node["path"], file.path)
            self.assertEqual(node["end_line"], file.line_count)

    def test_hallucinated_and_duplicate_ids_are_rejected(self):
        for mutation in ("fake", "duplicate"):
            raw = self.draft()
            raw["nodes"][1]["id"] = "invented" if mutation == "fake" else raw["nodes"][0]["id"]
            with self.subTest(mutation=mutation), self.assertRaises(RepoLensError):
                validate_graph(raw, self.index)

    def test_fabricated_reverse_edge_is_rejected(self):
        raw = self.draft()
        raw["edges"][0]["source"], raw["edges"][0]["target"] = raw["edges"][0]["target"], raw["edges"][0]["source"]
        with self.assertRaises(RepoLensError):
            validate_graph(raw, self.index)

    def test_model_cannot_supply_anchors(self):
        raw = self.draft()
        raw["nodes"][0]["path"] = "invented.py"
        with self.assertRaises(RepoLensError):
            validate_graph(raw, self.index)

    def test_boolean_and_confidence_are_strict(self):
        for value in (True, -1, 2, float("nan"), "0.9"):
            raw = self.draft()
            raw["edges"][0]["confidence"] = value
            with self.subTest(value=value), self.assertRaises(RepoLensError):
                validate_graph(raw, self.index)
        raw = self.draft()
        raw["incomplete"] = "false"
        with self.assertRaises(RepoLensError):
            validate_graph(raw, self.index)

    def test_missing_edges_are_incomplete(self):
        raw = self.draft()
        raw["edges"] = []
        self.assertTrue(validate_graph(raw, self.index)["incomplete"])

    def test_schema_is_generated_from_models(self):
        schema = schema_for()
        self.assertEqual(set(schema["required"]), set(self.draft()))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["edges"]["items"]["properties"]["type"]["enum"], ["import"])

    def semantic_draft(self):
        files = list(self.index.files.values())
        return {
            "project_type": "Python 服务",
            "summary": "入口模块调用服务模块。",
            "modules": [
                {"module_key": "entry", "label": "请求入口", "kind": "service", "summary": "接收项目入口请求。",
                 "group": "服务入口", "anchor_ids": [files[0].id], "evidence_refs": ["ev_entry"], "confidence": .9,
                 "render_spec": {"shape": "service", "accent": "blue", "badge": "API", "lane": "服务入口", "emphasis": "high"}},
                {"module_key": "service", "label": "业务服务", "kind": "library", "summary": "提供入口依赖的业务函数。",
                 "group": "运行时核心", "anchor_ids": [files[1].id], "evidence_refs": ["ev_service"], "confidence": .8},
            ],
            "relations": [{"source": "entry", "target": "service", "type": "calls", "label": "调用服务",
                           "confidence": .7, "evidence_refs": ["ev_relation"]}],
            "incomplete": False, "incomplete_regions": [],
        }

    def records(self):
        registry = build_default_registry(self.index)
        records = {}
        for key, file in zip(("ev_entry", "ev_service"), self.index.files.values()):
            record = registry.invoke("read_range", {"file_id": file.id, "start_line": 1, "end_line": 1})["evidence"]
            records[key] = record | {"evidence_id": key}
        records["ev_relation"] = records["ev_entry"] | {"evidence_id": "ev_relation", "anchors": records["ev_entry"]["anchors"] + records["ev_service"]["anchors"]}
        return records

    def test_semantic_graph_derives_ids_and_real_anchors(self):
        graph = validate_semantic_graph(self.semantic_draft(), self.index,
                                        self.records())
        self.assertEqual(graph["schema_version"], "0.2")
        self.assertTrue(graph["nodes"][0]["id"].startswith("module_"))
        self.assertEqual(graph["nodes"][0]["anchors"][0]["path"], "main.py")
        self.assertEqual(graph["edges"][0]["type"], "calls")
        self.assertEqual(graph["nodes"][0]["render_spec"]["shape"], "service")

    def test_semantic_graph_rejects_fake_anchor_or_evidence(self):
        raw = self.semantic_draft()
        raw["modules"][0]["anchor_ids"] = ["symbol_invented"]
        with self.assertRaises(RepoLensError):
            validate_semantic_graph(raw, self.index, self.records())
        raw = self.semantic_draft()
        raw["modules"][0]["evidence_refs"] = ["ev_missing"]
        with self.assertRaises(RepoLensError):
            validate_semantic_graph(raw, self.index, self.records())

    def test_semantic_schema_is_strict_and_has_relation_types(self):
        schema = semantic_schema_for()
        self.assertEqual(schema["required"], ["project_type", "summary", "modules", "relations", "incomplete", "incomplete_regions"])
        self.assertIn("dispatches", schema["properties"]["relations"]["items"]["properties"]["type"]["enum"])


class ToolTests(RepositoryCase):
    def test_default_registry_returns_bounded_evidence(self):
        registry = build_default_registry(self.index)
        self.assertIn("read_symbol", registry.names())
        listing = registry.invoke("list_files", {"limit": 1})
        self.assertEqual(listing["evidence"]["snapshot"], self.index.snapshot)
        self.assertEqual(len(listing["result"]["items"]), 1)
        symbol = self.index.files[next(iter(self.index.files))].definitions
        result = registry.invoke("read_symbol", {"symbol_id": symbol[0].id})
        self.assertEqual(result["result"]["symbol_id"], symbol[0].id)
        self.assertNotIn(str(self.root), json.dumps(result, ensure_ascii=False))

    def test_tools_reject_unknown_ids_and_oversized_reads(self):
        registry = build_default_registry(self.index)
        with self.assertRaises(RepoLensError):
            registry.invoke("read_range", {"file_id": "file_fake", "start_line": 1, "end_line": 2})
        file_id = next(iter(self.index.documents))
        with self.assertRaises(RepoLensError):
            registry.invoke("read_range", {"file_id": file_id, "start_line": 1, "end_line": 201})


class PersistenceTests(RepositoryCase):
    def _graph(self):
        registry = build_default_registry(self.index)
        file = next(iter(self.index.files.values()))
        evidence = registry.invoke("read_range", {"file_id": file.id, "start_line": 1, "end_line": 1})["evidence"]["evidence_id"]
        return validate_semantic_graph({
            "project_type": "Python", "summary": "持久化测试。",
            "modules": [{"module_key": "entry", "label": "入口", "kind": "module", "summary": "入口模块。", "group": "核心",
                         "anchor_ids": [file.id], "evidence_refs": [evidence], "confidence": .8}],
            "relations": [], "incomplete": False, "incomplete_regions": [],
        }, self.index, registry.evidence) | {"usage": {"requests": 1, "tool_calls": 1, "total_tokens": 2}, "mode": "deepseek-agent"}

    def test_explicit_save_and_load_roundtrip(self):
        graph = self._graph()
        try:
            path = save_graph(self.index, graph, model="deepseek-chat", goal="理解入口", usage=graph["usage"])
            self.assertEqual(path, architecture_path(self.root))
            loaded, issue = load_graph(self.index)
            self.assertIsNone(issue)
            self.assertEqual(loaded["schema_version"], "0.2")
            self.assertEqual(loaded["mode"], "saved-agent")
            self.assertNotIn("DO_NOT_SEND_BODY", path.read_text(encoding="utf-8"))
            self.assertNotIn('"content":', path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_stale_saved_graph_is_not_restored(self):
        graph = self._graph()
        save_graph(self.index, graph)
        (self.root / "main.py").write_text("changed = True\n", encoding="utf-8")
        changed = scan_repository(self.root)
        loaded, issue = load_graph(changed)
        self.assertIsNone(loaded)
        self.assertIn("旧索引", issue)
        shutil.rmtree(architecture_path(self.root).parent, ignore_errors=True)


class StubClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def complete(self, messages, max_tokens):
        self.requests.append(copy.deepcopy(messages))
        return Completion(next(self.outputs), 100)


class AgentStubClient:
    def __init__(self, final_output):
        self.final_output = final_output
        self.requests = []
        self.calls = 0

    def complete(self, messages, max_tokens, tools=None):
        self.requests.append(copy.deepcopy(messages))
        self.calls += 1
        if self.calls == 1:
            name, args = "submit_plan", {"steps": ["读取入口并提交图"]}
        elif self.calls == 2:
            name, args = "read_range", {"file_id": json.loads(self.final_output)["modules"][0]["anchor_ids"][0], "start_line": 1, "end_line": 1}
        else:
            name, args = "submit_graph", json.loads(self.final_output)
        return Completion("", 100, tool_calls=({"id": "call_" + str(self.calls), "name": name, "arguments": json.dumps(args)},))


class AgentTests(RepositoryCase):
    def test_tool_exploration_submits_valid_semantic_graph(self):
        from repolens.tools import build_default_registry
        registry = build_default_registry(self.index)
        files = list(self.index.files.values())
        evidence = registry.invoke("read_range", {"file_id": files[0].id, "start_line": 1, "end_line": 1})["evidence"]["evidence_id"]
        raw = {
            "project_type": "Python 服务", "summary": "通过入口调用服务。",
            "modules": [
                {"module_key": "entry", "label": "入口", "kind": "service", "summary": "服务入口。", "group": "入口",
                 "anchor_ids": [files[0].id], "evidence_refs": [evidence], "confidence": .8},
            ],
            "relations": [], "incomplete": True, "incomplete_regions": ["仅探索入口候选"],
        }
        client = AgentStubClient(json.dumps(raw))
        result = explore(self.index, "理解入口", client, registry=registry)
        self.assertEqual(result["schema_version"], "0.2")
        self.assertEqual(result["usage"]["tool_calls"], 1)
        self.assertEqual(len(client.requests), 3)
        self.assertTrue(any(item["role"] == "tool" for item in client.requests[-1]))

    def test_agent_rejects_graph_without_obtained_evidence(self):
        files = list(self.index.files.values())
        raw = {
            "project_type": "Python", "summary": "不应通过。",
            "modules": [{"module_key": "entry", "label": "入口", "kind": "module", "summary": "入口。", "group": "入口",
                         "anchor_ids": [files[0].id], "evidence_refs": ["ev_not_seen"], "confidence": .5}],
            "relations": [], "incomplete": False, "incomplete_regions": [],
        }
        client = AgentStubClient(json.dumps(raw))
        with self.assertRaisesRegex(RepoLensError, "未能"):
            explore(self.index, "理解入口", client)


class AnalysisTests(RepositoryCase):
    def test_minimal_pipeline_and_private_source(self):
        client = StubClient([json.dumps(self.draft())])
        result = analyze(self.index, "理解入口", client)
        self.assertEqual(result["mode"], "deepseek")
        self.assertEqual(result["usage"]["requests"], 1)
        self.assertNotIn("DO_NOT_SEND_BODY", json.dumps(client.requests))

    def test_one_bounded_repair(self):
        client = StubClient(["not json", json.dumps(self.draft())])
        self.assertEqual(analyze(self.index, "", client, settings=AgentConfig(max_rounds=2))["usage"]["requests"], 2)
        self.assertIn("校验错误", client.requests[-1][-1]["content"])

    def test_repair_exhaustion(self):
        client = StubClient(["{}", "{}"])
        with self.assertRaisesRegex(RepoLensError, "两次"):
            analyze(self.index, "", client, settings=AgentConfig(max_rounds=2))
        self.assertEqual(len(client.requests), 2)

    def test_stale_index_fails_before_network(self):
        (self.root / "main.py").write_text("pass", encoding="utf-8")
        client = StubClient([])
        with self.assertRaises(RepoLensError):
            analyze(self.index, "", client, settings=AgentConfig(max_rounds=2))
        self.assertEqual(client.requests, [])


class ClientTests(unittest.TestCase):
    def test_official_request_and_usage(self):
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {"total_tokens": 123}}
        opener = unittest.mock.Mock()
        opener.open.return_value = io.BytesIO(json.dumps(response).encode())
        with patch("repolens.llm.request.build_opener", return_value=opener):
            completion = DeepSeekClient("synthetic-test-key").complete([{"role": "user", "content": "JSON"}], 512)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, ENDPOINT)
        payload = json.loads(req.data)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(completion.tokens, 123)

    def test_upstream_body_and_key_are_never_exposed(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = error.HTTPError(ENDPOINT, 401, "private-upstream", {}, io.BytesIO(b"synthetic-test-key"))
        with patch("repolens.llm.request.build_opener", return_value=opener):
            with self.assertRaises(RepoLensError) as raised:
                DeepSeekClient("synthetic-test-key").complete([], 512)
        self.assertNotIn("synthetic", str(raised.exception))
        self.assertNotIn("private-upstream", str(raised.exception))

    def test_tool_call_response_is_parsed(self):
        response = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}
        ]}}], "usage": {"total_tokens": 87}}
        opener = unittest.mock.Mock()
        opener.open.return_value = io.BytesIO(json.dumps(response).encode())
        with patch("repolens.llm.request.build_opener", return_value=opener):
            completion = DeepSeekClient("synthetic-test-key").complete([], 512,
                tools=[{"name": "list_files", "description": "list", "parameters": {"type": "object"}}])
        self.assertEqual(completion.tool_calls[0]["name"], "list_files")
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertIn("tools", payload)
        self.assertNotIn("response_format", payload)


class ServerTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.client = StubClient([json.dumps(self.draft())])
        self.state = AppState(self.root, client_factory=lambda *args, **kwargs: self.client, restore_key=False)
        self.server = LocalServer(0, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.opener = request.build_opener(request.HTTPCookieProcessor())
        with self.opener.open(self.server.origin) as response:
            self.assertIn("HttpOnly", response.headers["Set-Cookie"])

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def call(self, path, body=None, headers=None):
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = request.Request(self.server.origin + path, data=data, headers=headers)
        with self.opener.open(req) as response:
            return json.load(response)

    def test_preview_source_and_fake_model_end_to_end(self):
        state = self.call("/api/state")
        self.assertEqual(len(state["graph"]["nodes"]), 2)
        file_id = state["graph"]["nodes"][0]["id"]
        self.assertEqual(self.call("/api/source?id=" + file_id)["start_line"], 1)
        self.state.api_key = "synthetic-test-key"
        result = self.call("/api/analyze", {"confirmed": True, "goal": "理解入口"})
        self.assertEqual(result["graph"]["mode"], "deepseek")
        self.assertNotIn("synthetic-test-key", json.dumps(self.call("/api/state")))

    def test_rejects_missing_cookie(self):
        with self.assertRaises(error.HTTPError) as raised:
            request.urlopen(self.server.origin + "/api/state")
        self.assertEqual(raised.exception.code, 403)

    def test_rejects_cross_origin(self):
        with self.assertRaises(error.HTTPError) as raised:
            self.call("/api/state", headers={"Origin": "https://example.com"})
        self.assertEqual(raised.exception.code, 403)

    def test_requires_upload_consent(self):
        self.state.api_key = "synthetic-test-key"
        with self.assertRaises(error.HTTPError) as raised:
            self.call("/api/analyze", {"confirmed": False})
        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(self.client.requests, [])

    def test_invalid_import_keeps_previous_graph(self):
        old = self.state.graph
        with self.assertRaises(error.HTTPError):
            self.call("/api/repository", {"path": str(self.root / "missing")})
        self.assertIs(self.state.graph, old)

    def test_directory_picker_import_and_cancel(self):
        self.state.directory_picker = lambda: str(self.root)
        self.assertEqual(self.call("/api/repository/pick", {}), {"pending": True})
        for _ in range(20):
            with self.opener.open(request.Request(self.server.origin + "/api/repository/pick/status")) as response:
                result = json.load(response)
            if result.get("repository", {}).get("path") == str(self.root.resolve()):
                break
            import time
            time.sleep(0.01)
        else:
            self.fail("directory picker result was not committed")
        self.state.directory_picker = lambda: None
        self.assertEqual(self.call("/api/repository/pick", {}), {"pending": True})
        for _ in range(20):
            with self.opener.open(request.Request(self.server.origin + "/api/repository/pick/status")) as response:
                status = json.load(response)
            if status.get("cancelled"):
                break
            import time
            time.sleep(0.01)
        else:
            self.fail("directory picker cancellation was not reported")

    def test_browser_directory_upload_creates_local_snapshot(self):
        result = self.call("/api/repository/upload", {"files": [
            {"path": "upload_demo/main.py", "content": "from helper import run\nrun()\n"},
            {"path": "upload_demo/helper.py", "content": "def run():\n    return 1\n"},
            {"path": "upload_demo/README.md", "content": "# uploaded\n"},
        ]})
        self.assertEqual(result["repository"]["name"], "upload_demo")
        self.assertEqual(len(result["repository"]["files"]), 3)
        self.assertEqual(len(result["graph"]["edges"]), 1)
        source_id = next(item["id"] for item in result["repository"]["files"] if item["path"] == "helper.py")
        self.assertIn("def run", self.call("/api/source?id=" + source_id)["content"])


@unittest.skipUnless(os.name == "nt", "Windows credential API")
class CredentialTests(unittest.TestCase):
    def test_synthetic_credential_roundtrip_and_cleanup(self):
        with patch.object(credentials, "TARGET", "RepoLens/Test/" + uuid.uuid4().hex):
            try:
                credentials.save_key("synthetic-test-key-not-a-real-token")
                self.assertEqual(credentials.load_key(), "synthetic-test-key-not-a-real-token")
            finally:
                credentials.delete_key()
            self.assertIsNone(credentials.load_key())


@unittest.skipUnless(os.name == "nt", "Windows directory picker")
class DirectoryDialogTests(unittest.TestCase):
    def test_cancel_does_not_open_a_second_dialog(self):
        with patch.object(dialogs.shutil, "which", return_value="powershell.exe"), \
             patch.object(dialogs, "_python_directory", return_value=None), \
             patch.object(dialogs, "_powershell_directory", return_value=None), \
             patch.object(dialogs.ctypes, "WinDLL") as fallback:
            self.assertIsNone(dialogs.choose_directory())
            fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
