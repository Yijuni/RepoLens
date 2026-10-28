"""Behavior checks for semantic evidence, Agent jobs and persistence boundaries."""
import copy
import json
import threading
import time
from unittest.mock import patch

import test_core as fixtures
from repolens.agent import explore
from repolens.config import AgentConfig
from repolens.contracts import validate_semantic_graph
from repolens.errors import RepoLensError
from repolens.indexing import scan_repository
from repolens.llm import Completion
from repolens.persistence import decode_graph, encode_graph, save_graph
from repolens.tools import build_default_registry


class EvidenceTests(fixtures.RepositoryCase):
    semantic_draft = fixtures.ContractTests.semantic_draft
    records = fixtures.ContractTests.records
    def test_listing_does_not_prove_module(self):
        registry = build_default_registry(self.index)
        result = registry.invoke("list_files", {})
        raw = self.semantic_draft()
        for node in raw["modules"]:
            node["evidence_refs"] = [result["evidence"]["evidence_id"]]
        with self.assertRaisesRegex(RepoLensError, "未覆盖"):
            validate_semantic_graph(raw, self.index, registry.evidence)

    def test_relation_requires_both_ends_and_inference_cap(self):
        raw = self.semantic_draft()
        raw["relations"][0]["evidence_refs"] = ["ev_entry"]
        with self.assertRaisesRegex(RepoLensError, "两端"):
            validate_semantic_graph(raw, self.index, self.records())
        raw = self.semantic_draft()
        raw["relations"][0]["confidence"] = .99
        with self.assertRaisesRegex(RepoLensError, "0.85"):
            validate_semantic_graph(raw, self.index, self.records())

    def test_stable_module_id_across_unrelated_change(self):
        graph = validate_semantic_graph(self.semantic_draft(), self.index, self.records())
        (self.root / "README.md").write_text("new documentation", encoding="utf-8")
        self.index = scan_repository(self.root)
        updated = validate_semantic_graph(self.semantic_draft(), self.index, self.records())
        self.assertEqual(graph["nodes"][0]["id"], updated["nodes"][0]["id"])
        self.assertNotEqual(graph["snapshot"], updated["snapshot"])

    def test_persistence_rechecks_provenance_and_keeps_old_file(self):
        graph = validate_semantic_graph(self.semantic_draft(), self.index, self.records())
        saved = save_graph(self.index, graph)
        original = saved.read_bytes()
        with patch("repolens.persistence.os.replace", side_effect=PermissionError):
            with self.assertRaises(RepoLensError):
                save_graph(self.index, graph)
        self.assertEqual(saved.read_bytes(), original)
        bad = json.loads(original)
        next(iter(bad["graph"]["evidence"].values()))["anchors"][0]["fingerprint"] = "fake"
        with self.assertRaises(RepoLensError):
            decode_graph(self.index, json.dumps(bad).encode())
        (self.root / "main.py").write_text("pass", encoding="utf-8")
        with self.assertRaises(RepoLensError):
            save_graph(self.index, graph)
        self.assertEqual(saved.read_bytes(), original)

    def test_saved_file_drops_unrecognized_evidence_fields(self):
        records = self.records()
        records["ev_entry"]["private_source"] = "DO_NOT_PERSIST"
        graph = validate_semantic_graph(self.semantic_draft(), self.index, records)
        self.assertNotIn("DO_NOT_PERSIST", encode_graph(self.index, graph).decode())

    def test_malformed_saved_nodes_and_symlink_are_rejected(self):
        graph = validate_semantic_graph(self.semantic_draft(), self.index, self.records())
        payload = json.loads(encode_graph(self.index, graph))
        payload["graph"]["nodes"] = ["invalid"]
        with self.assertRaises(RepoLensError):
            decode_graph(self.index, json.dumps(payload).encode())
        outside = self.root.parent / "outside"
        outside.mkdir()
        try:
            (self.root / ".repolens").symlink_to(outside, target_is_directory=True)
        except OSError:
            return  # Symlink creation requires an OS privilege on Windows.
        with self.assertRaisesRegex(RepoLensError, "符号链接"):
            save_graph(self.index, graph)
        self.assertFalse(list(outside.iterdir()))


