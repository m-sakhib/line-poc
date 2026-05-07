"""CSV converter: transforms JSONL lineage output to CSV at 100% completion."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from lineage_poc.schema.lineage_record import LineageSchema


def convert_jsonl_to_csv(
    jsonl_path: str | Path,
    csv_path: str | Path,
    schema: LineageSchema,
    language: str | None = None,
) -> int:
    """Convert JSONL lineage records to CSV.

    Returns the number of records written.
    """
    jsonl_path = Path(jsonl_path)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        pd.DataFrame(columns=schema.all_field_names(language)).to_csv(csv_path, index=False)
        return 0

    # Flatten evidence chain to readable string
    for r in records:
        evidence = r.get("dataOperationEvidence")
        if isinstance(evidence, list):
            steps = []
            for e in evidence:
                if isinstance(e, dict):
                    file_ref = f"[{e.get('file', '?')}:{e.get('line', '?')}]"
                    desc = e.get("description", "")
                    code = e.get("code", "")
                    steps.append(f"{file_ref} {desc} | {code}")
            r["dataOperationEvidence"] = " -> ".join(steps)

    df = pd.DataFrame(records)

    # Ensure all configured columns exist
    all_fields = schema.all_field_names(language)
    for field_name in all_fields:
        if field_name not in df.columns:
            df[field_name] = ""

    # Reorder columns to match schema, drop extra columns
    df = df[[c for c in all_fields if c in df.columns]]
    df.to_csv(csv_path, index=False)
    return len(records)
