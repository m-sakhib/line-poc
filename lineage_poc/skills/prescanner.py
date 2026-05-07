"""AST pre-scanner: scans the repo and builds work manifest for the LLM agent.

This runs BEFORE the agent. No LLM calls. It produces DataFlowSnippet objects
that are small, self-contained chunks the agent can analyze one at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lineage_poc.skills.call_graph import CallGraph, CallGraphBuilder, CallGraphNode


@dataclass
class DataFlowSnippet:
    """A unit of work for the LLM agent to analyze."""

    file_path: str
    language: str
    function_name: str
    class_name: str | None
    snippet: str
    call_chain: list[str]
    detected_patterns: list[str]
    sql_strings: list[str]
    imports_context: str          # Relevant imports as text
    callers_snippet: str          # Snippets of functions that call this one
    callees_snippet: str          # Snippets of functions this one calls


@dataclass
class WorkManifest:
    repo_path: str
    total_files: int
    total_snippets: int
    languages: list[str]
    snippets: list[DataFlowSnippet]
    call_graph: CallGraph


class PreScanner:
    """Scans a repository and produces a WorkManifest."""

    MAX_SNIPPET_CONTEXT_LINES = 30  # Max lines for caller/callee context

    def __init__(self) -> None:
        self._graph_builder = CallGraphBuilder()

    def scan(self, repo_path: str | Path) -> WorkManifest:
        repo_path = Path(repo_path)

        # Build call graph (parses all files)
        graph = self._graph_builder.build(repo_path)

        # Find data flow chains
        chains = graph.get_data_flow_chains()

        # Collect all data-touching nodes from chains
        data_node_names: set[str] = set()
        chain_map: dict[str, list[list[str]]] = {}  # node_name -> chains containing it

        for chain in chains:
            for name in chain:
                node = graph.nodes.get(name)
                if node and (node.detected_patterns or node.sql_strings):
                    data_node_names.add(name)
                    chain_map.setdefault(name, []).append(chain)

        # Also add data-touching nodes not in any chain (standalone)
        for name, node in graph.nodes.items():
            if (node.detected_patterns or node.sql_strings) and name not in data_node_names:
                data_node_names.add(name)
                chain_map.setdefault(name, [[name]])

        # Build snippets
        snippets = []
        for name in sorted(data_node_names):
            node = graph.nodes[name]
            snippet = self._build_snippet(node, graph, chain_map.get(name, []))
            snippets.append(snippet)

        # Count files and languages
        files = {node.file_path for node in graph.nodes.values()}
        languages = list({node.language for node in graph.nodes.values()})

        return WorkManifest(
            repo_path=str(repo_path),
            total_files=len(files),
            total_snippets=len(snippets),
            languages=languages,
            snippets=snippets,
            call_graph=graph,
        )

    def _build_snippet(
        self,
        node: CallGraphNode,
        graph: CallGraph,
        chains: list[list[str]],
    ) -> DataFlowSnippet:
        # Build the longest chain containing this node
        best_chain = max(chains, key=len) if chains else [node.qualified_name]

        # Get caller snippets (who calls this?)
        callers_context = []
        for caller_name in graph.callers_of.get(node.qualified_name, [])[:3]:
            caller_node = graph.nodes.get(caller_name)
            if caller_node:
                trimmed = self._trim_snippet(caller_node.snippet)
                callers_context.append(f"// Caller: {caller_name} ({caller_node.file_path}:{caller_node.lineno})\n{trimmed}")

        # Get callee snippets (what does this call?)
        callees_context = []
        for callee_name in graph.callees_of.get(node.qualified_name, [])[:3]:
            callee_node = graph.nodes.get(callee_name)
            if callee_node:
                trimmed = self._trim_snippet(callee_node.snippet)
                callees_context.append(f"// Callee: {callee_name} ({callee_node.file_path}:{callee_node.lineno})\n{trimmed}")

        # Build imports context from the file
        imports_context = self._get_imports_context(node.file_path, node.language)

        parts = node.qualified_name.split(".")
        class_name = parts[-2] if len(parts) >= 3 else (parts[0] if len(parts) == 2 else None)
        func_name = parts[-1]

        return DataFlowSnippet(
            file_path=node.file_path,
            language=node.language,
            function_name=func_name,
            class_name=class_name,
            snippet=node.snippet,
            call_chain=best_chain,
            detected_patterns=node.detected_patterns,
            sql_strings=node.sql_strings,
            imports_context=imports_context,
            callers_snippet="\n\n".join(callers_context),
            callees_snippet="\n\n".join(callees_context),
        )

    def _trim_snippet(self, snippet: str) -> str:
        lines = snippet.splitlines()
        if len(lines) > self.MAX_SNIPPET_CONTEXT_LINES:
            return "\n".join(lines[: self.MAX_SNIPPET_CONTEXT_LINES]) + "\n// ... truncated"
        return snippet

    def _get_imports_context(self, file_path: str, language: str) -> str:
        """Read the imports section from a file."""
        try:
            lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return ""

        import_lines = []
        for line in lines:
            stripped = line.strip()
            if language == "python":
                if stripped.startswith(("import ", "from ")):
                    import_lines.append(stripped)
            elif language == "java":
                if stripped.startswith("import "):
                    import_lines.append(stripped)
                elif stripped.startswith("package "):
                    import_lines.append(stripped)
            elif language == "cobol_aps":
                # For APS, collect COPY statements and file declarations as "imports"
                upper = stripped.upper()
                if upper.startswith(("COPY ", "SELECT ", "IO SELECT ", "FD ")):
                    import_lines.append(stripped)
        return "\n".join(import_lines)
