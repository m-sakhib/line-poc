"""Custom tools exposed to the Copilot agent.

- emit_lineage_record: Agent calls this to output one lineage record (appended to JSONL)
- analyze_ast: Agent can request additional AST info for ambiguous snippets
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from copilot import define_tool

from lineage_poc.schema.lineage_record import EvidenceStep, LineageRecord, LineageSchema
from lineage_poc.skills.python.ast_parser import PythonASTParser
from lineage_poc.skills.java.ast_parser import JavaASTParser
from lineage_poc.skills.cobol_aps.ast_parser import APSParser
from lineage_poc.tools.verification import LineageVerifier, VerificationResult


# ── emit_lineage_record ──────────────────────────────────────────────

class EvidenceStepParam(BaseModel):
    step: int = Field(description="Step number in the transformation chain (1-based)")
    file: str = Field(description="Source file path where this step occurs")
    line: int = Field(description="Line number")
    code: str = Field(description="The code snippet for this step")
    description: str = Field(description="What this step does to the data (e.g., 'Filter active users', 'Join with orders table')")


class EmitLineageRecordParams(BaseModel):
    sourceTechnologyType: str = Field(description="Source technology: PostgreSQL, MySQL, REST API, Kafka, CSV, etc.")
    sourceEntityName: str = Field(description="Source entity: table name, endpoint path, topic name, file name")
    targetTechnologyType: str = Field(description="Target technology type")
    targetEntityName: str = Field(description="Target entity name")
    dataOperationEvidence: list[EvidenceStepParam] = Field(
        min_length=1,
        description="Ordered list of ALL transformation steps from source to target. Include every map, filter, join, type conversion, column rename, aggregation."
    )
    # Optional fields
    sourceAppID: str | None = Field(default=None, description="Source application ID")
    sourceDataSourceName: str | None = Field(default=None, description="Source data source name as used in code")
    sourceSchemaType: str | None = Field(default=None, description="Schema type: database, api, file, message_queue")
    sourceSchemaName: str | None = Field(default=None, description="Schema name")
    sourceEntityType: str | None = Field(default=None, description="Entity type: table, view, endpoint, topic, file")
    sourceColumnDataType: str | None = Field(default=None, description="Source column data type")
    sourceColumnName: str | None = Field(default=None, description="Source column name")
    targetAppID: str | None = Field(default=None, description="Target application ID")
    targetDataTargetName: str | None = Field(default=None, description="Target data destination name")
    targetSchemaType: str | None = Field(default=None, description="Target schema type")
    targetSchemaName: str | None = Field(default=None, description="Target schema name")
    targetEntityType: str | None = Field(default=None, description="Target entity type")
    targetColumnDataType: str | None = Field(default=None, description="Target column data type")
    targetColumnName: str | None = Field(default=None, description="Target column name")
    dataOperationType: str | None = Field(default=None, description="READ, WRITE, TRANSFORM, COPY, JOIN, FILTER, AGGREGATE")
    dataOperationName: str | None = Field(default=None, description="Function/method name performing the operation")
    dataOperationDescription: str | None = Field(default=None, description="Human-readable description")


class LineageOutputCollector:
    """Thread-safe collector that appends records to a JSONL file."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def append(self, record: dict[str, Any]) -> int:
        with self._lock:
            with open(self.output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._count += 1
            return self._count


def build_emit_lineage_tool(
    collector: LineageOutputCollector,
    verifier: LineageVerifier | None = None,
    parser_detected_patterns: list[str] | None = None,
):
    """Build the emit_lineage_record tool bound to a specific collector.

    Args:
        collector: Output collector for JSONL records.
        verifier: Optional LineageVerifier for confidence scoring.
        parser_detected_patterns: Patterns the parser detected for the current snippet.
    """

    @define_tool(
        name="emit_lineage_record",
        description=(
            "Emit a single data lineage record. Call this once for EACH source-to-target "
            "data flow you identify. The dataOperationEvidence must include EVERY intermediate "
            "transformation step (map, filter, join, type conversion, aggregation, column rename) "
            "between source and target."
        ),
        skip_permission=True,
    )
    async def emit_lineage_record(params: EmitLineageRecordParams) -> str:
        record = params.model_dump(exclude_none=True)
        # Convert evidence steps to dicts
        record["dataOperationEvidence"] = [
            step.model_dump() for step in params.dataOperationEvidence
        ]

        # Run verification if available
        verification_msg = ""
        if verifier:
            # Format evidence as string for verification
            evidence_str = " | ".join(
                f"[{s.file}:{s.line}] {s.code}"
                for s in params.dataOperationEvidence
            )
            record_for_verify = {**record, "dataOperationEvidence": evidence_str}
            result: VerificationResult = verifier.verify(
                record_for_verify, parser_detected_patterns
            )
            record["confidence"] = result.confidence
            record["verificationIssues"] = result.issues if result.issues else None
            verification_msg = f" [confidence={result.confidence}]"
            if result.needs_review:
                verification_msg += " [NEEDS REVIEW]"

        count = collector.append(record)
        return (
            f"Record #{count} saved: {params.sourceEntityName} -> {params.targetEntityName} "
            f"({len(params.dataOperationEvidence)} evidence steps){verification_msg}"
        )

    return emit_lineage_record


# ── analyze_ast ──────────────────────────────────────────────────────

class AnalyzeAstParams(BaseModel):
    file_path: str = Field(description="Path to the source file to analyze")
    query: str = Field(description="What to find: 'imports', 'function_calls', 'callers', 'callees', 'data_flows', 'function_body'")
    scope: str | None = Field(default=None, description="Limit to a specific function or class name")


_python_parser = PythonASTParser()
_java_parser = JavaASTParser()
_aps_parser = APSParser()


@define_tool(
    name="analyze_ast",
    description=(
        "Query the AST of a source file for additional context. Use this when "
        "a code snippet is ambiguous and you need to see what a function calls, "
        "who calls it, or what imports are used."
    ),
    skip_permission=True,
)
async def analyze_ast(params: AnalyzeAstParams) -> str:
    file_path = Path(params.file_path)
    if not file_path.exists():
        return f"File not found: {params.file_path}"

    ext = file_path.suffix.lower()

    try:
        if ext == ".py":
            analysis = _python_parser.parse_file(file_path)
            return _format_python_analysis(analysis, params.query, params.scope)
        elif ext == ".java":
            analysis = _java_parser.parse_file(file_path)
            return _format_java_analysis(analysis, params.query, params.scope)
        elif ext == ".aps":
            analysis = _aps_parser.parse_file(file_path)
            return _format_aps_analysis(analysis, params.query, params.scope)
        else:
            return f"Unsupported file type: {ext}"
    except Exception as e:
        return f"AST parse error: {e}"


def _format_python_analysis(analysis, query: str, scope: str | None) -> str:
    if query == "imports":
        lines = [f"from {imp.module} import {', '.join(imp.names)}" for imp in analysis.imports]
        return "\n".join(lines) or "No imports found."

    functions = analysis.functions
    if scope:
        functions = [f for f in functions if f.name == scope or f.class_name == scope]

    if query == "function_calls":
        lines = []
        for f in functions:
            name = f"{f.class_name}.{f.name}" if f.class_name else f.name
            lines.append(f"{name} calls: {', '.join(f.calls) or 'nothing'}")
        return "\n".join(lines) or "No functions found."

    if query == "data_flows":
        lines = []
        for f in functions:
            if f.detected_patterns or f.sql_strings:
                name = f"{f.class_name}.{f.name}" if f.class_name else f.name
                lines.append(f"{name}: patterns={f.detected_patterns}, sql={f.sql_strings}")
        return "\n".join(lines) or "No data flows detected."

    if query == "function_body":
        lines = []
        for f in functions:
            name = f"{f.class_name}.{f.name}" if f.class_name else f.name
            lines.append(f"# {name} (line {f.lineno})\n{f.source_snippet}")
        return "\n\n".join(lines) or "Function not found."

    return f"Unknown query type: {query}. Use: imports, function_calls, data_flows, function_body"


def _format_java_analysis(analysis, query: str, scope: str | None) -> str:
    if query == "imports":
        lines = [f"import {imp.path};" for imp in analysis.imports]
        return "\n".join(lines) or "No imports found."

    methods = analysis.methods
    if scope:
        methods = [m for m in methods if m.name == scope or m.class_name == scope]

    if query == "function_calls":
        lines = []
        for m in methods:
            name = f"{m.class_name}.{m.name}" if m.class_name else m.name
            lines.append(f"{name} calls: {', '.join(m.calls) or 'nothing'}")
        return "\n".join(lines) or "No methods found."

    if query == "data_flows":
        lines = []
        for m in methods:
            if m.detected_patterns or m.sql_strings:
                name = f"{m.class_name}.{m.name}" if m.class_name else m.name
                lines.append(f"{name}: patterns={m.detected_patterns}, sql={m.sql_strings}")
        return "\n".join(lines) or "No data flows detected."

    if query == "function_body":
        lines = []
        for m in methods:
            name = f"{m.class_name}.{m.name}" if m.class_name else m.name
            lines.append(f"// {name} (line {m.lineno})\n{m.source_snippet}")
        return "\n\n".join(lines) or "Method not found."

    return f"Unknown query type: {query}. Use: imports, function_calls, data_flows, function_body"


def _format_aps_analysis(analysis, query: str, scope: str | None) -> str:
    """Format APS analysis results for the agent."""
    from lineage_poc.skills.cobol_aps.ast_parser import APSFileAnalysis

    assert isinstance(analysis, APSFileAnalysis)

    if query == "imports":
        lines = [f"COPY {c.copybook_name}" for c in analysis.copies]
        for fd in analysis.file_declarations:
            lines.append(f"SELECT {fd.logical_name} ASSIGN TO {fd.physical_name or '?'}")
        return "\n".join(lines) or "No file declarations or COPY statements found."

    sections = analysis.sections
    if scope:
        sections = [s for s in sections if s.name == scope]

    if query == "function_calls":
        lines = []
        for s in sections:
            calls_in_section = [
                c.target_program for c in analysis.calls
                if s.lineno <= c.lineno <= s.end_lineno
            ]
            lines.append(f"{s.name} calls: {', '.join(calls_in_section) or 'nothing'}")
        return "\n".join(lines) or "No sections found."

    if query == "data_flows":
        lines = []
        for s in sections:
            if s.has_data_flow:
                patterns = list({m.category for m in s.matches if m.data_role in ('source', 'target')})
                lines.append(f"{s.name}: data_patterns={patterns}")
        return "\n".join(lines) or "No data flows detected."

    if query == "function_body":
        lines = []
        for s in sections:
            lines.append(f"* {s.name} (line {s.lineno})\n{s.snippet}")
        return "\n\n".join(lines) or "Section not found."

    return f"Unknown query type: {query}. Use: imports, function_calls, data_flows, function_body"
