"""Errors the Dokploy client raises. Tools turn these into MCP tool errors."""


class DokployError(Exception):
    """The Dokploy API rejected a call, or the local config cannot make one."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
