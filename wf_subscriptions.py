from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SubscriptionEntry:
    unified_msg_origin: str
    user_id: str | None
    platform: str | None
    topics: dict[str, dict[str, Any]]


class SubscriptionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 2, "items": []}
        except Exception:
            return {"version": 2, "items": []}
        if not isinstance(data, dict):
            return {"version": 2, "items": []}

        ver = data.get("version")
        if ver == 1:
            # migrate v1: {items:[{umo, topics, ...}]} -> v2 adds uid=None
            items = data.get("items")
            migrated: list[dict[str, Any]] = []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    umo = it.get("umo")
                    topics = it.get("topics")
                    if not isinstance(umo, str) or not umo:
                        continue
                    if not isinstance(topics, dict):
                        topics = {}
                    migrated.append(
                        {
                            "umo": umo,
                            "uid": None,
                            "topics": topics,
                            "created_at": it.get("created_at") or _utcnow_iso(),
                            "updated_at": it.get("updated_at") or _utcnow_iso(),
                        }
                    )
            data = {"version": 2, "items": migrated}
        elif ver != 2:
            return {"version": 2, "items": []}

        if not isinstance(data.get("items"), list):
            data["items"] = []
        return data

    def save(self, data: dict[str, Any]) -> None:
        data = dict(data)
        data["version"] = 2
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def list_entries(self, data: dict[str, Any]) -> list[SubscriptionEntry]:
        items = data.get("items")
        if not isinstance(items, list):
            return []
        out: list[SubscriptionEntry] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            umo = it.get("umo")
            uid = it.get("uid")
            platform = it.get("platform")
            topics = it.get("topics")
            if not isinstance(umo, str) or not umo:
                continue
            if uid is not None and not isinstance(uid, str):
                uid = None
            if platform is not None and not isinstance(platform, str):
                platform = None
            if not isinstance(topics, dict):
                topics = {}
            out.append(SubscriptionEntry(unified_msg_origin=umo, user_id=uid, platform=platform, topics=topics))
        return out

    def upsert_topic(
        self,
        data: dict[str, Any],
        *,
        umo: str,
        uid: str | None,
        topic: str,
        user_name: str | None = None,
        platform: str | None = None,
    ) -> bool:
        items = data.setdefault("items", [])
        if not isinstance(items, list):
            data["items"] = []
            items = data["items"]

        entry: dict[str, Any] | None = None
        for it in items:
            if isinstance(it, dict) and it.get("umo") == umo and it.get("uid") == uid:
                entry = it
                break
        if entry is None:
            entry = {
                "umo": umo,
                "uid": uid,
                "platform": platform,
                "user_name": user_name,
                "topics": {},
                "created_at": _utcnow_iso(),
                "updated_at": _utcnow_iso(),
            }
            items.append(entry)

        topics = entry.get("topics")
        if not isinstance(topics, dict):
            topics = {}
            entry["topics"] = topics

        if topic in topics:
            return False
        topics[topic] = {"created_at": _utcnow_iso(), "updated_at": _utcnow_iso(), "last_sig": None, "last_pre_sig": None}
        if user_name:
            entry["user_name"] = user_name
        if platform:
            entry["platform"] = platform
        entry["updated_at"] = _utcnow_iso()
        return True

    def remove_topic(self, data: dict[str, Any], *, umo: str, uid: str | None, topic: str) -> bool:
        items = data.get("items")
        if not isinstance(items, list):
            return False
        changed = False
        for it in items:
            if not isinstance(it, dict) or it.get("umo") != umo or it.get("uid") != uid:
                continue
            topics = it.get("topics")
            if not isinstance(topics, dict):
                return False
            if topic in topics:
                topics.pop(topic, None)
                it["updated_at"] = _utcnow_iso()
                changed = True
            break
        if changed:
            data["items"] = [
                x
                for x in items
                if isinstance(x, dict) and x.get("umo") and isinstance(x.get("topics"), dict) and x["topics"]
            ]
        return changed

    def clear_umo(self, data: dict[str, Any], *, umo: str, uid: str | None, include_legacy: bool = True) -> bool:
        items = data.get("items")
        if not isinstance(items, list):
            return False
        before = len(items)
        def keep(x: Any) -> bool:
            if not isinstance(x, dict) or x.get("umo") != umo:
                return True
            if x.get("uid") == uid:
                return False
            if include_legacy and x.get("uid") is None:
                return False
            return True

        data["items"] = [x for x in items if keep(x)]
        return len(data["items"]) != before

    def set_last_sig(self, data: dict[str, Any], *, umo: str, uid: str | None, topic: str, sig: str | None) -> bool:
        items = data.get("items")
        if not isinstance(items, list):
            return False
        for it in items:
            if not isinstance(it, dict) or it.get("umo") != umo or it.get("uid") != uid:
                continue
            topics = it.get("topics")
            if not isinstance(topics, dict):
                return False
            meta = topics.get(topic)
            if not isinstance(meta, dict):
                return False
            if meta.get("last_sig") == sig:
                return False
            meta["last_sig"] = sig
            meta["updated_at"] = _utcnow_iso()
            it["updated_at"] = _utcnow_iso()
            return True
        return False

    def set_last_pre_sig(self, data: dict[str, Any], *, umo: str, uid: str | None, topic: str, sig: str | None) -> bool:
        items = data.get("items")
        if not isinstance(items, list):
            return False
        for it in items:
            if not isinstance(it, dict) or it.get("umo") != umo or it.get("uid") != uid:
                continue
            topics = it.get("topics")
            if not isinstance(topics, dict):
                return False
            meta = topics.get(topic)
            if not isinstance(meta, dict):
                return False
            if meta.get("last_pre_sig") == sig:
                return False
            meta["last_pre_sig"] = sig
            meta["updated_at"] = _utcnow_iso()
            it["updated_at"] = _utcnow_iso()
            return True
        return False
