from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any


class CredentialBroker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.index_path = self.root / "index.json"
        self.key_path = self.root / "local.key"

    def _require_openssl(self) -> None:
        if not shutil.which("openssl"):
            raise RuntimeError("openssl_required_for_local_encrypted_file")

    def _ensure_key(self) -> None:
        if not self.key_path.exists():
            self.key_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            os.chmod(self.key_path, 0o600)

    def _encrypt_to_file(self, secret_value: str, secret_path: Path) -> None:
        self._require_openssl()
        self._ensure_key()
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-out",
                str(secret_path),
                "-pass",
                f"file:{self.key_path}",
            ],
            input=secret_value.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        os.chmod(secret_path, 0o600)

    def _decrypt_file(self, secret_path: Path) -> str:
        self._require_openssl()
        result = subprocess.run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-in",
                str(secret_path),
                "-pass",
                f"file:{self.key_path}",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
        return result.stdout.decode("utf-8")

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
        secret_path = self.root / f"{safe_name}.secret.enc"
        self._encrypt_to_file(secret_value, secret_path)
        record = dict(metadata)
        record["storage"] = "local-openssl-file"
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

    def delete(self, handle: str) -> dict[str, Any]:
        data = self._load_index()
        record = data.pop(handle)
        secret_path = Path(record["secret_path"])
        if secret_path.exists():
            secret_path.unlink()
        self._save_index(data)
        return {"handle": handle, "deleted": True}

    def test(self, handle: str) -> dict[str, Any]:
        record = self._load_index()[handle]
        secret = self._decrypt_file(Path(record["secret_path"]))
        return {"handle": handle, "ok": bool(secret), "type": record.get("type")}

    def with_secret_env(self, handle: str, env_name: str, command: list[str]) -> subprocess.CompletedProcess[str]:
        record = self._load_index()[handle]
        secret = self._decrypt_file(Path(record["secret_path"]))
        env = {env_name: secret, "PATH": os.environ.get("PATH", "")}
        return subprocess.run(command, env=env, text=True, capture_output=True, check=False)
