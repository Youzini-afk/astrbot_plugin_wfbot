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

FACTION_ZH: dict[str, str] = {
    "FC_GRINEER": "Grineer",
    "FC_CORPUS": "Corpus",
    "FC_INFESTED": "Infested",
    "FC_OROKIN": "Orokin",
    "FC_SENTIENT": "Sentient",
    "GRINEER": "Grineer",
    "CORPUS": "Corpus",
    "INFESTED": "Infested",
    "OROKIN": "Orokin",
    "SENTIENT": "Sentient",
}

FISSURE_TIER_ZH: dict[str, str] = {
    "VoidT1": "古纪",
    "VoidT2": "中纪",
    "VoidT3": "前纪",
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


def _translate_mission_like(val: Any) -> str:
    if not isinstance(val, str) or not val:
        return "-"
    if val in MISSION_TYPE_ZH or val.startswith("MT_"):
        return translate_mission_type(val)
    lookup = val.strip()
    key = lookup.lower().replace("_", " ").strip()
    mapping = {
        "extermination": "歼灭",
        "capture": "捕获",
        "survival": "生存",
        "defense": "防御",
        "mobile defense": "移动防御",
        "rescue": "救援",
        "spy": "间谍",
        "sabotage": "破坏",
        "excavation": "挖掘",
        "interception": "拦截",
        "disruption": "扰乱",
        "hive": "清巢",
        "assassination": "刺杀",
        "assault": "强袭",
    }
    if key in mapping:
        return mapping[key]
    return val


def _translate_boss(val: Any) -> str:
    if not isinstance(val, str) or not val:
        return "-"
    mapping = {
        "SORTIE_BOSS_INFALAD": "异融者 Alad V",
        "SORTIE_BOSS_AMAR": "阿玛尔",
        "SORTIE_BOSS_BOREAL": "波瑞尔",
        "SORTIE_BOSS_NIRA": "尼拉",
    }
    if val in mapping:
        return mapping[val]
    if val.startswith("SORTIE_BOSS_"):
        core = val.replace("SORTIE_BOSS_", "").replace("_", " ").title()
        return core
    return val


def _translate_faction(val: Any) -> str:
    if not isinstance(val, str) or not val:
        return "-"
    return FACTION_ZH.get(val, val)


def _is_active(activation: Any, expiry: Any) -> bool | None:
    now = datetime.now(timezone.utc).timestamp()
    act = None
    exp = None
    if isinstance(activation, (int, float)):
        act = float(activation)
    elif isinstance(activation, str) and activation:
        try:
            act = datetime.fromisoformat(activation.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            act = None
    if isinstance(expiry, (int, float)):
        exp = float(expiry)
    elif isinstance(expiry, str) and expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            exp = None
    if act is None or exp is None:
        return None
    return act <= now <= exp


def _fmt_remaining(ts: Any) -> str:
    # Accept seconds timestamp (float/int) or ISO string; prefer short relative output.
    now = datetime.now(timezone.utc).timestamp()
    secs: float | None = None
    if isinstance(ts, (int, float)):
        secs = float(ts) - now
    elif isinstance(ts, str) and ts:
        s = ts.strip()
        if s.isdigit():
            try:
                v = float(s)
                if v > 1e12:
                    v = v / 1000.0
                secs = v - now
            except Exception:
                secs = None
        else:
            s = s.replace("Z", "+00:00")
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
    if zone in {"earth", "cetus"} and ("isDay" in obj or "isday" in obj):
        flag = obj.get("isDay") if "isDay" in obj else obj.get("isday")
        return "day" if bool(flag) else "night"
    if zone == "vallis" and ("isWarm" in obj or "iswarm" in obj):
        flag = obj.get("isWarm") if "isWarm" in obj else obj.get("iswarm")
        return "warm" if bool(flag) else "cold"
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
        return "⚔️ 入侵：-"
    active = [i for i in inv if isinstance(i, dict) and not i.get("completed")]
    lines = [f"⚔️ 入侵（{len(active)}）"]
    for i in active[:limit]:
        node = _node_name(str(i.get("node") or "-"), nodes_map)
        atk = _translate_faction(i.get("attackingFaction") or i.get("attackerFaction") or i.get("faction"))
        dfd = _translate_faction(i.get("defendingFaction") or i.get("defenderFaction"))
        prog = i.get("completion")
        prog_s = f"{int(float(prog) * 100)}%" if isinstance(prog, (int, float)) else "-"
        eta = _fmt_remaining(i.get("expiry"))
        lines.append(f"- {node}｜{atk} vs {dfd}｜进度 {prog_s}｜⏳{eta}")
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
        tl = v.get("timeLeft")
        if isinstance(tl, str) and tl.strip():
            eta = tl.strip()
        else:
            eta = _fmt_remaining(v.get("expiry") or v.get("expiration") or v.get("expiryDate") or v.get("endTime"))
        lines.append(f"- {title}｜{st_label}｜⏳{eta}")
    return "\n".join(lines)


def format_void_trader(ws: dict, nodes_map: dict[str, str] | None = None, items_map: dict[str, str] | None = None) -> str:
    v = ws.get("voidTrader")
    if not isinstance(v, dict):
        return "🧳 奸商：-"
    active = v.get("active")
    loc = _node_name(str(v.get("location") or "-"), nodes_map)
    exp = v.get("expiry")
    act = v.get("activation")
    inventory = v.get("inventory") or v.get("manifest")
    items: list[dict] = inventory if isinstance(inventory, list) else []
    if active is None:
        active = _is_active(act, exp)
    header = ""
    if active is True:
        eta = _fmt_remaining(exp)
        header = f"🧳 奸商｜已到达 {loc}｜剩余⏳{eta}"
    elif active is False:
        eta = _fmt_remaining(act)
        header = f"🧳 奸商｜未到达 {loc}｜距离到达⏳{eta}"
    else:
        header = f"🧳 奸商｜位置 {loc}｜⏳{_fmt_remaining(exp)}"
    if not items:
        return header
    lines = [header, "📦 商品清单："]
    for it in items[:10]:
        if not isinstance(it, dict):
            continue
        unique = it.get("uniqueName")
        name = it.get("item") or it.get("name")
        if isinstance(unique, str) and items_map:
            if unique in items_map:
                name = items_map.get(unique)
            else:
                parts = [p for p in unique.split("/") if p]
                if parts:
                    suffix = "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)
                    if suffix in items_map:
                        name = items_map.get(suffix)
        if not name:
            name = "-"
        ducats = it.get("ducats")
        credits = it.get("credits")
        price_bits = []
        if ducats is not None:
            price_bits.append(f"{ducats} 杜卡德")
        if credits is not None:
            price_bits.append(f"{credits} 信用点")
        price = " / ".join(price_bits) if price_bits else "-"
        lines.append(f"- {name}｜{price}")
    return "\n".join(lines)


def format_daily_deals(ws: dict, limit: int = 8, items_map: dict[str, str] | None = None) -> str:
    deals = _g(ws, "dailyDeals", default=[])
    if not isinstance(deals, list):
        return "💰 每日特惠：-"
    lines = [f"💰 每日特惠（{len(deals)}）"]
    for d in deals[:limit]:
        if not isinstance(d, dict):
            continue
        item = d.get("item") or "-"
        unique = d.get("uniqueName")
        if items_map:
            if isinstance(item, str) and item in items_map:
                item = items_map[item]
            elif isinstance(unique, str) and unique in items_map:
                item = items_map[unique]
            else:
                raw = unique if isinstance(unique, str) else item
                if isinstance(raw, str):
                    parts = [p for p in raw.split("/") if p]
                    if parts:
                        suffix = "/".join(parts[-3:]) if len(parts) > 3 else "/".join(parts)
                        if suffix in items_map:
                            item = items_map[suffix]
        price = d.get("salePrice") or d.get("originalPrice") or "-"
        exp = _fmt_remaining(d.get("expiry"))
        lines.append(f"- {item}｜价格 {price}｜⏳{exp}")
    return "\n".join(lines)


def format_sortie(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    s = ws.get("sortie")
    if not isinstance(s, dict):
        return "⚔️ 突击：-"
    boss = _translate_boss(s.get("boss") or "-")
    exp = _fmt_remaining(s.get("expiry"))
    lines = [f"⚔️ 突击｜首领 {boss}｜⏳{exp}"]
    variants = s.get("variants")
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, dict):
                continue
            node = _node_name(str(v.get("node") or "-"), nodes_map)
            mtype = _translate_mission_like(v.get("missionType"))
            mod = v.get("modifier") or v.get("modifierType") or "-"
            lines.append(f"- {node}｜{mtype}｜{mod}")
    return "\n".join(lines)


def format_archon_hunt(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    # field name varies by worldstate model; try common keys
    hunt = ws.get("archonHunt") or ws.get("liteSortie")
    if not isinstance(hunt, dict):
        return "🧿 执刑官猎杀：-"
    boss = _translate_boss(hunt.get("boss") or hunt.get("bossName") or "-")
    exp = _fmt_remaining(hunt.get("expiry"))
    lines = [f"🧿 执刑官猎杀｜首领 {boss}｜⏳{exp}"]
    missions = hunt.get("missions") or hunt.get("variants")
    if isinstance(missions, list):
        for m in missions:
            if not isinstance(m, dict):
                continue
            node = _node_name(str(m.get("node") or m.get("location") or "-"), nodes_map)
            mtype = _translate_mission_like(m.get("missionType"))
            lines.append(f"- {node}｜{mtype}")
    return "\n".join(lines)


def format_arbitration(ws: dict, nodes_map: dict[str, str] | None = None) -> str:
    arb = ws.get("arbitration")
    if not isinstance(arb, dict):
        return "⚖️ 仲裁：-"
    raw_node = str(
        arb.get("node")
        or arb.get("location")
        or arb.get("nodeName")
        or arb.get("nodeKey")
        or arb.get("planet")
        or "-"
    )
    node = _node_name(raw_node, nodes_map)
    if node == raw_node and raw_node.startswith("SolNode"):
        node = "未知地点"
    mtype = _translate_mission_like(arb.get("type") or arb.get("missionType"))
    if mtype in {"Unknown", "unknown"}:
        mtype = "未知"
    exp = _fmt_remaining(arb.get("expiry"))
    if node == "未知地点" and mtype == "未知" and exp == "-":
        return "⚖️ 仲裁：暂无数据"
    return f"⚖️ 仲裁｜{node}｜{mtype}｜⏳{exp}"


def format_steel_path(ws: dict) -> str:
    sp = ws.get("steelPath") or ws.get("steelPathOffering")
    if not isinstance(sp, dict):
        return "🟥 钢铁奖励：-"
    exp = _fmt_remaining(sp.get("expiry"))
    rotation = sp.get("rotation") or sp.get("name") or "-"
    current = sp.get("currentReward")
    next_reward = sp.get("nextReward")
    remaining = sp.get("remaining")
    lines = [f"🟥 钢铁奖励｜⏳{exp}"]
    if isinstance(current, dict):
        name = current.get("name") or current.get("item") or "-"
        cost = current.get("cost")
        lines.append(f"📦 当前：{name}{f'（{cost} 余烬）' if cost is not None else ''}")
    elif isinstance(current, str):
        lines.append(f"📦 当前：{current}")
    if isinstance(next_reward, dict):
        name = next_reward.get("name") or next_reward.get("item") or "-"
        cost = next_reward.get("cost")
        lines.append(f"✨ 下次：{name}{f'（{cost} 余烬）' if cost is not None else ''}")
    elif isinstance(next_reward, str):
        lines.append(f"✨ 下次：{next_reward}")
    if isinstance(remaining, str) and remaining:
        lines.append(f"⏰ 剩余：{remaining}")
    if isinstance(rotation, list):
        for it in rotation[:6]:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("item") or "-"
            cost = it.get("cost")
            if cost is not None:
                lines.append(f"- {name}｜{cost} 余烬")
            else:
                lines.append(f"- {name}")
        return "\n".join(lines)
    return f"🟥 钢铁奖励｜{rotation}｜⏳{exp}"


def format_duviri_cycle(ws: dict) -> str:
    d = ws.get("duviriCycle") or ws.get("duvalierCycle")
    if not isinstance(d, dict):
        return "🎭 双衍王境：-"
    st = _cycle_state("duviri", d)
    _, st_label = _cycle_label("duviri", st)
    tl = d.get("timeLeft")
    if isinstance(tl, str) and tl.strip():
        eta = tl.strip()
    else:
        eta = _fmt_remaining(d.get("expiry") or d.get("expiration") or d.get("expiryDate") or d.get("endTime"))
    return f"🎭 双衍王境｜{st_label}｜⏳{eta}"


def format_nightwave(ws: dict) -> str:
    n = ws.get("seasonInfo") or ws.get("nightwave")
    if not isinstance(n, dict):
        return "📡 电波：-"
    season = n.get("season")
    tag = n.get("tag") or "-"
    if isinstance(season, (int, float)) or (isinstance(season, str) and str(season).isdigit()):
        tag = f"第{season}期"
    elif isinstance(tag, str) and tag != "-":
        t = tag
        t = t.replace("RadioLegion", "电波")
        t = t.replace("Intermission", "中场")
        t = t.replace("Syndicate", "")
        t = t.replace("  ", " ").strip()
        tag = t
    exp = _fmt_remaining(n.get("expiry"))
    return f"📡 电波｜{tag}｜⏳{exp}"
