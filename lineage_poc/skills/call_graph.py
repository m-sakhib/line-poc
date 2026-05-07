"""Cross-file call graph builder.

Builds a graph of which functions/methods call which others across files.
Used to trace data lineage chains: Controller -> Service -> DAO -> DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lineage_poc.skills.python.ast_parser import PythonASTParser, PythonFileAnalysis
from lineage_poc.skills.java.ast_parser import JavaASTParser, JavaFileAnalysis
from lineage_poc.skills.cobol_aps.ast_parser import APSParser, APSFileAnalysis


@dataclass
class CallGraphNode:
    """A function/method in the call graph."""

    qualified_name: str        # e.g., "OrderService.saveOrder" or "etl.pipeline.extract_data"
    file_path: str
    language: str
    lineno: int
    end_lineno: int
    snippet: str
    detected_patterns: list[str]
    sql_strings: list[str]
    raw_calls: list[str]       # Unresolved call names from AST


@dataclass
class CallGraphEdge:
    caller: str    # Qualified name
    callee: str    # Qualified name


@dataclass
class CallGraph:
    nodes: dict[str, CallGraphNode] = field(default_factory=dict)
    edges: list[CallGraphEdge] = field(default_factory=list)
    # Reverse index: callee -> list of callers
    callers_of: dict[str, list[str]] = field(default_factory=dict)
    # Forward index: caller -> list of callees
    callees_of: dict[str, list[str]] = field(default_factory=dict)

    def add_node(self, node: CallGraphNode) -> None:
        self.nodes[node.qualified_name] = node

    def add_edge(self, caller: str, callee: str) -> None:
        self.edges.append(CallGraphEdge(caller=caller, callee=callee))
        self.callees_of.setdefault(caller, []).append(callee)
        self.callers_of.setdefault(callee, []).append(caller)

    def get_call_chain(self, node_name: str, direction: str = "down", max_depth: int = 10) -> list[str]:
        """Trace the call chain from a node.

        direction="down": follow callees (what does this function call?)
        direction="up": follow callers (who calls this function?)
        """
        visited = set()
        chain = []

        def _walk(name: str, depth: int) -> None:
            if name in visited or depth > max_depth:
                return
            visited.add(name)
            chain.append(name)
            index = self.callees_of if direction == "down" else self.callers_of
            for next_name in index.get(name, []):
                _walk(next_name, depth + 1)

        _walk(node_name, 0)
        return chain

    def get_data_flow_chains(self) -> list[list[str]]:
        """Find all chains that start from an entry point and end at a data-touching node."""
        data_nodes = {
            name for name, node in self.nodes.items()
            if node.detected_patterns or node.sql_strings
        }
        # Entry points: nodes that are not called by anything, or are REST endpoints
        entry_points = set()
        for name, node in self.nodes.items():
            if name not in self.callers_of:
                entry_points.add(name)
            if any(p in ("rest_endpoint", "fastapi") for p in node.detected_patterns):
                entry_points.add(name)

        chains = []
        for entry in entry_points:
            chain = self.get_call_chain(entry, direction="down")
            # Keep if chain reaches a data node
            if any(n in data_nodes for n in chain):
                chains.append(chain)
        return chains


class CallGraphBuilder:
    """Builds a cross-file call graph by scanning all source files."""

    LANGUAGE_EXTENSIONS = {
        ".py": "python",
        ".java": "java",
        ".aps": "cobol_aps",
    }

    def __init__(self) -> None:
        self._python_parser = PythonASTParser()
        self._java_parser = JavaASTParser()
        self._aps_parser = APSParser()

    def build(self, repo_path: str | Path) -> CallGraph:
        repo_path = Path(repo_path)
        graph = CallGraph()

        # Phase 1: Parse all files, create nodes
        file_analyses: list[tuple[str, str, object]] = []  # (file_path, lang, analysis)
        for ext, lang in self.LANGUAGE_EXTENSIONS.items():
            for source_file in repo_path.rglob(f"*{ext}"):
                # Skip common non-source dirs
                parts = source_file.relative_to(repo_path).parts
                if any(p in ("node_modules", ".git", "__pycache__", ".venv", "venv", "build", "target", ".tox") for p in parts):
                    continue
                try:
                    if lang == "python":
                        analysis = self._python_parser.parse_file(source_file)
                    elif lang == "java":
                        analysis = self._java_parser.parse_file(source_file)
                    elif lang == "cobol_aps":
                        analysis = self._aps_parser.parse_file(source_file)
                    else:
                        continue
                    file_analyses.append((str(source_file), lang, analysis))
                    self._add_nodes(graph, str(source_file), lang, analysis)
                except Exception as e:
                    # Skip unparseable files
                    continue

        # Phase 2: Resolve calls to edges
        self._resolve_edges(graph)

        return graph

    def _add_nodes(
        self, graph: CallGraph, file_path: str, lang: str, analysis: object
    ) -> None:
        if lang == "python":
            assert isinstance(analysis, PythonFileAnalysis)
            for func in analysis.functions:
                qname = f"{func.class_name}.{func.name}" if func.class_name else func.name
                # Prefix with module-like path for uniqueness
                module = Path(file_path).stem
                qname = f"{module}.{qname}"
                graph.add_node(CallGraphNode(
                    qualified_name=qname,
                    file_path=file_path,
                    language=lang,
                    lineno=func.lineno,
                    end_lineno=func.end_lineno,
                    snippet=func.source_snippet,
                    detected_patterns=func.detected_patterns,
                    sql_strings=func.sql_strings,
                    raw_calls=func.calls,
                ))
        elif lang == "java":
            assert isinstance(analysis, JavaFileAnalysis)
            for method in analysis.methods:
                qname = f"{method.class_name}.{method.name}" if method.class_name else method.name
                graph.add_node(CallGraphNode(
                    qualified_name=qname,
                    file_path=file_path,
                    language=lang,
                    lineno=method.lineno,
                    end_lineno=method.end_lineno,
                    snippet=method.source_snippet,
                    detected_patterns=method.detected_patterns,
                    sql_strings=method.sql_strings,
                    raw_calls=method.calls,
                ))
        elif lang == "cobol_aps":
            assert isinstance(analysis, APSFileAnalysis)
            program_name = Path(file_path).stem
            for section in analysis.sections:
                if not section.has_data_flow:
                    continue
                qname = f"{program_name}.{section.name}"
                detected = list({m.category for m in section.matches if m.data_role in ('source', 'target', 'call')})
                raw_calls = [c.target_program for c in analysis.calls if section.lineno <= c.lineno <= section.end_lineno]
                graph.add_node(CallGraphNode(
                    qualified_name=qname,
                    file_path=file_path,
                    language=lang,
                    lineno=section.lineno,
                    end_lineno=section.end_lineno,
                    snippet=section.snippet,
                    detected_patterns=detected,
                    sql_strings=[],
                    raw_calls=raw_calls,
                ))

    def _resolve_edges(self, graph: CallGraph) -> None:
        """Best-effort resolution of raw call names to graph nodes."""
        # Build a reverse index: short_name -> list of qualified names
        short_to_qualified: dict[str, list[str]] = {}
        for qname in graph.nodes:
            short = qname.rsplit(".", 1)[-1]  # e.g., "saveOrder"
            short_to_qualified.setdefault(short, []).append(qname)
            # Also index with class.method
            parts = qname.split(".")
            if len(parts) >= 2:
                class_method = f"{parts[-2]}.{parts[-1]}"
                short_to_qualified.setdefault(class_method, []).append(qname)

        for qname, node in graph.nodes.items():
            for raw_call in node.raw_calls:
                # Try exact match first
                candidates = short_to_qualified.get(raw_call, [])
                if not candidates:
                    # Try just the method name part
                    short = raw_call.rsplit(".", 1)[-1]
                    candidates = short_to_qualified.get(short, [])

                for candidate in candidates:
                    if candidate != qname:  # Avoid self-edges
                        graph.add_edge(qname, candidate)
