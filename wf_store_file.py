from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileStore:
    """
    Simple persistence fallback when sqlite is unavailable.

    Layout (under base_dir):
    - snapshots/<source>/<name>/<timestamp>.json
    - snapshots/<source>/<name>/<timestamp>.meta.json
    - event_history.json  (event -> key -> notified_at)
    """

    base_dir: Path

    def __post_init__(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def put_snapshot(
        self,
        *,
        source: str,
        name: str,
        status: int,
        url: str,
        sha256: str,
        json_obj: Any | None = None,
        raw: bytes | None = None,
        fetched_at: int | None = None,
    ) -> None:
        ts = int(fetched_at if fetched_at is not None else time.time())
        folder = self.base_dir / "snapshots" / source / name
        folder.mkdir(parents=True, exist_ok=True)

        meta = {"fetched_at": ts, "status": int(status), "url": url, "sha256": sha256}
        (folder / f"{ts}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        if json_obj is not None:
            (folder / f"{ts}.json").write_text(
                json.dumps(json_obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
        elif raw is not None:
            (folder / f"{ts}.raw").write_bytes(raw)

    def was_notified(self, *, event: str, key: str, within_seconds: int) -> bool:
        hist = self._read_event_history()
        ts = hist.get(event, {}).get(key)
        if ts is None:
            return False
        return int(time.time()) - int(ts) <= int(max(0, within_seconds))

    def mark_notified(self, *, event: str, key: str, notified_at: int | None = None) -> None:
        ts = int(notified_at if notified_at is not None else time.time())
        hist = self._read_event_history()
        hist.setdefault(event, {})[key] = ts
        self._write_event_history(hist)

    def prune(self, *, retention_days: int) -> None:
        cutoff = int(time.time()) - int(max(0, retention_days)) * 86400

        # prune snapshots
        snapshots_root = self.base_dir / "snapshots"
        if snapshots_root.exists():
            for p in snapshots_root.rglob("*"):
                if p.is_file():
                    try:
                        if int(p.stem.split(".", 1)[0]) < cutoff:
                            p.unlink()
                    except Exception:
                        continue

        # prune event history
        hist = self._read_event_history()
        new_hist: dict[str, dict[str, int]] = {}
        for event, keys in hist.items():
            kept = {k: int(v) for k, v in keys.items() if int(v) >= cutoff}
            if kept:
                new_hist[event] = kept
        self._write_event_history(new_hist)

    def _read_event_history(self) -> dict[str, dict[str, int]]:
        path = self.base_dir / "event_history.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out: dict[str, dict[str, int]] = {}
                for event, keys in data.items():
                    if not isinstance(keys, dict):
                        continue
                    out[str(event)] = {str(k): int(v) for k, v in keys.items()}
                return out
        except Exception:
            return {}
        return {}

    def _write_event_history(self, data: dict[str, dict[str, int]]) -> None:
        path = self.base_dir / "event_history.json"
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


