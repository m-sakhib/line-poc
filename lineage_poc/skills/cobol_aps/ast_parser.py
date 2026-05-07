"""COBOL/APS regex-based parser for data lineage detection.

APS (Micro Focus Advanced Programming Software for z/OS) uses proprietary
macro syntax instead of standard COBOL divisions. This parser uses regex
patterns to extract data-touching constructs.

Pattern Registry Design:
- All patterns are stored in PATTERN_REGISTRY and can be updated once
  a real .aps sample is provided.
- Each pattern has: name, compiled regex, description, and data_role
  (source/target/transform/call).
- Patterns marked with # TODO need refinement after sample review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


# ── Pattern Registry ─────────────────────────────────────────────────

@dataclass
class APSPattern:
    """A regex pattern for detecting APS constructs."""

    name: str
    regex: re.Pattern
    description: str
    data_role: Literal["source", "target", "transform", "call", "definition", "io"]
    category: str  # e.g., "file_io", "subprogram", "record_def", "copy"


# TODO: All patterns below are placeholder approximations based on known
# APS keywords (SELECT, FD, REC, CALL, COPY, IO SELECT, field, table).
# These MUST be refined once a real .aps sample is available.

PATTERN_REGISTRY: list[APSPattern] = [
    # ── File I/O declarations ──
    APSPattern(
        name="io_select",
        regex=re.compile(
            r"^\s*(IO\s+)?SELECT\s+(\S+)\s+ASSIGN\s+(?:TO\s+)?(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="File SELECT/IO SELECT assignment — declares a logical file",
        data_role="definition",
        category="file_io",
    ),
    APSPattern(
        name="fd_declaration",
        regex=re.compile(
            r"^\s*FD\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="File Description — defines record layout for a file",
        data_role="definition",
        category="file_io",
    ),

    # ── Record / field definitions ──
    APSPattern(
        name="record_definition",
        regex=re.compile(
            r"^\s*(?:01|REC)\s+(\S+)\s*\.",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Record definition (01 level or REC keyword)",
        data_role="definition",
        category="record_def",
    ),
    APSPattern(
        name="field_definition",
        regex=re.compile(
            r"^\s*(?:05|10|15|20|25|field)\s+(\S+)\s+PIC\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Field definition with PIC clause",
        data_role="definition",
        category="record_def",
    ),

    # ── File READ/WRITE operations ──
    APSPattern(
        name="file_read",
        regex=re.compile(
            r"^\s*READ\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Read from a file (VSAM or sequential)",
        data_role="source",
        category="file_io",
    ),
    APSPattern(
        name="file_write",
        regex=re.compile(
            r"^\s*WRITE\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Write to a file",
        data_role="target",
        category="file_io",
    ),
    APSPattern(
        name="file_rewrite",
        regex=re.compile(
            r"^\s*REWRITE\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Rewrite (update) a record in a file",
        data_role="target",
        category="file_io",
    ),
    APSPattern(
        name="file_delete",
        regex=re.compile(
            r"^\s*DELETE\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Delete a record from a file",
        data_role="target",
        category="file_io",
    ),

    # ── Subprogram calls ──
    APSPattern(
        name="call_subprogram",
        regex=re.compile(
            r"^\s*CALL\s+['\"]?(\S+?)['\"]?\s+USING\s+(.*?)(?:\.|$)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Call to subprogram with data passed via USING",
        data_role="call",
        category="subprogram",
    ),
    APSPattern(
        name="call_simple",
        regex=re.compile(
            r"^\s*CALL\s+['\"]?(\S+?)['\"]?\s*\.",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Simple call to subprogram (no USING clause)",
        data_role="call",
        category="subprogram",
    ),

    # ── COPY / INCLUDE (external copybook references) ──
    APSPattern(
        name="copy_statement",
        regex=re.compile(
            r"^\s*COPY\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Copy an external copybook (data structure definition)",
        data_role="definition",
        category="copy",
    ),

    # ── Data movement / transformation ──
    APSPattern(
        name="move_statement",
        regex=re.compile(
            r"^\s*MOVE\s+(\S+)\s+TO\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Move data from one field to another (data transformation)",
        data_role="transform",
        category="data_movement",
    ),

    # ── Table/array references ──
    APSPattern(
        name="table_reference",
        regex=re.compile(
            r"^\s*(?:table|OCCURS)\s+(\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        description="Table/array definition or reference",
        data_role="definition",
        category="record_def",
    ),
]


# ── Data classes for analysis results ────────────────────────────────

@dataclass
class APSMatch:
    """A single regex match in an APS file."""

    pattern_name: str
    category: str
    data_role: str
    lineno: int
    matched_text: str
    groups: tuple  # Captured groups from regex
    description: str


@dataclass
class APSSectionInfo:
    """A logical section/paragraph in the APS file."""

    name: str
    lineno: int
    end_lineno: int
    snippet: str
    matches: list[APSMatch]
    has_data_flow: bool


@dataclass
class APSFileInfo:
    """A file-level declaration (SELECT/FD)."""

    logical_name: str
    physical_name: str | None
    record_name: str | None
    fields: list[dict]  # [{"name": "CUST-ID", "pic": "9(8)"}, ...]
    lineno: int


@dataclass
class APSCallInfo:
    """A subprogram call."""

    target_program: str
    using_params: list[str]
    lineno: int


@dataclass
class APSCopyInfo:
    """A COPY/INCLUDE reference."""

    copybook_name: str
    lineno: int


@dataclass
class APSFileAnalysis:
    """Complete analysis of a .aps file."""

    file_path: str
    lines_total: int
    sections: list[APSSectionInfo]
    file_declarations: list[APSFileInfo]
    calls: list[APSCallInfo]
    copies: list[APSCopyInfo]
    all_matches: list[APSMatch]
    records: list[dict]  # Record/field hierarchy


# ── Parser ───────────────────────────────────────────────────────────

class APSParser:
    """Parses .aps files using regex pattern matching.

    This is a best-effort structural parser for proprietary APS syntax.
    It identifies data-touching sections that the LLM agent will interpret.
    """

    def __init__(self, extra_patterns: list[APSPattern] | None = None) -> None:
        self._patterns = list(PATTERN_REGISTRY)
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    def parse_file(self, file_path: str | Path) -> APSFileAnalysis:
        file_path = Path(file_path)
        source = file_path.read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()

        # Run all patterns against the source
        all_matches = self._find_all_matches(source, lines)

        # Extract structured info
        file_declarations = self._extract_file_declarations(all_matches, lines)
        calls = self._extract_calls(all_matches)
        copies = self._extract_copies(all_matches)
        records = self._extract_records(all_matches, lines)
        sections = self._identify_sections(lines, all_matches)

        return APSFileAnalysis(
            file_path=str(file_path),
            lines_total=len(lines),
            sections=sections,
            file_declarations=file_declarations,
            calls=calls,
            copies=copies,
            all_matches=all_matches,
            records=records,
        )

    def get_data_touching_sections(self, file_path: str | Path) -> list[APSSectionInfo]:
        """Return only sections that have data flow operations."""
        analysis = self.parse_file(file_path)
        return [s for s in analysis.sections if s.has_data_flow]

    def _find_all_matches(self, source: str, lines: list[str]) -> list[APSMatch]:
        """Run all patterns against the source and collect matches."""
        matches = []
        for pattern in self._patterns:
            for m in pattern.regex.finditer(source):
                # Calculate line number from match position
                lineno = source[:m.start()].count("\n") + 1
                matches.append(APSMatch(
                    pattern_name=pattern.name,
                    category=pattern.category,
                    data_role=pattern.data_role,
                    lineno=lineno,
                    matched_text=m.group(0).strip(),
                    groups=m.groups(),
                    description=pattern.description,
                ))
        # Sort by line number
        matches.sort(key=lambda x: x.lineno)
        return matches

    def _extract_file_declarations(
        self, matches: list[APSMatch], lines: list[str]
    ) -> list[APSFileInfo]:
        """Extract file SELECT/FD pairs."""
        files: dict[str, APSFileInfo] = {}

        for m in matches:
            if m.pattern_name == "io_select" and m.groups:
                logical_name = m.groups[1] if len(m.groups) > 1 else m.groups[0]
                physical_name = m.groups[2] if len(m.groups) > 2 else None
                # Clean up quotes/punctuation
                logical_name = logical_name.strip(".'\"")
                if physical_name:
                    physical_name = physical_name.strip(".'\"")
                files[logical_name] = APSFileInfo(
                    logical_name=logical_name,
                    physical_name=physical_name,
                    record_name=None,
                    fields=[],
                    lineno=m.lineno,
                )
            elif m.pattern_name == "fd_declaration" and m.groups:
                fd_name = m.groups[0].strip(".'\"")
                if fd_name in files:
                    pass  # Already have it
                else:
                    files[fd_name] = APSFileInfo(
                        logical_name=fd_name,
                        physical_name=None,
                        record_name=None,
                        fields=[],
                        lineno=m.lineno,
                    )

        return list(files.values())

    def _extract_calls(self, matches: list[APSMatch]) -> list[APSCallInfo]:
        """Extract subprogram CALL statements."""
        calls = []
        for m in matches:
            if m.pattern_name == "call_subprogram" and m.groups:
                target = m.groups[0].strip(".'\"")
                using_raw = m.groups[1] if len(m.groups) > 1 else ""
                params = [p.strip() for p in using_raw.split() if p.strip()]
                calls.append(APSCallInfo(
                    target_program=target,
                    using_params=params,
                    lineno=m.lineno,
                ))
            elif m.pattern_name == "call_simple" and m.groups:
                target = m.groups[0].strip(".'\"")
                calls.append(APSCallInfo(
                    target_program=target,
                    using_params=[],
                    lineno=m.lineno,
                ))
        return calls

    def _extract_copies(self, matches: list[APSMatch]) -> list[APSCopyInfo]:
        """Extract COPY/INCLUDE statements."""
        copies = []
        for m in matches:
            if m.pattern_name == "copy_statement" and m.groups:
                name = m.groups[0].strip(".'\"")
                copies.append(APSCopyInfo(copybook_name=name, lineno=m.lineno))
        return copies

    def _extract_records(self, matches: list[APSMatch], lines: list[str]) -> list[dict]:
        """Extract record/field definitions as a hierarchy."""
        records = []
        current_record: dict | None = None

        for m in matches:
            if m.pattern_name == "record_definition" and m.groups:
                if current_record:
                    records.append(current_record)
                current_record = {
                    "name": m.groups[0].strip(".'\""),
                    "lineno": m.lineno,
                    "fields": [],
                }
            elif m.pattern_name == "field_definition" and m.groups and current_record:
                current_record["fields"].append({
                    "name": m.groups[0].strip(".'\""),
                    "pic": m.groups[1].strip(".'\"") if len(m.groups) > 1 else None,
                    "lineno": m.lineno,
                })

        if current_record:
            records.append(current_record)
        return records

    def _identify_sections(
        self, lines: list[str], matches: list[APSMatch]
    ) -> list[APSSectionInfo]:
        """Identify logical sections/paragraphs in the APS file.

        TODO: APS section boundaries need refinement based on real syntax.
        Current heuristic: split on lines that look like section headers
        (all-caps label ending with a period or SECTION keyword).
        """
        # Heuristic: look for section-like boundaries
        section_pattern = re.compile(
            r"^(\s{0,6})([A-Z][A-Z0-9-]+)\s*(SECTION\s*\.?|\.)\s*$"
        )

        section_starts: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            m = section_pattern.match(line)
            if m:
                section_starts.append((i + 1, m.group(2)))

        if not section_starts:
            # Treat entire file as one section
            snippet = "\n".join(lines)
            data_matches = [m for m in matches if m.data_role in ("source", "target", "call")]
            return [APSSectionInfo(
                name="MAIN",
                lineno=1,
                end_lineno=len(lines),
                snippet=snippet if len(lines) <= 100 else "\n".join(lines[:100]) + "\n... (truncated)",
                matches=matches,
                has_data_flow=bool(data_matches),
            )]

        sections = []
        for idx, (start_line, name) in enumerate(section_starts):
            end_line = (
                section_starts[idx + 1][0] - 1
                if idx + 1 < len(section_starts)
                else len(lines)
            )
            snippet_lines = lines[start_line - 1 : end_line]
            snippet = "\n".join(snippet_lines)

            section_matches = [
                m for m in matches if start_line <= m.lineno <= end_line
            ]
            data_matches = [
                m for m in section_matches if m.data_role in ("source", "target", "call")
            ]

            sections.append(APSSectionInfo(
                name=name,
                lineno=start_line,
                end_lineno=end_line,
                snippet=snippet if len(snippet_lines) <= 80 else "\n".join(snippet_lines[:80]) + "\n... (truncated)",
                matches=section_matches,
                has_data_flow=bool(data_matches),
            ))

        return sections
