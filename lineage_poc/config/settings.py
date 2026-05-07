"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    # Azure Foundry BYOK
    azure_endpoint: str = ""
    azure_api_key: str = ""
    azure_api_version: str = "2024-10-21"
    model_name: str = "gpt5.1"

    # Target repository
    target_repo_path: str = ""

    # Paths
    skills_dir: str = ""
    schema_path: str = ""
    output_dir: str = ""

    # Processing
    max_retries: int = 2
    snippets_per_session_turn: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        project_root = Path(__file__).resolve().parent.parent.parent
        return cls(
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
            azure_api_key=os.environ.get("AZURE_OPENAI_KEY", ""),
            azure_api_version=os.environ.get("AZURE_API_VERSION", "2024-10-21"),
            model_name=os.environ.get("MODEL_NAME", "gpt5.1"),
            target_repo_path=os.environ.get("TARGET_REPO_PATH", ""),
            skills_dir=os.environ.get("SKILLS_DIR", str(project_root / "lineage_poc" / "skills")),
            schema_path=os.environ.get("SCHEMA_PATH", str(project_root / "config" / "lineage_schema.yaml")),
            output_dir=os.environ.get("OUTPUT_DIR", str(project_root / "output")),
            max_retries=int(os.environ.get("MAX_RETRIES", "2")),
            snippets_per_session_turn=int(os.environ.get("SNIPPETS_PER_TURN", "10")),
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.azure_endpoint:
            errors.append("AZURE_OPENAI_ENDPOINT is required")
        if not self.azure_api_key:
            errors.append("AZURE_OPENAI_KEY is required")
        if not self.target_repo_path:
            errors.append("TARGET_REPO_PATH is required")
        elif not Path(self.target_repo_path).is_dir():
            errors.append(f"TARGET_REPO_PATH does not exist: {self.target_repo_path}")
        return errors
