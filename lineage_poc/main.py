"""Main orchestrator: ties together AST pre-scan, agent sessions, and output."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from copilot.generated.session_events import AssistantMessageData, SessionIdleData

from lineage_poc.config.settings import Settings
from lineage_poc.schema.lineage_record import load_schema
from lineage_poc.skills.prescanner import PreScanner, DataFlowSnippet, WorkManifest
from lineage_poc.client.session import SessionFactory
from lineage_poc.tools.lineage_tools import (
    LineageOutputCollector,
    analyze_ast,
    build_emit_lineage_tool,
)
from lineage_poc.tools.progress import ProgressTracker
from lineage_poc.tools.csv_converter import convert_jsonl_to_csv

logger = logging.getLogger("lineage_poc")


SYSTEM_PROMPT_TEMPLATE = """\
You are a data lineage analyst. Your job is to analyze code snippets and identify
ALL data flows: where data comes from (source) and where it goes (target).

## Your task
For each code snippet you receive, identify every data source and data target.
Then call `emit_lineage_record()` once for EACH distinct source-to-target flow.

## Evidence chain requirement
The `dataOperationEvidence` field MUST contain EVERY intermediate transformation step
between source and target. This includes:
- Reading from a source (SELECT, API call, file read)
- Any .map(), .filter(), .groupBy(), .join() operations
- Type conversions or column renames
- Aggregations (SUM, COUNT, AVG)
- Writing to a target (INSERT, API call, file write)

Do NOT skip intermediate steps. Each step needs: file path, line number, code snippet, and description.

## Data source names
For data source and target names, use the identifier/variable name as it appears in code.
For example, if the code uses `userDatabase`, use that as the sourceDataSourceName.

## Output schema
{schema_description}

## Available tools
- `emit_lineage_record()`: Call this for each source-to-target data flow
- `analyze_ast()`: Use this if you need more context about what a function calls or who calls it

