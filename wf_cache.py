from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileCache:
    base_dir: Path

    def path(self, *parts: str) -> Path:
        return self.base_dir.joinpath(*parts)

    def read_bytes(self, *parts: str) -> bytes | None:
        p = self.path(*parts)
        if not p.exists():
            return None
        return p.read_bytes()

    def write_bytes(self, data: bytes, *parts: str) -> Path:
        p = self.path(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def read_json(self, *parts: str) -> Any | None:
        data = self.read_bytes(*parts)
        if data is None:
            return None
        return json.loads(data.decode("utf-8", errors="replace"))

    def write_json(self, obj: Any, *parts: str) -> Path:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.write_bytes(data, *parts)


