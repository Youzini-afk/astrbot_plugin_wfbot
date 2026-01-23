from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


MISSION_TYPE_ZH: dict[str, str] = {
    "MT_EXTERMINATION": "歼灭",
    "MT_CAPTURE": "捕获",
    "MT_SURVIVAL": "生存",
    "MT_DEFENSE": "防御",
    "MT_MOBILE_DEFENSE": "移动防御",
    "MT_RESCUE": "救援",
    "MT_SPY": "间谍",
    "MT_SABOTAGE": "破坏",
    "MT_EXCAVATION": "挖掘",
    "MT_INTERCEPTION": "拦截",
    "MT_DISRUPTION": "扰乱",
    "MT_TERRITORY": "占领",
    "MT_INTEL": "情报",
    "MT_ALCHEMY": "炼金",
    "MT_CORRUPTION": "腐化",
    # Zariman / Void (common in fissures)
    "MT_VOID_CASCADE": "虚空瀑布",
    "MT_VOID_FLOOD": "虚空洪流",
    "MT_VOID_ARMAGEDDON": "虚空末日",
}

FISSURE_TIER_ZH: dict[str, str] = {
    "VoidT1": "古纪",
    "VoidT2": "中纪",
    "VoidT3": "新纪",
    "VoidT4": "后纪",
    "VoidT5": "安魂",
    "VoidT6": "全能",
}

DUVIRI_STATE_ZH: dict[str, str] = {
    "anger": "怒火",
    "joy": "喜悦",
    "envy": "嫉妒",
    "sorrow": "悲伤",
    "fear": "恐惧",
}

ZARIMAN_STATE_ZH: dict[str, str] = {
    "corpus": "Corpus",
    "grineer": "Grineer",
}


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
    if isinstance(nodes_dataset, dict):
        # warframestat solNodes returns a mapping: SolNode26 -> {"value": "...", ...}
        for k, v in nodes_dataset.items():
            if not isinstance(k, str) or not k:
                continue
            if isinstance(v, dict):
                name = v.get("value") or v.get("name")
                if isinstance(name, str) and name:
                    out[k] = name
            elif isinstance(v, str) and v:
                out[k] = v
    if isinstance(nodes_dataset, list):
        for it in nodes_dataset:
            if not isinstance(it, dict):
                continue
            node_id = it.get("id") or it.get("node") or it.get("uniqueName")
            name = it.get("name")
            system = it.get("systemName") or it.get("system")
            if node_id and name:
                disp = f"{name}({system})" if system else str(name)
                out[str(node_id)] = disp
    return out


def translate_mission_type(code: Any) -> str:
    if not isinstance(code, str) or not code:
        return "-"
    return MISSION_TYPE_ZH.get(code, code)


def translate_fissure_tier(code: Any) -> str:
    if not isinstance(code, str) or not code:
        return "-"
    return FISSURE_TIER_ZH.get(code, code)


def mission_emoji(code: Any) -> str:
    if not isinstance(code, str) or not code:
        return ""
    return {
        "MT_EXTERMINATION": "⚔️",
        "MT_CAPTURE": "🎯",
        "MT_SURVIVAL": "⏱️",
        "MT_DEFENSE": "🛡️",
        "MT_MOBILE_DEFENSE": "🛡️",
        "MT_RESCUE": "🆘",
        "MT_SPY": "🕵️",
        "MT_SABOTAGE": "💥",
        "MT_EXCAVATION": "⛏️",
        "MT_INTERCEPTION": "📡",
        "MT_DISRUPTION": "🧪",
        "MT_TERRITORY": "🚩",
        "MT_INTEL": "🧠",
        "MT_ALCHEMY": "🧪",
        "MT_CORRUPTION": "☣️",
    }.get(code, "")


def _fmt_remaining(ts: Any) -> str:
    # Accept seconds timestamp (float/int) or ISO string; prefer short relative output.
    now = datetime.now(timezone.utc).timestamp()
    secs: float | None = None
    if isinstance(ts, (int, float)):
        secs = float(ts) - now
    elif isinstance(ts, str) and ts:
        s = ts.replace("Z", "+00:00")
        try:
            secs = datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() - now
        except Exception:
            secs = None
    if secs is None:
        return "-"
    if secs <= 0:
        return "已结束"
    minutes = int(secs // 60)
    if minutes < 60:
        return f"{minutes}分"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}小时{minutes % 60}分"
    days = hours // 24
    return f"{days}天{hours % 24}小时"


def _cycle_state(zone: str, obj: dict) -> str:
    st = obj.get("state")
    if isinstance(st, str) and st:
        return st.strip().lower()
    if zone in {"earth", "cetus"} and "isDay" in obj:
        return "day" if bool(obj.get("isDay")) else "night"
    if zone == "vallis" and "isWarm" in obj:
        return "warm" if bool(obj.get("isWarm")) else "cold"
    return "-"


