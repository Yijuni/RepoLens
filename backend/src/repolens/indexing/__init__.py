"""Read-only, bounded Python AST indexing. Never imports target code."""

import ast
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import stat
import tokenize

from repolens.errors import RepoLensError

MAX_FILES = 40
MAX_FILE_BYTES = 100_000
MAX_TOTAL_BYTES = 1_000_000
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    "dist", "build", "target", ".idea", ".vs", ".repolens",
}
SKIP_FILES = {"secrets.py", "credentials.py"}


def _decode(raw: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


def _read_bounded(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    return raw


def _linked(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


@dataclass(frozen=True)
class SourceFile:
    id: str
    path: str
    fingerprint: str
    line_count: int
    symbols: tuple[str, ...]
    imports: tuple[str, ...]
    targets: tuple[str, ...]
    entry: bool

    def evidence(self) -> dict:
        return {
            "id": self.id, "path": self.path, "symbols": list(self.symbols[:15]),
            "imports": list(self.imports[:20]), "entry_candidate": self.entry,
        }


@dataclass
class RepositoryIndex:
    root: Path
    files: dict[str, SourceFile]
    edges: set[tuple[str, str]]
    warnings: list[str]
    snapshot: str

    def model_map(self) -> dict:
        return {
            "name": self.root.name,
            "files": [file.evidence() for file in self.files.values()],
            "import_edges": [
                {"source": source, "target": target}
                for source, target in sorted(self.edges)
            ],
            "incomplete": bool(self.warnings),
        }

    def source(self, file_id: str) -> dict:
        file = self.files.get(file_id)
        if file is None:
            raise RepoLensError("该文件不在当前索引中。", 404)
        candidate = self.root / file.path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
            if _linked(candidate) or not resolved.is_file():
                raise ValueError("linked file")
            raw = _read_bounded(resolved)
        except (OSError, ValueError):
            raise RepoLensError("源码已移动或不再位于仓库内，请重新导入。", 409) from None
        if hashlib.sha256(raw).hexdigest() != file.fingerprint:
            raise RepoLensError("源码自索引后已改变，请重新导入，避免引用旧行号。", 409)
        return {"id": file.id, "path": file.path, "start_line": 1,
                "end_line": file.line_count, "content": _decode(raw)}

    def assert_current(self) -> None:
        for file_id in self.files:
            self.source(file_id)


def scan_repository(path: str | Path) -> RepositoryIndex:
    try:
        root = Path(path).expanduser().resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        raise RepoLensError("仓库路径不存在或无法访问。") from None
    if not root.is_dir():
        raise RepoLensError("请选择仓库目录，而不是文件。")
    files: dict[str, SourceFile] = {}
    warnings: list[str] = []
    total_bytes = 0
    directory_count = 0
    stopped = False

    def traversal_error(_error: OSError) -> None:
        warnings.append("部分目录无法访问，索引可能不完整。")

    for directory, children, names in os.walk(root, followlinks=False, onerror=traversal_error):
        directory_count += 1
        if directory_count > 2000:
            warnings.append("目录数量超过试用版限制，请选择较小的子目录。")
            break
        folder = Path(directory)
        safe_children = []
        for name in sorted(children):
            try:
                child = folder / name
                if not name.startswith(".") and name not in SKIP_DIRS and not _linked(child):
                    child.resolve(strict=True).relative_to(root)
                    safe_children.append(name)
            except (OSError, ValueError, RuntimeError):
                warnings.append("已跳过不可访问或越界的目录。")
        children[:] = safe_children
        for name in sorted(names):
            if not name.endswith(".py") or name.startswith(".") or name.lower() in SKIP_FILES:
                continue
            if len(files) >= MAX_FILES:
                warnings.append(f"试用版最多索引 {MAX_FILES} 个 Python 文件，当前结果不完整。")
                stopped = True
                break
            candidate = folder / name
            relative = candidate.relative_to(root).as_posix()
            try:
                candidate.resolve(strict=True).relative_to(root)
                if _linked(candidate):
                    continue
                size = candidate.stat().st_size
                if size > MAX_FILE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
                    warnings.append(f"已跳过较大文件：{relative}")
                    continue
                raw = _read_bounded(candidate)
                if len(raw) > MAX_FILE_BYTES or total_bytes + len(raw) > MAX_TOTAL_BYTES:
                    warnings.append(f"已跳过较大文件：{relative}")
                    continue
                text = _decode(raw)
                tree = ast.parse(text, filename=relative)
            except (OSError, UnicodeError, SyntaxError, ValueError, RuntimeError):
                warnings.append(f"无法解析，已跳过：{relative}")
                continue
            total_bytes += len(raw)
            imports: set[str] = set()
            targets: set[str] = set()
            module = relative.removesuffix(".py").replace("/", ".")
            package = module.removesuffix(".__init__") if name == "__init__.py" else module.rpartition(".")[0]
            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    for alias in item.names:
                        imports.add(alias.name)
                        targets.add(alias.name)
                elif isinstance(item, ast.ImportFrom):
                    imports.add("." * item.level + (item.module or ""))
                    if item.level:
                        parts = package.split(".") if package else []
                        if item.level > len(parts):
                            continue
                        base = ".".join(parts[:len(parts) - item.level + 1])
                        base = ".".join(filter(None, [base, item.module]))
                    else:
                        base = item.module or ""
                    if base:
                        targets.add(base)
                    for alias in item.names:
                        targets.add(".".join(filter(None, [base, alias.name])))
            symbols = tuple(
                item.name for item in tree.body
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            )
            file_id = "file_" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
            files[file_id] = SourceFile(
                file_id, relative, hashlib.sha256(raw).hexdigest(),
                max(1, len(text.splitlines())), symbols, tuple(sorted(imports)),
                tuple(sorted(targets)),
                name in {"main.py", "__main__.py", "app.py"} or any(
                    isinstance(item, ast.Constant) and item.value == "__main__"
                    for item in ast.walk(tree)
                ),
            )
        if stopped:
            break
    if not files:
        raise RepoLensError("未找到可解析的 Python 文件；首版仅支持 Python 仓库。")
    aliases: dict[str, str] = {}
    for file in files.values():
        module = file.path.removesuffix(".py").replace("/", ".")
        aliases[module.removesuffix(".__init__")] = file.id
        if module.startswith("src."):
            aliases[module[4:].removesuffix(".__init__")] = file.id
    edges: set[tuple[str, str]] = set()
    for file in files.values():
        for target in file.targets:
            target_id = aliases.get(target)
            if target_id and target_id != file.id:
                edges.add((file.id, target_id))
    snapshot_text = "|".join(f"{file.id}:{file.fingerprint}" for file in files.values())
    return RepositoryIndex(root, files, edges, list(dict.fromkeys(warnings)),
                           hashlib.sha256(snapshot_text.encode()).hexdigest())
