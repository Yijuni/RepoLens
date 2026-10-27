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
from repolens.contracts import dependency_preview, schema_for, validate_graph
from repolens.errors import RepoLensError
from repolens.indexing import scan_repository
from repolens.llm import Completion, DeepSeekClient, ENDPOINT
from repolens import platform as credentials
from repolens.platform import dialogs
from repolens.server import AppState, LocalServer


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

    def test_file_limit_marks_incomplete(self):
        for number in range(45):
            (self.root / f"extra_{number}.py").write_text("pass", encoding="utf-8")
        index = scan_repository(self.root)
        self.assertEqual(len(index.files), 40)
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


class StubClient:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def complete(self, messages, max_tokens):
        self.requests.append(copy.deepcopy(messages))
        return Completion(next(self.outputs), 100)


class AnalysisTests(RepositoryCase):
    def test_minimal_pipeline_and_private_source(self):
        client = StubClient([json.dumps(self.draft())])
        result = analyze(self.index, "理解入口", client)
        self.assertEqual(result["mode"], "deepseek")
        self.assertEqual(result["usage"]["requests"], 1)
        self.assertNotIn("DO_NOT_SEND_BODY", json.dumps(client.requests))

    def test_one_bounded_repair(self):
        client = StubClient(["not json", json.dumps(self.draft())])
        self.assertEqual(analyze(self.index, "", client)["usage"]["requests"], 2)
        self.assertIn("校验错误", client.requests[-1][-1]["content"])

    def test_repair_exhaustion(self):
        client = StubClient(["{}", "{}"])
        with self.assertRaisesRegex(RepoLensError, "两次"):
            analyze(self.index, "", client)
        self.assertEqual(len(client.requests), 2)

    def test_stale_index_fails_before_network(self):
        (self.root / "main.py").write_text("pass", encoding="utf-8")
        client = StubClient([])
        with self.assertRaises(RepoLensError):
            analyze(self.index, "", client)
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
             patch.object(dialogs, "_powershell_directory", return_value=None), \
             patch.object(dialogs.ctypes, "WinDLL") as fallback:
            self.assertIsNone(dialogs.choose_directory())
            fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
