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
MAX_BROWSE_FILES = 400
MAX_BROWSE_BYTES = 4_000_000
BROWSE_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg",
                   ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".sql", ".rs", ".go", ".java",
                   ".c", ".h", ".cpp", ".hpp"}
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
class Symbol:
    id: str
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int

    def summary(self) -> dict:
        return vars(self).copy()


@dataclass(frozen=True)
class TextFile:
    id: str
    path: str
    fingerprint: str
    line_count: int


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
    definitions: tuple[Symbol, ...] = ()

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
    documents: dict[str, TextFile | SourceFile]

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
        file = self.documents.get(file_id)
        anchor = None
        if file is None:
            for candidate_file in self.files.values():
                anchor = next((symbol for symbol in candidate_file.definitions if symbol.id == file_id), None)
                if anchor:
                    file = candidate_file
                    break
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
                "end_line": file.line_count, "content": _decode(raw),
                "anchor": {"id": anchor.id if anchor else file.id,
                           "start_line": anchor.start_line if anchor else 1,
                           "end_line": anchor.end_line if anchor else file.line_count,
                           "name": anchor.qualified_name if anchor else file.path}}

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
    documents: dict[str, TextFile | SourceFile] = {}
    warnings: list[str] = []
    total_bytes = 0
    browse_bytes = 0
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
            suffix = Path(name).suffix.lower()
            if suffix not in BROWSE_SUFFIXES or name.startswith(".") or name.lower() in SKIP_FILES or any(
                word in name.lower() for word in ("secret", "credential", ".env", "private_key")
            ):
                continue
            if len(documents) >= MAX_BROWSE_FILES:
                warnings.append(f"文件浏览达到 {MAX_BROWSE_FILES} 个文件的上限，目录树不完整。")
                stopped = True
                break
            candidate = folder / name
            relative = candidate.relative_to(root).as_posix()
            try:
                candidate.resolve(strict=True).relative_to(root)
                if _linked(candidate):
                    continue
                size = candidate.stat().st_size
                if size > MAX_FILE_BYTES or browse_bytes + size > MAX_BROWSE_BYTES:
                    warnings.append(f"已跳过较大文件：{relative}")
                    continue
                raw = _read_bounded(candidate)
                if browse_bytes + len(raw) > MAX_BROWSE_BYTES:
                    warnings.append(f"已跳过较大文件：{relative}")
                    continue
                text = _decode(raw) if suffix == ".py" else raw.decode("utf-8-sig")
                if "\0" in text:
                    raise ValueError("binary file")
            except (OSError, UnicodeError, SyntaxError, ValueError, RuntimeError):
                warnings.append(f"无法解析，已跳过：{relative}")
                continue
            browse_bytes += len(raw)
            file_id = "file_" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
            documents[file_id] = TextFile(file_id, relative, hashlib.sha256(raw).hexdigest(), max(1, len(text.splitlines())))
            if suffix != ".py":
                continue
            if len(files) >= MAX_FILES or total_bytes + len(raw) > MAX_TOTAL_BYTES:
                warnings.append(f"分析最多 {MAX_FILES} 个 Python 文件、累计 1 MB；部分文件仅可浏览。")
                continue
            try:
                tree = ast.parse(text, filename=relative)
            except (SyntaxError, ValueError, RecursionError):
                warnings.append(f"无法解析，仅可浏览：{relative}")
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
            definitions = []

            def add_definition(item, prefix=""):
                qualified = prefix + item.name
                symbol_id = "symbol_" + hashlib.sha256((relative + ":" + qualified).encode("utf-8")).hexdigest()[:16]
                definitions.append(Symbol(symbol_id, item.name, qualified,
                                          "class" if isinstance(item, ast.ClassDef) else "function",
                                          min([item.lineno] + [decorator.lineno for decorator in item.decorator_list]),
                                          item.end_lineno or item.lineno))
                if isinstance(item, ast.ClassDef):
                    for member in item.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            add_definition(member, qualified + ".")

            for item in tree.body:
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_definition(item)
            files[file_id] = SourceFile(
                file_id, relative, hashlib.sha256(raw).hexdigest(),
                max(1, len(text.splitlines())), symbols, tuple(sorted(imports)),
                tuple(sorted(targets)),
                name in {"main.py", "__main__.py", "app.py"} or any(
                    isinstance(item, ast.Constant) and item.value == "__main__"
                    for item in ast.walk(tree)
                ), tuple(definitions),
            )
            documents[file_id] = files[file_id]
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
    snapshot_text = "|".join(f"{file.id}:{file.fingerprint}" for file in documents.values())
    return RepositoryIndex(root, files, edges, list(dict.fromkeys(warnings)),
                           hashlib.sha256(snapshot_text.encode()).hexdigest(), documents)
