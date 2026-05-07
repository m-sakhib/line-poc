"""Verification layer for lineage records.

Scores confidence and validates evidence chains before accepting records.
Three-tier confidence:
  HIGH   — parser-detected pattern matches the claim
  MEDIUM — agent-reported + cross-validated (e.g., entity exists in repo)
  LOW    — agent-only claim, no parser or file evidence
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class VerificationResult:
    """Result of verifying a single lineage record."""

    confidence: ConfidenceLevel
    issues: list[str]
    verified_evidence_lines: int
    total_evidence_lines: int
    entity_exists: bool | None  # None if check not applicable
    needs_review: bool


class LineageVerifier:
    """Verifies lineage records against source files and parser results.

    Usage:
        verifier = LineageVerifier(repo_path)
        result = verifier.verify(record_dict, parser_matches)
    """

    def __init__(self, repo_path: str | Path) -> None:
        self._repo_path = Path(repo_path)
        # Cache of file contents: path -> lines
        self._file_cache: dict[str, list[str]] = {}

    def verify(
        self,
        record: dict,
        parser_detected_patterns: list[str] | None = None,
    ) -> VerificationResult:
        """Verify a lineage record and score confidence.

        Args:
            record: The lineage record dict (from emit_lineage_record tool).
            parser_detected_patterns: Patterns the AST/regex parser found for
                the function/section this record came from.

        Returns:
            VerificationResult with confidence and any issues.
        """
        issues: list[str] = []

        # 1. Verify evidence lines exist in source files
        verified_lines, total_lines = self._verify_evidence_lines(record, issues)

        # 2. Verify that source/target entities exist in the repo
        entity_exists = self._verify_entities_exist(record, issues)

        # 3. Score confidence
        confidence = self._score_confidence(
            record=record,
            parser_detected_patterns=parser_detected_patterns or [],
            verified_lines=verified_lines,
            total_lines=total_lines,
            entity_exists=entity_exists,
        )

        needs_review = confidence == "LOW" or bool(issues)

        return VerificationResult(
            confidence=confidence,
            issues=issues,
            verified_evidence_lines=verified_lines,
            total_evidence_lines=total_lines,
            entity_exists=entity_exists,
            needs_review=needs_review,
        )

    def _verify_evidence_lines(
        self, record: dict, issues: list[str]
    ) -> tuple[int, int]:
        """Check that evidence chain lines actually exist in source files.

        Returns (verified_count, total_count).
        """
        evidence_chain = record.get("dataOperationEvidence", "")
        if not evidence_chain:
            issues.append("Missing dataOperationEvidence field")
            return (0, 0)

        # Parse evidence chain steps: look for [file:line] patterns
        import re

        step_pattern = re.compile(r"\[([^:\]]+):(\d+)\]")
        matches = step_pattern.findall(evidence_chain)

        if not matches:
            # No file:line references in evidence
            issues.append("Evidence chain has no file:line references")
            return (0, 1)

        verified = 0
        total = len(matches)

        for file_ref, line_str in matches:
            line_no = int(line_str)
            lines = self._get_file_lines(file_ref)

            if lines is None:
                issues.append(f"Evidence references file '{file_ref}' which was not found in repo")
                continue

            if line_no < 1 or line_no > len(lines):
                issues.append(
                    f"Evidence references line {line_no} in '{file_ref}' "
                    f"but file only has {len(lines)} lines"
                )
                continue

            verified += 1

        return (verified, total)

    def _verify_entities_exist(
        self, record: dict, issues: list[str]
    ) -> bool | None:
        """Check that referenced entities (files, tables, programs) exist.

        For files: check if something matching the entity name is in the repo.
        For subprograms: check if a .aps/.cbl/.py/.java file with that name exists.
        """
        source_entity = record.get("sourceEntityName", "")
        target_entity = record.get("targetEntityName", "")

        if not source_entity and not target_entity:
            return None  # Nothing to verify

        found_any = False
        checked = False

        for entity in [source_entity, target_entity]:
            if not entity:
                continue
            checked = True
            # Check if entity matches a file or program in repo
            if self._entity_exists_in_repo(entity):
                found_any = True
            else:
                # Not necessarily an issue — entity could be an external table/service
                pass

        if not checked:
            return None
        return found_any

    def _entity_exists_in_repo(self, entity_name: str) -> bool:
        """Check if an entity name maps to something in the repository."""
        if not entity_name:
            return False

        # Normalize: strip quotes, parens, suffixes like "(subprogram)"
        clean = entity_name.strip("'\"").split("(")[0].strip()

        # Check for file match (case-insensitive)
        clean_lower = clean.lower()
        for ext in (".aps", ".cbl", ".cob", ".py", ".java", ".sql"):
            matches = list(self._repo_path.rglob(f"*{ext}"))
            for f in matches:
                if f.stem.lower() == clean_lower:
                    return True

        # Check if the entity name appears in any file content (grep)
        # This is expensive so we skip it — rely on file-name matching
        return False

    def _score_confidence(
        self,
        record: dict,
        parser_detected_patterns: list[str],
        verified_lines: int,
        total_lines: int,
        entity_exists: bool | None,
    ) -> ConfidenceLevel:
        """Score confidence based on multiple signals.

        HIGH: Parser detected the pattern AND evidence lines verified.
        MEDIUM: Agent found it + some cross-validation (entity exists or lines match).
        LOW: Agent claim only, no supporting evidence.
        """
        has_parser_match = bool(parser_detected_patterns)
        has_verified_evidence = verified_lines > 0 and verified_lines == total_lines
        has_partial_evidence = verified_lines > 0

        # HIGH: parser confirms + evidence checks out
        if has_parser_match and has_verified_evidence:
            return "HIGH"

        # HIGH: parser confirms + most evidence checks out
        if has_parser_match and has_partial_evidence and total_lines > 0:
            ratio = verified_lines / total_lines
            if ratio >= 0.8:
                return "HIGH"

        # MEDIUM: parser confirms OR evidence fully checks out
        if has_parser_match:
            return "MEDIUM"
        if has_verified_evidence and entity_exists:
            return "MEDIUM"

        # MEDIUM: partial evidence + entity exists
        if has_partial_evidence and entity_exists:
            return "MEDIUM"

        # LOW: no parser, no verified evidence
        return "LOW"

    def _get_file_lines(self, file_ref: str) -> list[str] | None:
        """Load file lines from cache or disk.

        Tries to match the file_ref against files in the repo.
        """
        if file_ref in self._file_cache:
            return self._file_cache[file_ref]

        # Try as relative path
        candidate = self._repo_path / file_ref
        if candidate.is_file():
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            self._file_cache[file_ref] = lines
            return lines

        # Try matching just the filename
        name = Path(file_ref).name
        for f in self._repo_path.rglob(name):
            if f.is_file():
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                self._file_cache[file_ref] = lines
                return lines

        return None