## Instructions
1. Read the code snippet carefully
2. Identify ALL data sources (where data is read from)
3. Identify ALL data targets (where data is written to)
4. Trace each transformation step between source and target
5. Call emit_lineage_record() for each flow with complete evidence chain
6. If a function calls another function that touches data, note it in the call chain
"""


def _snippet_key(snippet: DataFlowSnippet) -> str:
    return f"{snippet.file_path}::{snippet.class_name or ''}.{snippet.function_name}"


def _build_snippet_prompt(snippet: DataFlowSnippet) -> str:
    parts = [
        f"## Analyze this code for data lineage\n",
        f"**File:** {snippet.file_path}",
        f"**Function:** {snippet.class_name + '.' if snippet.class_name else ''}{snippet.function_name}",
        f"**Language:** {snippet.language}",
        f"**Detected patterns:** {', '.join(snippet.detected_patterns) or 'none'}",
        f"**Call chain:** {' → '.join(snippet.call_chain)}",
    ]

    if snippet.sql_strings:
        parts.append(f"**SQL found:** {'; '.join(snippet.sql_strings)}")

    parts.append(f"\n### Imports\n```\n{snippet.imports_context}\n```")
    parts.append(f"\n### Code\n```{snippet.language}\n{snippet.snippet}\n```")

    if snippet.callers_snippet:
        parts.append(f"\n### Callers (who calls this function)\n```\n{snippet.callers_snippet}\n```")

    if snippet.callees_snippet:
        parts.append(f"\n### Callees (what this function calls)\n```\n{snippet.callees_snippet}\n```")

    parts.append(
        "\nIdentify ALL data flows and call emit_lineage_record() for each one. "
        "Include every transformation step in the evidence chain."
    )

    return "\n".join(parts)


async def run_lineage_analysis(settings: Settings) -> Path:
    """Run the full lineage analysis pipeline. Returns path to CSV output."""

    output_dir = Path(settings.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "lineage_output.jsonl"
    csv_path = output_dir / "lineage_output.csv"
    progress_path = output_dir / "progress.json"

    # Load schema
    schema = load_schema(settings.schema_path)
    progress = ProgressTracker(progress_path)

    # ── Phase A: AST Pre-Scan ────────────────────────────────────
    logger.info("Phase A: Scanning repository with AST...")
    progress.phase = "ast_prescan"

    scanner = PreScanner()
    manifest = scanner.scan(settings.target_repo_path)

    logger.info(
        f"Found {manifest.total_snippets} data-touching snippets "
        f"in {manifest.total_files} files ({', '.join(manifest.languages)})"
    )
    progress.set_manifest_total(manifest.total_snippets)

    if manifest.total_snippets == 0:
        logger.warning("No data-touching code found in repository.")
        progress.phase = "complete"
        # Write empty CSV
        convert_jsonl_to_csv(jsonl_path, csv_path, schema)
        return csv_path

    # ── Phase B: LLM Analysis ────────────────────────────────────
    logger.info("Phase B: Analyzing snippets with LLM agent...")
    progress.phase = "llm_analysis"

    collector = LineageOutputCollector(jsonl_path)
    emit_tool = build_emit_lineage_tool(collector)

    # Detect primary language for schema
    language = manifest.languages[0] if len(manifest.languages) == 1 else None

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        schema_description=schema.to_json_schema_description(language)
    )

    session_factory = SessionFactory(settings)

    try:
        # Group snippets by file for session management
        snippets_by_file: dict[str, list[DataFlowSnippet]] = {}
        for snippet in manifest.snippets:
            snippets_by_file.setdefault(snippet.file_path, []).append(snippet)

        for file_path, file_snippets in snippets_by_file.items():
            logger.info(f"Processing {file_path} ({len(file_snippets)} snippets)")

            # New session per file for clean context
            session = await session_factory.create_session(
                system_message=system_prompt,
                tools=[emit_tool, analyze_ast],
            )

            try:
                for snippet in file_snippets:
                    key = _snippet_key(snippet)

                    if progress.is_processed(key):
                        logger.debug(f"  Skipping (already processed): {key}")
                        continue

                    logger.info(f"  Analyzing: {key}")
                    prompt = _build_snippet_prompt(snippet)
                    records_before = collector.count

                    try:
                        done = asyncio.Event()

                        def on_event(event):
                            match event.data:
                                case SessionIdleData():
                                    done.set()

                        session.on(on_event)
                        await session.send(prompt)
                        await asyncio.wait_for(done.wait(), timeout=120)

                        records_emitted = collector.count - records_before
                        progress.mark_processed(key, records_emitted)
                        logger.info(f"    → {records_emitted} records emitted. {progress.summary()}")

                    except asyncio.TimeoutError:
                        progress.mark_failed(key, "timeout")
                        logger.warning(f"    → Timeout for {key}")
                    except Exception as e:
                        progress.mark_failed(key, str(e))
                        logger.warning(f"    → Error for {key}: {e}")

            finally:
                await session.disconnect()

    finally:
        await session_factory.stop()

    # ── Phase C: Finalize ────────────────────────────────────────
    logger.info("Phase C: Converting to CSV...")
    progress.phase = "csv_conversion"

    record_count = convert_jsonl_to_csv(jsonl_path, csv_path, schema, language)
    progress.phase = "complete"

    logger.info(f"Done! {record_count} lineage records written to {csv_path}")
    logger.info(progress.summary())

    return csv_path


# ── CLI Entry Point ──────────────────────────────────────────────

def cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Data Lineage Tracker")
    parser.add_argument("repo_path", help="Path to the repository to analyze")
    parser.add_argument("--output", "-o", help="Output directory", default=None)
    parser.add_argument("--schema", help="Path to lineage_schema.yaml", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = Settings.from_env()
    settings.target_repo_path = args.repo_path

    if args.output:
        settings.output_dir = args.output
    if args.schema:
        settings.schema_path = args.schema

    errors = settings.validate()
    if errors:
        for e in errors:
            logger.error(e)
        sys.exit(1)

    csv_path = asyncio.run(run_lineage_analysis(settings))
    print(f"\nLineage output: {csv_path}")


if __name__ == "__main__":
    cli()
