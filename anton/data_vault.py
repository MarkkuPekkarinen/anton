"""Local Vault — stores data source connection credentials.

Each connection is saved as a JSON file in ~/.anton/data_vault/.
Files are named {engine}-{connection_name} and contain the credential
fields in plain text. Files are only read at runtime when Anton needs
to establish or test a connection.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def _sanitize(value: str) -> str:
    """Strip characters unsafe for file names, keep alphanumeric, dash, underscore."""
    return re.sub(r"[^\w\-]", "_", value).strip("_")


class DataVault:
    """Manages data source connection credentials in ~/.anton/data_vault/."""

    def __init__(self, vault_dir: Path | None = None) -> None:
        self._dir = vault_dir or Path("~/.anton/data_vault").expanduser()


    def _path_for(self, engine: str, name: str) -> Path:
        return self._dir / f"{_sanitize(engine)}-{_sanitize(name)}"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._dir.chmod(0o700)


    def save(self, engine: str, name: str, credentials: dict[str, str]) -> Path:
        """Write credentials as JSON atomically. Creates vault dir if needed."""
        self._ensure_dir()
        path = self._path_for(engine, name)
        data = {
            "engine": engine,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "fields": credentials,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.rename(path)
        return path

    def load(self, engine: str, name: str) -> dict[str, str] | None:
        """Return the fields dict for a connection, or None if not found."""
        path = self._path_for(engine, name)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("fields", {})
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self, engine: str, name: str) -> bool:
        """Remove a connection file. Returns True if it existed."""
        path = self._path_for(engine, name)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_connections(self) -> list[dict[str, str]]:
        """Return [{engine, name, created_at}] for all stored connections."""
        if not self._dir.is_dir():
            return []
        results: list[dict[str, str]] = []
        for path in sorted(self._dir.iterdir()):
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "engine": data.get("engine", ""),
                    "name": data.get("name", ""),
                    "created_at": data.get("created_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results


    def inject_env(self, engine: str, name: str) -> list[str] | None:
        """Load credentials and set DS_<FIELD_UPPER> in os.environ.

        Returns the list of env var names set, or None if connection not found.
        Call this before a scratchpad exec that needs the data source.
        """
        fields = self.load(engine, name)
        if fields is None:
            return None
        var_names: list[str] = []
        for key, value in fields.items():
            var = f"DS_{key.upper()}"
            os.environ[var] = value
            var_names.append(var)
        return var_names

    def clear_ds_env(self) -> None:
        """Remove all DS_* variables from os.environ."""
        ds_keys = [k for k in os.environ if k.startswith("DS_")]
        for key in ds_keys:
            del os.environ[key]


    def next_connection_number(self, engine: str) -> int:
        """Return the next auto-increment number for an engine (1-based).

        Used when naming partial connections: postgresql-1, postgresql-2, etc.
        """
        prefix = _sanitize(engine) + "-"
        if not self._dir.is_dir():
            return 1
        existing = [
            p.name for p in self._dir.iterdir()
            if p.is_file() and p.name.startswith(prefix)
        ]
        max_n = 0
        for fname in existing:
            suffix = fname[len(prefix):]
            if suffix.isdigit():
                max_n = max(max_n, int(suffix))
        return max_n + 1