class ToolBoundaryTests(fixtures.RepositoryCase):
    def test_schema_validation_and_redaction(self):
        file = next(iter(self.index.files.values()))
        (self.root / file.path).write_text('api_key = "synthetic-private-value"\nprint(1)\n', encoding="utf-8")
        self.index = scan_repository(self.root)
        registry = build_default_registry(self.index)
        with self.assertRaisesRegex(RepoLensError, "未知字段"):
            registry.invoke("entry_candidates", {"command": "echo"})
        result = registry.invoke("read_range", {"file_id": file.id, "start_line": 1, "end_line": 2})
        self.assertNotIn("synthetic-private-value", json.dumps(result))
        self.assertIn("REDACTED", result["result"]["content"])

    def test_empty_and_long_symbols_are_readable(self):
        (self.root / "empty.py").write_text("", encoding="utf-8")
        (self.root / "long.py").write_text("def long_function():\n" + "    pass\n" * 250, encoding="utf-8")
        index = scan_repository(self.root)
        registry = build_default_registry(index)
        empty = next(f for f in index.documents.values() if f.path == "empty.py")
        self.assertEqual(registry.invoke("read_range", {"file_id": empty.id, "start_line": 1, "end_line": 1})["result"]["content"], "")
        long = next(f for f in index.files.values() if f.path == "long.py")
        result = registry.invoke("read_symbol", {"symbol_id": long.definitions[0].id})
        self.assertTrue(result["result"]["truncated"])
        self.assertEqual(result["result"]["next_line"], 201)

    def test_unsupported_language_uses_text_tools(self):
        (self.root / "main.py").unlink()
        (self.root / "service.py").unlink()
        (self.root / "query.sql").write_text("SELECT 1;", encoding="utf-8")
        index = scan_repository(self.root)
        self.assertFalse(index.files)
        self.assertEqual(len(build_default_registry(index).invoke("list_files")["result"]["items"]), 1)


def workflow_client(index, raw, *, broken_tool=False, bad_submit=False):
    raw = copy.deepcopy(raw)
    files = list(index.files.values())
    registry = build_default_registry(index)
    reads = [("read_range", {"file_id": f.id, "start_line": 1, "end_line": 1}) for f in files]
    refs = [registry.invoke(name, args)["evidence"]["evidence_id"] for name, args in reads]
    for i, node in enumerate(raw["modules"]):
        node["evidence_refs"] = [refs[i]]
    for edge in raw["relations"]:
        edge["evidence_refs"] = refs
    batches = [[("submit_plan", {"steps": ["查阅入口与服务"]})]]
    if broken_tool:
        batches.append([("read_range", {"file_id": "fake", "start_line": 1, "end_line": 1})])
    batches.append(reads)
    if bad_submit:
        invalid = copy.deepcopy(raw)
        invalid["modules"][0]["evidence_refs"] = ["ev_fake"]
        batches.append([("submit_graph", invalid)])
    batches.append([("submit_graph", raw)])
    class Client:
        def __init__(self): self.requests = []
        def complete(self, messages, max_tokens, tools=None):
            self.requests.append(copy.deepcopy(messages))
            batch = batches.pop(0)
            return Completion("", 10, tool_calls=tuple({"id": f"call_{len(self.requests)}_{i}", "name": name,
                              "arguments": json.dumps(args)} for i, (name, args) in enumerate(batch)))
    return Client()


class WorkflowTests(fixtures.RepositoryCase):
    semantic_draft = fixtures.ContractTests.semantic_draft
    def test_tool_error_and_submission_repair_are_recoverable(self):
        client = workflow_client(self.index, self.semantic_draft(), broken_tool=True, bad_submit=True)
        events = []
        graph = explore(self.index, "理解主流程", client, on_event=events.append)
        self.assertEqual(graph["usage"]["requests"], 5)
        self.assertEqual(graph["usage"]["tool_calls"], 3)
        self.assertEqual({e["phase"] for e in events}, {"INDEXING", "PLANNING", "EXPLORING", "SUBMITTING", "RENDERING", "IDLE"})
        results = [json.loads(m["content"]) for m in client.requests[-1] if m["role"] == "tool"]
        self.assertTrue(any(r.get("recoverable") for r in results))

    def test_cancel_and_budget_stop_before_network(self):
        client = workflow_client(self.index, self.semantic_draft())
        with self.assertRaisesRegex(RepoLensError, "取消"):
            explore(self.index, "", client, cancelled=lambda: True)
        self.assertEqual(client.requests, [])
        with self.assertRaisesRegex(RepoLensError, "预算"):
            explore(self.index, "", client, settings=AgentConfig(token_budget=512))
        self.assertEqual(client.requests, [])


