"""Merge KEY=VALUE updates into the env string Dokploy stores."""

from __future__ import annotations

import re

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLAIN = re.compile(r"^[A-Za-z0-9_./:@%+-]*$")


class EnvFileError(ValueError):
    """A variable name or value cannot be stored in a Dokploy env string."""


def validate_key(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise EnvFileError(
            f"Environment variable name {key!r} is not a valid identifier. "
            "Use letters, digits, and underscores, and do not start with a digit."
        )
    return key


def validate_value(key: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise EnvFileError(f"Value for {key} cannot contain a newline.")
    return value


def merge_env(
    current: str | None,
    *,
    replacement: str | None = None,
    variables: dict[str, str] | None = None,
    unset: list[str] | None = None,
) -> str:
    """Return the env string to save.

    `replacement` is the full file. `variables` overwrites keys. `unset` removes
    keys. Variables and removals apply on top of the replacement when both are set,
    otherwise on top of `current`.
    """
    base = current if replacement is None else replacement
    text = base or ""
    updates = dict(variables or {})
    for key, value in updates.items():
        validate_key(key)
        validate_value(key, value)
    removals = {validate_key(key) for key in (unset or [])}
    if not updates and not removals:
        return text

    pending = {key: value for key, value in updates.items() if key not in removals}
    written: list[str] = []
    for line in text.splitlines():
        assignment = _assignment_key(line)
        if assignment is None:
            written.append(line)
            continue
        key = assignment
        if key in removals:
            continue
        if key in pending:
            written.append(format_assignment(key, pending.pop(key)))
            continue
        written.append(line)
    for key, value in pending.items():
        written.append(format_assignment(key, value))

    if not written:
        return ""
    body = "\n".join(written)
    if text.endswith("\n") or updates or removals:
        return body + "\n"
    return body


def format_assignment(key: str, value: str) -> str:
    if value == "" or _PLAIN.fullmatch(value):
        return f"{key}={value}"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def _assignment_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    body = stripped[7:].strip() if stripped.startswith("export ") else stripped
    if "=" not in body:
        return None
    key, _value = body.split("=", 1)
    key = key.strip()
    if not _KEY.fullmatch(key):
        return None
    return key