def _cycle_label(zone: str, state: str) -> tuple[str, str]:
    if zone == "earth":
        return ("🌍 地球", "☀️白天" if state == "day" else "🌙夜晚" if state == "night" else state)
    if zone == "cetus":
        return ("🌾 夜灵平原", "☀️白天" if state == "day" else "🌙夜晚" if state == "night" else state)
    if zone == "vallis":
        return ("❄️ 福尔图娜", "🔥温暖" if state == "warm" else "❄️寒冷" if state == "cold" else state)
    if zone == "cambion":
        return ("🦠 魔胎之境", "🟥Fass" if state == "fass" else "🟦Vome" if state == "vome" else state)
    if zone == "zariman":
        zh = ZARIMAN_STATE_ZH.get(state, state)
        return ("🚢 扎里曼", f"⚔️{zh}" if zh else state)
    if zone == "duviri":
        zh = DUVIRI_STATE_ZH.get(state, state)
        return ("🎭 双衍王境", f"🎴{zh}" if zh else state)
    return (zone, state)


def format_alerts(ws: dict, nodes_map: dict[str, str] | None = None, limit: int = 8) -> str:
    alerts = _g(ws, "alerts", default=[])
    if not isinstance(alerts, list):
        return "alerts: -"
    lines = [f"🚨 警报（{len(alerts)}）"]
    for a in alerts[:limit]:
        if not isinstance(a, dict):
            continue
        mi = a.get("missionInfo") or {}
        node = _node_name(str(mi.get("location") or "-"), nodes_map)
        mtype_code = mi.get("missionType") or mi.get("missionTypeKey")
        mtype = translate_mission_type(mtype_code)
        em = mission_emoji(mtype_code)
        expiry = _ts_iso(a.get("expiry") or a.get("endTime"))
        eta = _fmt_remaining(a.get("expiry") or a.get("endTime"))
        lines.append(f"- {node}｜{em}{mtype}｜⏳{eta}")
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

    title = {"normal": "普通", "steel": "钢铁", "storm": "九重天"}.get(kind, kind)
    lines = [f"🌀 裂缝·{title}（{len(fiss)}）"]
    for m in fiss[:limit]:
        node = _node_name(str(m.get("node") or m.get("location") or "-"), nodes_map)
        tier_code = m.get("modifier") or m.get("tier")
        tier = translate_fissure_tier(tier_code)
        mtype_code = m.get("missionType") or m.get("MissionType")
        mtype = translate_mission_type(mtype_code)
        em = mission_emoji(mtype_code)
        expiry = _ts_iso(m.get("expiry"))
        eta = _fmt_remaining(m.get("expiry"))
        lines.append(f"- {node}｜{em}{mtype}｜{tier}｜⏳{eta}")
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
    specs = [
        ("earthCycle", "earth"),
        ("cetusCycle", "cetus"),
        ("vallisCycle", "vallis"),
        ("cambionCycle", "cambion"),
        ("zarimanCycle", "zariman"),
        ("duviriCycle", "duviri"),
        ("duvalierCycle", "duviri"),
    ]
    seen: set[str] = set()
    lines = ["🔄 循环"]
    for key, zone in specs:
        if key in seen:
            continue
        seen.add(key)
        v = ws.get(key)
        if not isinstance(v, dict):
            continue
        st = _cycle_state(zone, v)
        title, st_label = _cycle_label(zone, st)
        eta = _fmt_remaining(v.get("expiry"))
        lines.append(f"- {title}｜{st_label}｜⏳{eta}")
    return "\n".join(lines)


def format_void_trader(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    v = ws.get("voidTrader")
    if not isinstance(v, dict):
        return "voidTrader: -"
    active = v.get("active")
    loc = _node_name(str(v.get("location") or "-"), nodes_map)
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
    d = ws.get("duviriCycle") or ws.get("duvalierCycle")
    if not isinstance(d, dict):
        return "🎭 双衍王境：-"
    st = _cycle_state("duviri", d)
    _, st_label = _cycle_label("duviri", st)
    eta = _fmt_remaining(d.get("expiry"))
    return f"🎭 双衍王境｜{st_label}｜⏳{eta}"


def format_nightwave(ws: dict) -> str:
    n = ws.get("seasonInfo") or ws.get("nightwave")
    if not isinstance(n, dict):
        return "nightwave: -"
    tag = n.get("tag") or n.get("season") or "-"
    exp = _ts_iso(n.get("expiry"))
    return f"nightwave: {tag} exp={exp}"
