"""Loopback fixture for browser checks. Never calls a model or uses real keys."""
import argparse
from pathlib import Path
import shutil
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from repolens.server import AppState, LocalServer, PROJECT_ROOT
from repolens.errors import RepoLensError
from test_workflow import workflow_client
import test_core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    parent = PROJECT_ROOT / ".repolens" / "browser-fixtures"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / uuid.uuid4().hex
    shutil.copytree(PROJECT_ROOT / "samples" / "mini_project", root,
                    ignore=shutil.ignore_patterns(".repolens", "__pycache__"))
    try:
        def factory(*_args, **_kwargs):
            holder = test_core.RepositoryCase()
            holder.index = state.index
            raw = test_core.ContractTests.semantic_draft(holder)
            symbol = next(iter(state.index.files.values())).definitions
            if symbol:
                raw["modules"][0]["anchor_ids"].append(symbol[0].id)
            raw["modules"][1]["render_spec"] = {"shape": "worker", "accent": "purple", "badge": "Worker"}
            raw["relations"][0]["type"] = "dispatches"
            raw["relations"][0]["label"] = "分发任务（模拟）"
            raw["summary"] = "浏览器测试模拟图，不代表真实模型理解。"
            client = workflow_client(state.index, raw, tokens=200_000)
            class Simulated:
                def complete(self, messages, max_tokens, tools=None):
                    time.sleep(.3)
                    if _kwargs.get("model") == "fixture-failure" and client.requests:
                        raise RepoLensError("模拟连接中断，请重试。", 502)
                    return client.complete(messages, max_tokens, tools=tools)
            return Simulated()
        state = AppState(root, client_factory=factory, restore_key=False)
        state.api_key = "synthetic-browser-test-key"
        with LocalServer(args.port, state) as server:
            print(server.origin, flush=True)
            server.serve_forever()
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
