"""Java AST parser for data lineage detection.

Uses `javalang` to extract methods, classes, imports,
call graphs, and data-touching patterns from Java source files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import javalang


# Patterns that indicate data access
DATA_PATTERNS: dict[str, list[str]] = {
    "jdbc": [
        "JdbcTemplate", "NamedParameterJdbcTemplate", "PreparedStatement",
        "Statement", "CallableStatement", "ResultSet",
        "jdbcTemplate.query", "jdbcTemplate.update", "jdbcTemplate.execute",
        "jdbcTemplate.batchUpdate", "connection.prepareStatement",
        "connection.createStatement", "statement.executeQuery",
        "statement.executeUpdate",
    ],
    "jpa_hibernate": [
        "EntityManager", "entityManager.find", "entityManager.persist",
        "entityManager.merge", "entityManager.remove",
        "entityManager.createQuery", "entityManager.createNativeQuery",
        "Session.get", "Session.save", "Session.update", "Session.delete",
        "CriteriaBuilder", "CriteriaQuery",
    ],
    "spring_data": [
        "JpaRepository", "CrudRepository", "PagingAndSortingRepository",
        "MongoRepository", "@Repository", "@Query",
        "findBy", "findAll", "save(", "saveAll(", "deleteBy", "deleteAll(",
        "existsBy", "countBy",
    ],
    "mybatis": [
        "@Select", "@Insert", "@Update", "@Delete",
        "SqlSession", "sqlSession.selectList", "sqlSession.selectOne",
        "sqlSession.insert", "sqlSession.update", "sqlSession.delete",
    ],
    "rest_endpoint": [
        "@GetMapping", "@PostMapping", "@PutMapping", "@DeleteMapping",
        "@PatchMapping", "@RequestMapping",
    ],
    "rest_client": [
        "RestTemplate", "WebClient", "HttpClient", "HttpURLConnection",
        "restTemplate.getForObject", "restTemplate.postForObject",
        "restTemplate.exchange", "webClient.get", "webClient.post",
    ],
    "kafka": [
        "KafkaTemplate", "kafkaTemplate.send", "@KafkaListener",
        "KafkaProducer", "KafkaConsumer", "producer.send",
    ],
    "file_io": [
        "FileReader", "FileWriter", "BufferedReader", "BufferedWriter",
        "FileInputStream", "FileOutputStream", "Files.read", "Files.write",
        "Files.lines", "Files.newBufferedReader",
    ],
    "stored_procedure": [
        "StoredProcedureQuery", "CallableStatement",
        "@Procedure", "SimpleJdbcCall", "simpleJdbcCall.execute",
    ],
    "raw_sql": [],  # Detected via regex
}

SQL_PATTERN = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|MERGE\s+INTO|CALL\s+)\b",
    re.IGNORECASE,
)

ANNOTATION_PATTERN_MAP = {
    "GetMapping": "rest_endpoint",
    "PostMapping": "rest_endpoint",
    "PutMapping": "rest_endpoint",
    "DeleteMapping": "rest_endpoint",
    "PatchMapping": "rest_endpoint",
    "RequestMapping": "rest_endpoint",
    "KafkaListener": "kafka",
    "Query": "spring_data",
    "Repository": "spring_data",
    "Select": "mybatis",
    "Insert": "mybatis",
    "Update": "mybatis",
    "Delete": "mybatis",
    "Procedure": "stored_procedure",
}


@dataclass
class JavaMethodInfo:
    name: str
    class_name: str | None
    lineno: int
    end_lineno: int
    return_type: str | None
    parameters: list[str]
    annotations: list[str]
    calls: list[str]
    source_snippet: str
    detected_patterns: list[str]
    sql_strings: list[str]


@dataclass
class JavaImportInfo:
    path: str
    is_static: bool
    lineno: int


@dataclass
class JavaClassInfo:
    name: str
    extends: str | None
    implements: list[str]
    lineno: int
    methods: list[str]
    annotations: list[str]


@dataclass
class JavaFileAnalysis:
    file_path: str
    package: str | None
    imports: list[JavaImportInfo]
    classes: list[JavaClassInfo]
    methods: list[JavaMethodInfo]


class JavaASTParser:
    """Parses a Java file and extracts structural + data-flow info."""

    def parse_file(self, file_path: str | Path) -> JavaFileAnalysis:
        file_path = Path(file_path)
        source = file_path.read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
        tree = javalang.parse.parse(source)

        package = tree.package.name if tree.package else None
        imports = self._extract_imports(tree)
        classes = []
        methods = []

        for _, node in tree.filter(javalang.tree.ClassDeclaration):
            cls = self._extract_class(node)
            classes.append(cls)
            for method_node in node.methods:
                method = self._extract_method(method_node, node.name, lines, source)
                methods.append(method)

        for _, node in tree.filter(javalang.tree.InterfaceDeclaration):
            for method_node in node.methods:
                method = self._extract_method(method_node, node.name, lines, source)
                methods.append(method)

        return JavaFileAnalysis(
            file_path=str(file_path),
            package=package,
            imports=imports,
            classes=classes,
            methods=methods,
        )

    def _extract_imports(self, tree: javalang.tree.CompilationUnit) -> list[JavaImportInfo]:
        imports = []
        for imp in tree.imports:
            imports.append(JavaImportInfo(
                path=imp.path,
                is_static=imp.static,
                lineno=imp.position.line if imp.position else 0,
            ))
        return imports

    def _extract_class(self, node: javalang.tree.ClassDeclaration) -> JavaClassInfo:
        extends = None
        if node.extends:
            extends = node.extends.name if hasattr(node.extends, "name") else str(node.extends)

        implements = []
        if node.implements:
            for iface in node.implements:
                implements.append(iface.name if hasattr(iface, "name") else str(iface))

        methods = [m.name for m in node.methods]
        annotations = [a.name for a in (node.annotations or [])]

        return JavaClassInfo(
            name=node.name,
            extends=extends,
            implements=implements,
            lineno=node.position.line if node.position else 0,
            methods=methods,
            annotations=annotations,
        )

    def _extract_method(
        self,
        node: javalang.tree.MethodDeclaration,
        class_name: str,
        lines: list[str],
        source: str,
    ) -> JavaMethodInfo:
        lineno = node.position.line if node.position else 0
        annotations = [a.name for a in (node.annotations or [])]
        parameters = []
        for p in (node.parameters or []):
            type_name = p.type.name if hasattr(p.type, "name") else str(p.type)
            parameters.append(f"{type_name} {p.name}")

        return_type = None
        if node.return_type:
            return_type = node.return_type.name if hasattr(node.return_type, "name") else str(node.return_type)

        # Extract source snippet — find the method body via braces
        snippet, end_lineno = self._extract_method_snippet(lines, lineno)

        # Extract method calls
        calls = self._extract_calls(node)

        # Detect patterns
        patterns = self._detect_patterns(snippet, calls, annotations)
        sql_strings = self._extract_sql_strings(snippet)

        if sql_strings:
            patterns.append("raw_sql")

        return JavaMethodInfo(
            name=node.name,
            class_name=class_name,
            lineno=lineno,
            end_lineno=end_lineno,
            return_type=return_type,
            parameters=parameters,
            annotations=annotations,
            calls=calls,
            source_snippet=snippet,
            detected_patterns=list(set(patterns)),
            sql_strings=sql_strings,
        )

    def _extract_method_snippet(self, lines: list[str], start_line: int) -> tuple[str, int]:
        """Extract method body by counting braces from the start line."""
        if start_line <= 0 or start_line > len(lines):
            return "", start_line

        brace_count = 0
        started = False
        end_line = start_line

        for i in range(start_line - 1, min(start_line + 200, len(lines))):
            line = lines[i]
            for ch in line:
                if ch == "{":
                    brace_count += 1
                    started = True
                elif ch == "}":
                    brace_count -= 1
            end_line = i + 1
            if started and brace_count <= 0:
                break

        snippet_lines = lines[start_line - 1 : end_line]
        return "\n".join(snippet_lines), end_line

    def _extract_calls(self, node: javalang.tree.MethodDeclaration) -> list[str]:
        calls = []
        if not node.body:
            return calls
        for _, child in node.filter(javalang.tree.MethodInvocation):
            qualifier = child.qualifier or ""
            name = child.member
            if qualifier:
                calls.append(f"{qualifier}.{name}")
            else:
                calls.append(name)
        return calls

    def _detect_patterns(
        self, snippet: str, calls: list[str], annotations: list[str]
    ) -> list[str]:
        detected = []
        combined = snippet + " " + " ".join(calls)

        for pattern_name, markers in DATA_PATTERNS.items():
            if pattern_name == "raw_sql":
                continue
            for marker in markers:
                if marker in combined:
                    detected.append(pattern_name)
                    break

        for ann in annotations:
            if ann in ANNOTATION_PATTERN_MAP:
                pattern = ANNOTATION_PATTERN_MAP[ann]
                if pattern not in detected:
                    detected.append(pattern)

        return detected

    def _extract_sql_strings(self, snippet: str) -> list[str]:
        """Find string literals that look like SQL in the snippet."""
        sql_strings = []
        # Match Java string literals (simple approach)
        string_pattern = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
        for match in string_pattern.finditer(snippet):
            value = match.group(1)
            if SQL_PATTERN.search(value):
                sql_strings.append(value.strip())
        return sql_strings

    def get_data_touching_methods(self, file_path: str | Path) -> list[JavaMethodInfo]:
        """Return only methods that touch data."""
        analysis = self.parse_file(file_path)
        return [m for m in analysis.methods if m.detected_patterns or m.sql_strings]
