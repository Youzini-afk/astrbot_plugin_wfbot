from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .wf_cache import FileCache
from .wf_http import HttpClient


WARFRAME_WORLD_STATE_URL = "https://api.warframe.com/cdn/worldState.php"


def _unwrap_date(v: Any) -> float | None:
    # Warframe worldState.php uses Mongo-style date objects: {"$date":{"$numberLong":"<ms>"}}
    if isinstance(v, (int, float)):
        val = float(v)
        if val > 1e12:
            val = val / 1000.0
        return val
    if isinstance(v, dict):
        d = v.get("$date")
        if isinstance(d, dict):
            n = d.get("$numberLong")
            if isinstance(n, str) and n.isdigit():
                return int(n) / 1000.0
            if isinstance(n, (int, float)):
                return float(n) / 1000.0
        if isinstance(d, (int, float)):
            val = float(d)
            if val > 1e12:
                val = val / 1000.0
            return val
    return None


def _looks_like_official(ws: Any) -> bool:
    return isinstance(ws, dict) and ("WorldSeed" in ws or "Alerts" in ws or "ActiveMissions" in ws)


def _normalize_official_worldstate(ws: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def norm_alert(a: Any) -> Any:
        if not isinstance(a, dict):
            return a
        mi = a.get("MissionInfo")
        if not isinstance(mi, dict):
            mi = {}
        return {
            "id": a.get("id") or a.get("_id"),
            "expiry": _unwrap_date(a.get("Expiry")),
            "endTime": _unwrap_date(a.get("Expiry")),
            "activation": _unwrap_date(a.get("Activation")),
            "missionInfo": mi,
        }

    def norm_active_mission(m: Any) -> Any:
        if not isinstance(m, dict):
            return m
        return {
            "id": m.get("id") or m.get("_id"),
            "node": m.get("node") or m.get("Node"),
            "location": m.get("location") or m.get("Node"),
            "missionType": m.get("missionType") or m.get("MissionType"),
            "modifier": m.get("modifier") or m.get("Modifier"),
            "tier": m.get("tier") or m.get("Modifier"),
            "hard": m.get("hard") if "hard" in m else m.get("Hard"),
            "expiry": _unwrap_date(m.get("Expiry")),
            "activation": _unwrap_date(m.get("Activation")),
        }

    def norm_void_storm(m: Any) -> Any:
        if not isinstance(m, dict):
            return m
        return {
            "id": m.get("id") or m.get("_id"),
            "node": m.get("node") or m.get("Node"),
            "location": m.get("location") or m.get("Node"),
            "missionType": m.get("missionType") or m.get("MissionType"),
            "modifier": m.get("modifier") or m.get("Modifier"),
            "tier": m.get("tier") or m.get("Modifier"),
            "hard": m.get("hard") if "hard" in m else m.get("Hard"),
            "expiry": _unwrap_date(m.get("Expiry")),
            "activation": _unwrap_date(m.get("Activation")),
        }

    def norm_invasion(i: Any) -> Any:
        if not isinstance(i, dict):
            return i
        goal = i.get("Goal")
        count = i.get("Count")
        completion = None
        if isinstance(goal, (int, float)) and isinstance(count, (int, float)) and goal:
            completion = float(goal - count) / float(goal)
        attacking = i.get("AttackingFaction") or i.get("attackingFaction") or i.get("AttackerFaction") or i.get("attackerFaction")
        defending = i.get("DefendingFaction") or i.get("defendingFaction") or i.get("DefenderFaction") or i.get("defenderFaction")
        return {
            "id": i.get("id") or i.get("_id"),
            "node": i.get("node") or i.get("Node"),
            "completed": i.get("completed") if "completed" in i else i.get("Completed"),
            "completion": i.get("completion") if "completion" in i else completion,
            "expiry": _unwrap_date(i.get("Expiry")),
            "attackingFaction": attacking,
            "defendingFaction": defending,
        }

    def norm_sortie(s: Any) -> Any:
        if not isinstance(s, dict):
            return {}
        variants = s.get("Variants")
        out_vars = []
        if isinstance(variants, list):
            for v in variants:
                if not isinstance(v, dict):
                    continue
                out_vars.append(
                    {
                        "node": v.get("Node"),
                        "missionType": v.get("MissionType"),
                        "modifier": v.get("Modifier"),
                    }
                )
        return {
            "boss": s.get("Boss"),
            "expiry": _unwrap_date(s.get("Expiry")),
            "variants": out_vars,
        }

    def norm_lite_sortie(s: Any) -> Any:
        if not isinstance(s, dict):
            return {}
        missions = s.get("Missions")
        out_m = []
        if isinstance(missions, list):
            for m in missions:
                if not isinstance(m, dict):
                    continue
                out_m.append({"node": m.get("Node") or m.get("Location"), "missionType": m.get("MissionType")})
        return {
            "boss": s.get("Boss"),
            "expiry": _unwrap_date(s.get("Expiry")),
            "missions": out_m,
        }

    out["alerts"] = [norm_alert(a) for a in (ws.get("Alerts") or [])] if isinstance(ws.get("Alerts"), list) else []
    out["activeMissions"] = [norm_active_mission(m) for m in (ws.get("ActiveMissions") or [])] if isinstance(ws.get("ActiveMissions"), list) else []
    out["voidStorms"] = [norm_void_storm(m) for m in (ws.get("VoidStorms") or [])] if isinstance(ws.get("VoidStorms"), list) else []
    out["invasions"] = [norm_invasion(i) for i in (ws.get("Invasions") or [])] if isinstance(ws.get("Invasions"), list) else []

    vts = ws.get("VoidTraders")
    vt_obj = vts[0] if isinstance(vts, list) and vts else None
    if isinstance(vt_obj, dict):
        manifest_raw = vt_obj.get("Manifest") or vt_obj.get("manifest")
        manifest: list[dict] = []
        if isinstance(manifest_raw, list):
            for it in manifest_raw:
                if not isinstance(it, dict):
                    continue
                manifest.append(
                    {
                        "uniqueName": it.get("ItemType") or it.get("itemType") or it.get("uniqueName"),
                        "ducats": it.get("PrimePrice") or it.get("ducats"),
                        "credits": it.get("RegularPrice") or it.get("credits"),
                        "item": it.get("Item") or it.get("item"),
                    }
                )
        out["voidTrader"] = {
            "location": vt_obj.get("Node"),
            "expiry": _unwrap_date(vt_obj.get("Expiry")),
            "activation": _unwrap_date(vt_obj.get("Activation")),
            "character": vt_obj.get("Character") or vt_obj.get("character"),
            "manifest": manifest,
        }

    arbitration = ws.get("Arbitration") or ws.get("Arbitrations")
    if isinstance(arbitration, list) and arbitration:
        arbitration = arbitration[0]
    if isinstance(arbitration, dict):
        out["arbitration"] = {
            "node": arbitration.get("Node") or arbitration.get("node"),
            "type": arbitration.get("Type") or arbitration.get("type") or arbitration.get("MissionType"),
            "expiry": _unwrap_date(arbitration.get("Expiry") or arbitration.get("expiry")),
            "activation": _unwrap_date(arbitration.get("Activation") or arbitration.get("activation")),
        }

    steel_path = ws.get("SteelPath") or ws.get("SteelPathOffering") or ws.get("SteelPathOfferings")
    if isinstance(steel_path, list) and steel_path:
        steel_path = steel_path[0]
    if isinstance(steel_path, dict):
        rotation = steel_path.get("Rotation") or steel_path.get("rotation") or steel_path.get("Items") or steel_path.get("items")
        norm_rotation: list[dict] | None = None
        if isinstance(rotation, list):
            norm_rotation = []
            for it in rotation:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or it.get("Name") or it.get("item") or it.get("Item")
                cost = it.get("cost") or it.get("Cost") or it.get("price") or it.get("Price")
                norm_rotation.append({"name": name, "cost": cost})
        out["steelPathOffering"] = {
            "expiry": _unwrap_date(steel_path.get("Expiry") or steel_path.get("expiry")),
            "rotation": norm_rotation if norm_rotation is not None else rotation,
        }

    dds = ws.get("DailyDeals")
    if isinstance(dds, list):
        out["dailyDeals"] = [
            {
                "item": d.get("StoreItem"),
                "salePrice": d.get("SalePrice"),
                "originalPrice": d.get("OriginalPrice"),
                "expiry": _unwrap_date(d.get("Expiry")),
            }
            for d in dds
            if isinstance(d, dict)
        ]

    sorties = ws.get("Sorties")
    if isinstance(sorties, list) and sorties:
        out["sortie"] = norm_sortie(sorties[0])
    lite = ws.get("LiteSorties")
    if isinstance(lite, list) and lite:
        out["liteSortie"] = norm_lite_sortie(lite[0])

    season = ws.get("SeasonInfo")
    if isinstance(season, dict):
        out["seasonInfo"] = {
            "season": season.get("Season"),
            "tag": season.get("AffiliationTag"),
            "expiry": _unwrap_date(season.get("Expiry")),
        }

    out["_source"] = {"schema": "official", "url": WARFRAME_WORLD_STATE_URL}
    out["_fetched_at"] = ws.get("Time")
    return out

@dataclass(frozen=True)
class WorldStateResult:
    status: int
    json: Any | None
    raw: bytes
    url: str


class WorldStateClient:
    def __init__(self, http: HttpClient, cache: FileCache) -> None:
        self._http = http
        self._cache = cache

    async def fetch(self) -> WorldStateResult:
        resp = await self._http.get(WARFRAME_WORLD_STATE_URL)
        data = None
        if 200 <= resp.status < 400:
            # NyxBot treats 2xx and 3xx as acceptable.
            try:
                data = resp.json()
            except Exception:
                data = None
        if _looks_like_official(data):
            try:
                data = _normalize_official_worldstate(data)
            except Exception:
                pass
        return WorldStateResult(status=resp.status, json=data, raw=resp.body, url=resp.url)

    async def refresh_cache(self) -> WorldStateResult:
        result = await self.fetch()
        if result.json is not None:
            # Keep both raw and parsed formats for flexibility.
            await self._cache.write_bytes(result.raw, "worldstate", "latest.json")
            await self._cache.write_json(result.json, "worldstate", "latest.parsed.json")
        return result

    async def load_cached(self) -> Any | None:
        return await self._cache.read_json("worldstate", "latest.parsed.json")