class JobTests(fixtures.RepositoryCase):
    stop = fixtures.ServerTests.stop
    call = fixtures.ServerTests.call

    def setUp(self):
        super().setUp()
        self.state = fixtures.AppState(self.root, restore_key=False)
        self.server = fixtures.LocalServer(0, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.opener = fixtures.request.build_opener(fixtures.request.HTTPCookieProcessor())
        self.opener.open(self.server.origin).close()
    def wait_job(self, run_id):
        for _ in range(100):
            run = self.call("/api/agent/run?id=" + run_id)
            if run["status"] != "running": return run
            time.sleep(.01)
        self.fail("Job did not finish")

    def test_explicit_job_sse_save_and_restore(self):
        raw = fixtures.ContractTests.semantic_draft(self)
        client = workflow_client(self.index, raw)
        self.state.client_factory = lambda *a, **kw: client
        self.state.api_key = "synthetic-test-key"
        self.assertEqual(client.requests, [])
        with self.assertRaises(Exception):
            self.call("/api/agent/analyze", {"confirmed": False})
        result = self.call("/api/agent/analyze", {"confirmed": True})
        run = self.wait_job(result["id"])
        self.assertEqual(run["status"], "complete", run)
        with self.opener.open(self.server.origin + "/api/agent/events?id=" + result["id"]) as response:
            self.assertIn('"status": "complete"', response.read().decode())
        self.call("/api/architecture/save", {"confirmed": True})
        count = len(client.requests)
        result = self.call("/api/repository", {"path": str(self.root)})
        self.assertEqual(result["graph"]["mode"], "saved-agent")
        self.assertEqual(len(client.requests), count)

    def test_cancelled_job_retains_old_graph(self):
        entered, release = threading.Event(), threading.Event()
        class Blocking:
            def complete(self, *a, **kw):
                entered.set(); release.wait(3); return Completion("", 1)
        self.state.client_factory = lambda *a, **kw: Blocking()
        self.state.api_key = "synthetic-test-key"
        old = self.state.graph
        result = self.call("/api/agent/analyze", {"confirmed": True})
        self.assertTrue(entered.wait(1))
        self.call("/api/agent/cancel", {"id": result["id"]})
        release.set()
        self.assertEqual(self.wait_job(result["id"])["status"], "cancelled")
        self.assertIs(self.state.graph, old)

    def test_bad_upload_retains_previous_uploaded_source(self):
        result = self.call("/api/repository/upload", {"files": [{"path": "demo/main.py", "content": "pass"}]})
        file_id = result["repository"]["files"][0]["id"]
        with self.assertRaises(Exception):
            self.call("/api/repository/upload", {"files": [{"path": "demo/../escape.py", "content": "pass"}]})
        self.assertEqual(self.call("/api/source?id=" + file_id)["content"], "pass")

    def test_uploaded_saved_graph_restores_without_model(self):
        client = workflow_client(self.index, fixtures.ContractTests.semantic_draft(self))
        self.state.client_factory = lambda *a, **kw: client
        self.state.api_key = "synthetic-test-key"
        run = self.call("/api/agent/analyze", {"confirmed": True})
        self.assertEqual(self.wait_job(run["id"])["status"], "complete")
        payload = self.call("/api/architecture/export", {"confirmed": True})
        count = len(client.requests)
        files = [{"path": "browser/" + file.path, "content": (self.root / file.path).read_bytes().decode("utf-8")}
                 for file in self.index.documents.values()]
        files.append({"path": "browser/.repolens/architecture.json", "content": json.dumps(payload)})
        result = self.call("/api/repository/upload", {"files": files})
        self.assertEqual(result["graph"]["mode"], "saved-agent")
        self.assertEqual(len(client.requests), count)
