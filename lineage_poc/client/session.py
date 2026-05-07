"""Copilot SDK session factory with Azure Foundry BYOK."""

from __future__ import annotations

import asyncio
from pathlib import Path

from copilot import CopilotClient, SubprocessConfig
from copilot.generated.session_events import (
    AssistantMessageData,
    SessionIdleData,
)
from copilot.session import PermissionHandler, PermissionRequest, PermissionRequestResult

from lineage_poc.config.settings import Settings


def _read_only_permission_handler(
    request: PermissionRequest, invocation: dict
) -> PermissionRequestResult:
    """Allow reads and custom tools only. Deny shell and write."""
    kind = request.kind.value if hasattr(request.kind, "value") else str(request.kind)
    if kind in ("read", "custom-tool", "memory"):
        return PermissionRequestResult(kind="approved")
    # Deny everything else (shell, write, url, mcp, hook)
    return PermissionRequestResult(kind="denied-by-rules")


class SessionFactory:
    """Creates Copilot SDK sessions with Azure BYOK provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: CopilotClient | None = None

    async def start_client(self) -> CopilotClient:
        if self._client is not None:
            return self._client

        config = SubprocessConfig(
            cwd=self._settings.target_repo_path,
            use_stdio=True,
        )
        self._client = CopilotClient(config, auto_start=True)
        await self._client.start()
        return self._client

    async def create_session(
        self,
        system_message: str,
        tools: list | None = None,
    ):
        """Create a new session with Azure BYOK provider."""
        client = await self.start_client()

        provider = {
            "type": "azure",
            "base_url": self._settings.azure_endpoint,
            "api_key": self._settings.azure_api_key,
            "azure": {
                "api_version": self._settings.azure_api_version,
            },
        }

        session = await client.create_session(
            model=self._settings.model_name,
            provider=provider,
            on_permission_request=_read_only_permission_handler,
            tools=tools or [],
            system_message={"content": system_message},
            streaming=False,
            infinite_sessions={"enabled": False},  # We manage context ourselves
        )
        return session

    async def stop(self) -> None:
        if self._client:
            await self._client.stop()
            self._client = None
