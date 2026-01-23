from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _g(obj: Any, *path: str, default=None):
    cur = obj
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return default
    return cur if cur is not None else default


def _ts_iso(ts: Any) -> str:
    if ts is None:
        return "-"
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return str(ts)
    if isinstance(ts, str):
        return ts
    return str(ts)


def _node_name(node: str, nodes_map: dict[str, str] | None) -> str:
    if nodes_map and node in nodes_map:
        return nodes_map[node]
    return node


def build_nodes_map(nodes_dataset: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(nodes_dataset, list):
        for it in nodes_dataset:
            if not isinstance(it, dict):
                continue
            node_id = it.get("id") or it.get("node")
            name = it.get("name")
            system = it.get("systemName") or it.get("system")
            if node_id and name:
                disp = f"{name}({system})" if system else str(name)
                out[str(node_id)] = disp
    return out


def format_alerts(ws: dict, nodes_map: dict[str, str] | None = None, limit: int = 8) -> str:
    alerts = _g(ws, "alerts", default=[])
    if not isinstance(alerts, list):
        return "alerts: -"
    lines = [f"alerts: {len(alerts)}"]
    for a in alerts[:limit]:
        if not isinstance(a, dict):
            continue
        mi = a.get("missionInfo") or {}
        node = _node_name(str(mi.get("location") or "-"), nodes_map)
        mtype = mi.get("missionType") or mi.get("missionTypeKey") or "-"
        expiry = _ts_iso(a.get("expiry") or a.get("endTime"))
        lines.append(f"- {node} {mtype} exp={expiry}")
    return "\n".join(lines)


def format_fissures(ws: dict, nodes_map: dict[str, str] | None = None, *, kind: str = "normal", limit: int = 10) -> str:
    if kind == "storm":
        fiss = _g(ws, "voidStorms", default=[])
    else:
        fiss = _g(ws, "activeMissions", default=[])

    if not isinstance(fiss, list):
        return "fissures: -"

    if kind == "steel":
        fiss = [m for m in fiss if isinstance(m, dict) and m.get("hard") is True]
    elif kind == "normal":
        fiss = [m for m in fiss if isinstance(m, dict) and (m.get("hard") is False or m.get("hard") is None)]

    lines = [f"fissures({kind}): {len(fiss)}"]
    for m in fiss[:limit]:
        node = _node_name(str(m.get("node") or m.get("location") or "-"), nodes_map)
        tier = m.get("modifier") or m.get("tier") or "-"
        expiry = _ts_iso(m.get("expiry"))
        lines.append(f"- {node} tier={tier} exp={expiry}")
    return "\n".join(lines)


def format_invasions(ws: dict, nodes_map: dict[str, str] | None = None, limit: int = 8) -> str:
    inv = _g(ws, "invasions", default=[])
    if not isinstance(inv, list):
        return "invasions: -"
    active = [i for i in inv if isinstance(i, dict) and not i.get("completed")]
    lines = [f"invasions(active): {len(active)}"]
    for i in active[:limit]:
        node = _node_name(str(i.get("node") or "-"), nodes_map)
        prog = i.get("completion")
        eta = _ts_iso(i.get("expiry"))
        lines.append(f"- {node} completion={prog} exp={eta}")
    return "\n".join(lines)


def format_cycles(ws: dict) -> str:
    keys = ["earthCycle", "cetusCycle", "vallisCycle", "cambionCycle", "zarimanCycle", "duvalierCycle"]
    lines = ["cycles:"]
    for k in keys:
        v = ws.get(k)
        if isinstance(v, dict):
            state = v.get("state") or v.get("id") or "-"
            exp = _ts_iso(v.get("expiry"))
            lines.append(f"- {k}: {state} exp={exp}")
    return "\n".join(lines)


def format_void_trader(ws: dict) -> str:
    v = ws.get("voidTrader")
    if not isinstance(v, dict):
        return "voidTrader: -"
    active = v.get("active")
    loc = v.get("location") or "-"
    exp = _ts_iso(v.get("expiry"))
    return f"voidTrader: active={active} location={loc} exp={exp}"


def format_daily_deals(ws: dict, limit: int = 8) -> str:
    deals = _g(ws, "dailyDeals", default=[])
    if not isinstance(deals, list):
        return "dailyDeals: -"
    lines = [f"dailyDeals: {len(deals)}"]
    for d in deals[:limit]:
        if not isinstance(d, dict):
            continue
        item = d.get("item") or "-"
        price = d.get("salePrice") or d.get("originalPrice") or "-"
        exp = _ts_iso(d.get("expiry"))
        lines.append(f"- {item} price={price} exp={exp}")
    return "\n".join(lines)


def format_sortie(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    s = ws.get("sortie")
    if not isinstance(s, dict):
        return "sortie: -"
    boss = s.get("boss") or "-"
    exp = _ts_iso(s.get("expiry"))
    lines = [f"sortie: boss={boss} exp={exp}"]
    variants = s.get("variants")
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, dict):
                continue
            node = _node_name(str(v.get("node") or "-"), nodes_map)
            mtype = v.get("missionType") or "-"
            mod = v.get("modifier") or "-"
            lines.append(f"- {node} {mtype} {mod}")
    return "\n".join(lines)


def format_archon_hunt(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    # field name varies by worldstate model; try common keys
    hunt = ws.get("liteSortie") or ws.get("archonHunt")
    if not isinstance(hunt, dict):
        return "archon: -"
    boss = hunt.get("boss") or hunt.get("bossName") or "-"
    exp = _ts_iso(hunt.get("expiry"))
    lines = [f"archon: boss={boss} exp={exp}"]
    missions = hunt.get("missions") or hunt.get("variants")
    if isinstance(missions, list):
        for m in missions:
            if not isinstance(m, dict):
                continue
            node = _node_name(str(m.get("node") or m.get("location") or "-"), nodes_map)
            mtype = m.get("missionType") or "-"
            lines.append(f"- {node} {mtype}")
    return "\n".join(lines)


def format_arbitration(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    arb = ws.get("arbitration")
    if not isinstance(arb, dict):
        return "arbitration: -"
    node = _node_name(str(arb.get("node") or "-"), nodes_map)
    mtype = arb.get("type") or arb.get("missionType") or "-"
    exp = _ts_iso(arb.get("expiry"))
    return f"arbitration: {node} type={mtype} exp={exp}"


def format_steel_path(ws: dict) -> str:
    sp = ws.get("steelPath") or ws.get("steelPathOffering")
    if not isinstance(sp, dict):
        return "steelPath: -"
    exp = _ts_iso(sp.get("expiry"))
    rotation = sp.get("rotation") or sp.get("name") or "-"
    return f"steelPath: rotation={rotation} exp={exp}"


def format_duviri_cycle(ws: dict) -> str:
    d = ws.get("duvalierCycle")
    if not isinstance(d, dict):
        return "duviri: -"
    state = d.get("state") or d.get("id") or "-"
    exp = _ts_iso(d.get("expiry"))
    return f"duviri: state={state} exp={exp}"


def format_nightwave(ws: dict) -> str:
    n = ws.get("seasonInfo") or ws.get("nightwave")
    if not isinstance(n, dict):
        return "nightwave: -"
    tag = n.get("tag") or n.get("season") or "-"
    exp = _ts_iso(n.get("expiry"))
    return f"nightwave: {tag} exp={exp}"
