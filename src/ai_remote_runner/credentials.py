from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


class CredentialBroker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.index_path = self.root / "index.json"

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _save_index(self, data: dict[str, Any]) -> None:
        self.index_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(self.index_path, 0o600)

    def add_local_secret(self, metadata: dict[str, Any], secret_value: str) -> dict[str, Any]:
        handle = metadata["handle"]
        safe_name = handle.replace("://", "_").replace("/", "_")
        secret_path = self.root / f"{safe_name}.secret"
        secret_path.write_text(secret_value, encoding="utf-8")
        os.chmod(secret_path, 0o600)
        record = dict(metadata)
        record["storage"] = "local-0600-file"
        record["secret_path"] = str(secret_path)
        record["secret_material"] = "never returned"
        data = self._load_index()
        data[handle] = record
        self._save_index(data)
        return self.public_record(handle)

    def public_record(self, handle: str) -> dict[str, Any]:
        record = dict(self._load_index()[handle])
        record.pop("secret_path", None)
        record["secret_material"] = "never returned"
        return record

    def list_public(self) -> list[dict[str, Any]]:
        return [self.public_record(handle) for handle in sorted(self._load_index())]

    def with_secret_env(self, handle: str, env_name: str, command: list[str]) -> subprocess.CompletedProcess[str]:
        record = self._load_index()[handle]
        secret = Path(record["secret_path"]).read_text(encoding="utf-8")
        env = {env_name: secret, "PATH": os.environ.get("PATH", "")}
        return subprocess.run(command, env=env, text=True, capture_output=True, check=False)
