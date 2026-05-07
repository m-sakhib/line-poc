"""Python AST parser for data lineage detection.

Uses the stdlib `ast` module to extract functions, classes, imports,
call graphs, and data-touching patterns from Python source files.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


# Patterns that indicate data access
DATA_PATTERNS: dict[str, list[str]] = {
    "sqlalchemy": [
        "session.query", "session.execute", "session.add", "session.commit",
        "session.delete", "session.merge", "session.bulk_save_objects",
        "engine.execute", "engine.connect",
        "db.query", "db.execute", "db.add", "db.commit",
        ".query(", ".execute(",
    ],
    "django_orm": [
        ".objects.filter", ".objects.get", ".objects.create",
        ".objects.update", ".objects.delete", ".objects.all",
        ".objects.exclude", ".objects.annotate", ".objects.aggregate",
        ".objects.values", ".objects.raw", ".save(", ".delete(",
    ],
    "pandas": [
        "pd.read_csv", "pd.read_excel", "pd.read_sql", "pd.read_json",
        "pd.read_parquet", "pd.read_hdf", "pd.read_feather",
        "pd.DataFrame", "pd.merge", "pd.concat",
        ".to_csv", ".to_sql", ".to_excel", ".to_json", ".to_parquet",
        ".merge(", ".join(", ".groupby(", ".pivot_table(", ".melt(",
        ".apply(", ".map(", ".transform(",
    ],
    "file_io": [
        "open(", "pathlib", ".read(", ".write(",
        "json.load", "json.dump", "json.loads", "json.dumps",
        "csv.reader", "csv.writer", "csv.DictReader", "csv.DictWriter",
    ],
    "http": [
        "requests.get", "requests.post", "requests.put", "requests.delete",
        "requests.patch", "httpx.get", "httpx.post", "httpx.put",
        "httpx.AsyncClient", "aiohttp.ClientSession",
        "urllib.request.urlopen",
    ],
    "fastapi": [
        "app.get", "app.post", "app.put", "app.delete",
        "router.get", "router.post", "router.put", "router.delete",
    ],
    "kafka": [
        "KafkaProducer", "KafkaConsumer", "producer.send", "consumer.poll",
    ],
    "raw_sql": [],  # Detected via regex on string literals
}

SQL_PATTERN = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|MERGE\s+INTO|CALL\s+)\b",
    re.IGNORECASE,
)


@dataclass
class FunctionInfo:
    name: str
    class_name: str | None
    lineno: int
    end_lineno: int
    args: list[str]
    decorators: list[str]
    calls: list[str]             # Functions/methods this function calls
    source_snippet: str
    detected_patterns: list[str]
    sql_strings: list[str]


@dataclass
class ImportInfo:
    module: str
    names: list[str]
    alias: str | None
    lineno: int


@dataclass
class ClassInfo:
    name: str
    bases: list[str]
    lineno: int
    methods: list[str]
    decorators: list[str]


@dataclass
class PythonFileAnalysis:
    file_path: str
    imports: list[ImportInfo]
    classes: list[ClassInfo]
    functions: list[FunctionInfo]
    module_level_calls: list[str]


class PythonASTParser:
    """Parses a Python file and extracts structural + data-flow info."""

    def parse_file(self, file_path: str | Path) -> PythonFileAnalysis:
        file_path = Path(file_path)
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(file_path))
        lines = source.splitlines()

        imports = self._extract_imports(tree)
        classes = self._extract_classes(tree)
        functions = self._extract_functions(tree, lines, class_name=None)

        # Also extract methods from classes
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = self._extract_functions(node, lines, class_name=node.name)
                functions.extend(methods)

        module_calls = self._extract_module_level_calls(tree)

        return PythonFileAnalysis(
            file_path=str(file_path),
            imports=imports,
            classes=classes,
            functions=functions,
            module_level_calls=module_calls,
        )

    def _extract_imports(self, tree: ast.Module) -> list[ImportInfo]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(ImportInfo(
                        module=alias.name,
                        names=[alias.name],
                        alias=alias.asname,
                        lineno=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports.append(ImportInfo(
                    module=module,
                    names=[a.name for a in node.names],
                    alias=None,
                    lineno=node.lineno,
                ))
        return imports

    def _extract_classes(self, tree: ast.Module) -> list[ClassInfo]:
        classes = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        bases.append(ast.unparse(base))
                methods = [
                    n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                decorators = [ast.unparse(d) for d in node.decorator_list]
                classes.append(ClassInfo(
                    name=node.name,
                    bases=bases,
                    lineno=node.lineno,
                    methods=methods,
                    decorators=decorators,
                ))
        return classes

    def _extract_functions(
        self, parent: ast.AST, lines: list[str], class_name: str | None
    ) -> list[FunctionInfo]:
        functions = []
        for node in ast.iter_child_nodes(parent):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            args = [a.arg for a in node.args.args]
            decorators = [ast.unparse(d) for d in node.decorator_list]
            calls = self._extract_calls(node)
            end_lineno = node.end_lineno or node.lineno

            # Extract source snippet
            snippet_lines = lines[node.lineno - 1 : end_lineno]
            snippet = "\n".join(snippet_lines)

            # Detect data patterns
            patterns = self._detect_patterns(snippet, calls, decorators)
            sql_strings = self._extract_sql_strings(node)

            if sql_strings:
                patterns.append("raw_sql")

            functions.append(FunctionInfo(
                name=node.name,
                class_name=class_name,
                lineno=node.lineno,
                end_lineno=end_lineno,
                args=args,
                decorators=decorators,
                calls=calls,
                source_snippet=snippet,
                detected_patterns=list(set(patterns)),
                sql_strings=sql_strings,
            ))
        return functions

    def _extract_calls(self, node: ast.AST) -> list[str]:
        """Extract all function/method calls within a node."""
        calls = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._resolve_call_name(child)
                if call_name:
                    calls.append(call_name)
        return calls

    def _resolve_call_name(self, call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        elif isinstance(func, ast.Attribute):
            return ast.unparse(func)
        return None

    def _detect_patterns(
        self, snippet: str, calls: list[str], decorators: list[str] | None = None
    ) -> list[str]:
        detected = []
        call_str = " ".join(calls)
        dec_str = " ".join(decorators or [])
        combined = snippet + " " + call_str + " " + dec_str

        for pattern_name, markers in DATA_PATTERNS.items():
            if pattern_name == "raw_sql":
                continue
            for marker in markers:
                if marker in combined:
                    detected.append(pattern_name)
                    break
        return detected

    def _extract_sql_strings(self, node: ast.AST) -> list[str]:
        """Find string literals that look like SQL."""
        sql_strings = []
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if SQL_PATTERN.search(child.value):
                    sql_strings.append(child.value.strip())
            elif isinstance(child, ast.JoinedStr):
                # f-strings — try to reconstruct
                parts = []
                for v in child.values:
                    if isinstance(v, ast.Constant):
                        parts.append(str(v.value))
                    else:
                        parts.append("{...}")
                full = "".join(parts)
                if SQL_PATTERN.search(full):
                    sql_strings.append(full)
        return sql_strings

    def _extract_module_level_calls(self, tree: ast.Module) -> list[str]:
        calls = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = self._resolve_call_name(node.value)
                if name:
                    calls.append(name)
        return calls

    def get_data_touching_functions(self, file_path: str | Path) -> list[FunctionInfo]:
        """Return only functions that touch data (have detected patterns or SQL)."""
        analysis = self.parse_file(file_path)
        return [f for f in analysis.functions if f.detected_patterns or f.sql_strings]
