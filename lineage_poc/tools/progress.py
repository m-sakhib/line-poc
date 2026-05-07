"""Progress tracker for resumable processing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProgressTracker:
    """Tracks which snippets have been processed. Supports resume."""

    def __init__(self, progress_path: str | Path) -> None:
        self.path = Path(progress_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "manifest_total": 0,
            "snippets_processed": 0,
            "snippets_failed": 0,
            "records_emitted": 0,
            "processed_keys": [],
            "failed_snippets": [],
            "phase": "not_started",
        }

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    @property
    def phase(self) -> str:
        return self._data.get("phase", "not_started")

    @phase.setter
    def phase(self, value: str) -> None:
        self._data["phase"] = value
        self._save()

    @property
    def snippets_processed(self) -> int:
        return self._data.get("snippets_processed", 0)

    @property
    def manifest_total(self) -> int:
        return self._data.get("manifest_total", 0)

    def set_manifest_total(self, total: int) -> None:
        self._data["manifest_total"] = total
        self._save()

    def is_processed(self, snippet_key: str) -> bool:
        return snippet_key in self._data.get("processed_keys", [])

    def mark_processed(self, snippet_key: str, records_emitted: int) -> None:
        self._data.setdefault("processed_keys", []).append(snippet_key)
        self._data["snippets_processed"] = len(self._data["processed_keys"])
        self._data["records_emitted"] = self._data.get("records_emitted", 0) + records_emitted
        self._save()

    def mark_failed(self, snippet_key: str, error: str) -> None:
        self._data.setdefault("failed_snippets", []).append({
            "key": snippet_key,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._data["snippets_failed"] = len(self._data["failed_snippets"])
        self._save()

    def is_complete(self) -> bool:
        total = self._data.get("manifest_total", 0)
        processed = self._data.get("snippets_processed", 0)
        failed = self._data.get("snippets_failed", 0)
        return total > 0 and (processed + failed) >= total

    def progress_pct(self) -> float:
        total = self._data.get("manifest_total", 0)
        if total == 0:
            return 0.0
        done = self._data.get("snippets_processed", 0) + self._data.get("snippets_failed", 0)
        return round(done / total * 100, 1)

    def summary(self) -> str:
        return (
            f"Progress: {self.progress_pct()}% "
            f"({self._data.get('snippets_processed', 0)}/{self._data.get('manifest_total', 0)} processed, "
            f"{self._data.get('snippets_failed', 0)} failed, "
            f"{self._data.get('records_emitted', 0)} records emitted)"
        )
