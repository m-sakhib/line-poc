"""Pydantic models for lineage records and evidence chains."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class EvidenceStep(BaseModel):
    step: int = Field(description="Ordered step number in the transformation chain")
    file: str = Field(description="Source file path")
    line: int = Field(description="Line number in the source file")
    code: str = Field(description="Code snippet for this step")
    description: str = Field(description="Human-readable description of the transformation")


class LineageRecord(BaseModel):
    """Base lineage record with all configurable fields.

    Required fields: sourceTechnologyType, sourceEntityName,
    targetTechnologyType, targetEntityName, dataOperationEvidence.
    All others are optional.
    """

    sourceAppID: str | None = None
    sourceDataSourceName: str | None = None
    sourceTechnologyType: str
    sourceSchemaType: str | None = None
    sourceSchemaName: str | None = None
    sourceEntityType: str | None = None
    sourceEntityName: str
    sourceColumnDataType: str | None = None
    sourceColumnName: str | None = None
    targetAppID: str | None = None
    targetDataTargetName: str | None = None
    targetTechnologyType: str
    targetSchemaType: str | None = None
    targetSchemaName: str | None = None
    targetEntityType: str | None = None
    targetEntityName: str
    targetColumnDataType: str | None = None
    targetColumnName: str | None = None
    dataOperationDate: str | None = None
    dataOperationType: str | None = None
    dataOperationName: str | None = None
    dataOperationDescription: str | None = None
    dataOperationEvidence: list[EvidenceStep] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def set_operation_date(cls, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("dataOperationDate"):
            data["dataOperationDate"] = datetime.now(timezone.utc).isoformat()
        return data


class PythonLineageRecord(LineageRecord):
    """Extended record with Python-specific fields."""

    sourcePandasOperation: str | None = None
    sourceORMModel: str | None = None


class JavaLineageRecord(LineageRecord):
    """Extended record with Java-specific fields."""

    sourceJDBCDriver: str | None = None
    sourceSpringRepository: str | None = None
    sourceJPAEntity: str | None = None


# ---------- Schema loader ----------

class LineageFieldDef(BaseModel):
    name: str
    type: str
    required: bool = False
    description: str = ""


class LineageSchema(BaseModel):
    fields: list[LineageFieldDef]
    language_overrides: dict[str, dict[str, list[LineageFieldDef]]] = {}

    def all_field_names(self, language: str | None = None) -> list[str]:
        names = [f.name for f in self.fields]
        if language and language in self.language_overrides:
            extras = self.language_overrides[language].get("extra_fields", [])
            names.extend(f.name for f in extras)
        return names

    def required_field_names(self) -> list[str]:
        return [f.name for f in self.fields if f.required]

    def to_json_schema_description(self, language: str | None = None) -> str:
        """Produce a human-readable field list for the LLM system prompt."""
        lines = []
        for f in self.fields:
            req = "REQUIRED" if f.required else "optional"
            lines.append(f"- {f.name} ({f.type}, {req}): {f.description}")
        if language and language in self.language_overrides:
            extras = self.language_overrides[language].get("extra_fields", [])
            for f in extras:
                req = "REQUIRED" if f.required else "optional"
                lines.append(f"- {f.name} ({f.type}, {req}): {f.description}")
        return "\n".join(lines)

    def get_record_class(self, language: str | None = None) -> type[LineageRecord]:
        if language == "python":
            return PythonLineageRecord
        elif language == "java":
            return JavaLineageRecord
        return LineageRecord


def load_schema(schema_path: str | Path) -> LineageSchema:
    with open(schema_path) as f:
        raw = yaml.safe_load(f)

    overrides = {}
    for lang, lang_data in raw.get("language_overrides", {}).items():
        overrides[lang] = {
            "extra_fields": [LineageFieldDef(**fd) for fd in lang_data.get("extra_fields", [])]
        }

    return LineageSchema(
        fields=[LineageFieldDef(**fd) for fd in raw["fields"]],
        language_overrides=overrides,
    )
