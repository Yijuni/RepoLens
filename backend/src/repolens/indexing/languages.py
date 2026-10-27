"""只读的多语言轻量静态解析器；不导入、编译或执行目标仓库。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ParsedSource:
    language: str
    imports: tuple[str, ...]
    targets: tuple[str, ...]
    symbols: tuple[str, ...]
    definitions: tuple[tuple[str, str, str, int, int], ...]
    entry: bool


LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".c": "C", ".h": "C/C++", ".cc": "C++",
    ".cpp": "C++", ".cxx": "C++", ".hh": "C/C++", ".hpp": "C++", ".m": "C/Objective-C", ".mm": "C++/Objective-C", ".cs": "C#", ".kt": "Kotlin",
    ".kts": "Kotlin", ".swift": "Swift", ".php": "PHP", ".rb": "Ruby", ".sh": "Shell",
}
CODE_SUFFIXES = set(LANGUAGES)


def _definition(relative: str, name: str, qualified: str, kind: str, line: int, end_line: int | None = None):
    return (name, qualified, kind, line, end_line or line)


def _python(relative: str, text: str) -> ParsedSource:
    tree = ast.parse(text, filename=relative)
    imports: set[str] = set()
    targets: set[str] = set()
    module = relative.removesuffix(".py").replace("/", ".")
    basename = relative.rsplit("/", 1)[-1]
    package = module.removesuffix(".__init__") if basename == "__init__.py" else module.rpartition(".")[0]
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
    definitions = []

    def add_definition(item, prefix=""):
        qualified = prefix + item.name
        definitions.append(_definition(
            relative, item.name, qualified,
            "class" if isinstance(item, ast.ClassDef) else "function",
            min([item.lineno] + [decorator.lineno for decorator in item.decorator_list]),
            item.end_lineno or item.lineno,
        ))
        if isinstance(item, ast.ClassDef):
            for member in item.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    add_definition(member, qualified + ".")

    for item in tree.body:
        if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            add_definition(item)
    return ParsedSource(
        "Python", tuple(sorted(imports)), tuple(sorted(targets)),
        tuple(item.name for item in tree.body if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))),
        tuple(definitions),
        basename in {"main.py", "__main__.py", "app.py"} or any(
            isinstance(item, ast.Constant) and item.value == "__main__" for item in ast.walk(tree)
        ),
    )


def _regex_source(relative: str, text: str, language: str, imports: list[str], targets: list[str],
                  patterns: tuple[tuple[str, str], ...], entry: bool = False) -> ParsedSource:
    definitions = []
    seen: set[tuple[str, int]] = set()
    for pattern, kind in patterns:
        for match in re.finditer(pattern, text, re.MULTILINE):
            name = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            key = (name, line)
            if key in seen:
                continue
            seen.add(key)
            definitions.append(_definition(relative, name, name, kind, line))
    return ParsedSource(language, tuple(dict.fromkeys(imports)), tuple(dict.fromkeys(targets)),
                        tuple(item[0] for item in definitions), tuple(definitions), entry)


def parse_source(relative: str, text: str) -> ParsedSource:
    """Return bounded evidence for a recognized source suffix or a generic text file."""
    suffix = "." + relative.rsplit(".", 1)[-1].lower() if "." in relative.rsplit("/", 1)[-1] else ""
    language = LANGUAGES.get(suffix, "文本")
    basename = relative.rsplit("/", 1)[-1].lower()
    if suffix == ".py":
        return _python(relative, text)
    imports: list[str] = []
    targets: list[str] = []
    if suffix in {".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".m", ".mm"}:
        imports.extend(match.group(1) for match in re.finditer(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", text, re.MULTILINE))
        targets.extend(imports)
        patterns = ((r"\b(?:class|struct|enum)\s+([A-Za-z_]\w*)", "class"),
                    (r"\b(?:[A-Za-z_]\w*(?:\s*[*&])?\s+)+([A-Za-z_]\w*)\s*\([^;{}\n]*\)\s*\{", "function"))
        return _regex_source(relative, text, language, imports, targets, patterns, bool(re.search(r"\bmain\s*\(", text)))
    if suffix == ".go":
        imports.extend(match.group(1) for match in re.finditer(r"^\s*(?:import\s+)?\(?(?:\s*[A-Za-z_]\w*\s+)?\"([^\"]+)\"", text, re.MULTILINE))
        targets.extend(imports)
        patterns = ((r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", "function"),
                    (r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)", "class"))
        return _regex_source(relative, text, language, imports, targets, patterns, basename == "main.go")
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        imports.extend(match.group(1) for match in re.finditer(r"\b(?:from|import)\s*[\(]?\s*[\"']([^\"']+)", text))
        imports.extend(match.group(1) for match in re.finditer(r"\brequire\s*\(\s*[\"']([^\"']+)", text))
        targets.extend(imports)
        patterns = ((r"\bclass\s+([A-Za-z_]\w*)", "class"),
                    (r"\bfunction\s+([A-Za-z_]\w*)\s*\(", "function"),
                    (r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(", "function"))
        return _regex_source(relative, text, language, imports, targets, patterns, basename in {"index.js", "index.ts", "main.js", "main.ts"})
    if suffix == ".java":
        imports.extend(match.group(1) for match in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)", text, re.MULTILINE))
        targets.extend(imports)
        patterns = ((r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)", "class"),
                    (r"\b(?:public|private|protected|static|final|native|synchronized|abstract|\s)+[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{", "function"))
        return _regex_source(relative, text, language, imports, targets, patterns, basename == "main.java")
    if suffix == ".rs":
        imports.extend(match.group(1) for match in re.finditer(r"^\s*(?:use|mod)\s+([\w:]+)", text, re.MULTILINE))
        targets.extend(imports)
        patterns = ((r"\b(?:struct|enum|trait)\s+([A-Za-z_]\w*)", "class"),
                    (r"\bfn\s+([A-Za-z_]\w*)\s*\(", "function"))
        return _regex_source(relative, text, language, imports, targets, patterns, basename == "main.rs")
    return ParsedSource(language, (), (), (), (), basename.startswith("main."))
