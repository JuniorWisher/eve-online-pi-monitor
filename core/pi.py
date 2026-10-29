"""行星工业（Planetary Industry）数据解析与统计。

数据来源：
  GET /characters/{id}/planets/                     殖民地列表
  GET /characters/{id}/planets/{planet_id}/         殖民地详情（引脚/链接/路由/库存）
  POST /universe/names/                             id -> 名字
  GET /universe/planets/{id}/                       星球所属星系与类型
  GET /universe/systems/{id}/                       星系安全等级

一个"引脚(pin)"就是星球上的一个建筑：
  - extractor_details  -> 提取器控制单元
  - factory_details    -> 工厂（基础/高级/高科技）
  - 名字含 Launchpad / Storage -> 仓储（库存就在 contents 里）
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import i18n, market

# PI 物资按分组判定层级。优先用 group_id（稳定），名称只作兜底。
# 分组 id 来自 ESI /universe/categories/42 与 /43。
TIER_BY_GROUP_ID = {
    1032: "P0",  # Planet Solid - Raw Resource
    1033: "P0",  # Planet Liquid-Gas - Raw Resource
    1035: "P0",  # Planet Organic - Raw Resource
    1042: "P1",  # Basic Commodities - Tier 1
    1034: "P2",  # Refined Commodities - Tier 2
    1040: "P3",  # Specialized Commodities - Tier 3
    1041: "P4",  # Advanced Commodities - Tier 4
}

TIER_BY_GROUP_NAME = {
    "planet solid - raw resource": "P0",
    "planet liquid-gas - raw resource": "P0",
    "planet organic - raw resource": "P0",
    "basic commodities - tier 1": "P1",
    "refined commodities - tier 2": "P2",
    "specialized commodities - tier 3": "P3",
    "advanced commodities - tier 4": "P4",
    # 兼容早期命名
    "raw materials": "P0",
    "basic commodities": "P1",
    "refined commodities": "P2",
    "specialized commodities": "P3",
    "advanced commodities": "P4",
}

# 仓储建筑容量（m3）。实际引脚的类型名带星球前缀，例如 "Temperate Launchpad"。
CAPACITY_RULES = (
    (("launchpad", "spaceport"), 10000.0),   # 发射平台 10,000 m³
    (("storage facility",), 12000.0),        # 储存设施 12,000 m³
)

DEFAULT_ITEM_VOLUME = 0.01  # 拿不到类型体积时的兜底值

# 读海关/资产库存需要这个权限；旧 token 没有它，需要重新授权
ASSET_SCOPE = "esi-assets.read_assets.v1"
# 区分「玩家建筑」和「海关」必须解析建筑名，需要这个权限
STRUCT_SCOPE = "esi-universe.read_structures.v1"
# 判定"这是不是 PI 物资"用 type_id 白名单（P0-P4 全量 83 种）
PI_TYPE_IDS = frozenset(i18n.CN_BY_ID)

KIND_LABEL = {
    "extractor": "提取器",
    "factory": "工厂",
    "storage": "仓储",
    "command_center": "指挥中心",
    "other": "其他",
}


def parse_time(value) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def stock_value(colonies, assets, prices: dict) -> float:
    """按 Jita 最高买单估算全部库存价值，P0 原料不计入。"""
    total = 0.0

    def add(items):
        nonlocal total
        for i in items:
            if i.get("tier") == "P0":
                continue
            p = prices.get(int(i["type_id"]))
            if p and p.get("buy"):
                total += float(i["amount"]) * p["buy"]

    for c in colonies or []:
        for st in c.get("storages", []):
            add(st.get("items", []))
    for ch in (assets or {}).get("characters", []):
        add(ch.get("items", []))
    return total


def _tier_of(esi, type_id: int) -> str:
    info = esi.type_info(type_id)
    gid = info.get("group_id") or 0
    tier = TIER_BY_GROUP_ID.get(gid)
    if tier:
        return tier
    name = (esi.group_name(gid) or "").strip().lower()
    if name in TIER_BY_GROUP_NAME:
        return TIER_BY_GROUP_NAME[name]
    for key, t in (("tier 1", "P1"), ("tier 2", "P2"), ("tier 3", "P3"), ("tier 4", "P4")):
        if key in name:
            return t
    if "raw resource" in name:
        return "P0"
    return "?"


def _capacity_for(type_name: str) -> float | None:
    lower = (type_name or "").lower()
    for keywords, cap in CAPACITY_RULES:
        if any(k in lower for k in keywords):
            return cap
    return None


def _planet_type_label(name: str) -> str:
    """'Planet (Temperate)' -> 'Temperate'"""
    if not name:
        return ""
    if "(" in name and name.endswith(")"):
        return name[name.rindex("(") + 1 : -1]
    return name


ROMAN = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
    "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15,
    "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20,
}


def planet_sort_key(planet_name: str, system_name: str):
    """按行星名排序；同一星系内按罗马数字（Jita I、II、IV…）排，而不是字母序。"""
    name = planet_name or ""
    sysname = system_name or ""
    rest = name[len(sysname):].strip() if sysname and name.startswith(sysname) else name
    token = rest.split()[0] if rest else ""
    order = ROMAN.get(token.upper(), 999)
    return (sysname.lower(), order, name.lower())


def _sec_class(sec: float | None) -> str:
    if sec is None:
        return "unknown"
    if sec >= 0.45:
        return "high"
    if sec > 0.0:
        return "low"
    return "null"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def build_overview(esi, characters: list[dict], now: float | None = None) -> dict:
    now = now or time.time()

    char_errors: dict[int, str] = {}
    refs: list[dict] = []

    # 1. 每个角色的殖民地列表（可并行）
    def fetch_planets(ch):
        try:
            return ch, esi.get(f"/characters/{ch['id']}/planets/", auth=True, cid=ch["id"]) or []
        except Exception as exc:  # noqa: BLE001 - 单角色失败不应拖垮整体
            return ch, exc

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(characters)))) as pool:
        for ch, res in pool.map(fetch_planets, characters):
            if isinstance(res, Exception):
                char_errors[ch["id"]] = str(res)
                continue
            for p in res:
                refs.append(
                    {
                        "character_id": ch["id"],
                        "character_name": ch["name"],
                        "planet_id": p.get("planet_id"),
                        "system_id": p.get("solar_system_id"),
                        # 殖民地列表自带 last_update：CCP 最后一次重算这个殖民地的时间
                        "last_update": p.get("last_update"),
                    }
                )

    # 2. 殖民地详情（并行）
    def fetch_detail(ref):
        try:
            detail = esi.get(
                f"/characters/{ref['character_id']}/planets/{ref['planet_id']}/",
                auth=True,
                cid=ref["character_id"],
            )
            return ref, detail
        except Exception as exc:  # noqa: BLE001
            return ref, exc

    details: list[tuple[dict, dict]] = []
    if refs:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for ref, detail in pool.map(fetch_detail, refs):
                if isinstance(detail, Exception) or not detail:
                    continue
                details.append((ref, detail))

    # 3. 星球类型 / 星系安全等级
    planet_meta: dict[int, dict] = {}
    system_meta: dict[int, dict] = {}

    def fetch_planet_meta(pid):
        try:
            data = esi.get(f"/universe/planets/{pid}/", ttl=86400) or {}
        except Exception:  # noqa: BLE001
            data = {}
        return pid, data

    def fetch_system_meta(sid):
        try:
            data = esi.get(f"/universe/systems/{sid}/", ttl=86400) or {}
        except Exception:  # noqa: BLE001
            data = {}
        return sid, data

    planet_ids = {ref["planet_id"] for ref, _ in details}
    system_ids = {ref["system_id"] for ref, _ in details if ref["system_id"]}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for pid, data in pool.map(fetch_planet_meta, planet_ids):
            planet_meta[pid] = data
    with ThreadPoolExecutor(max_workers=6) as pool:
        for sid, data in pool.map(fetch_system_meta, system_ids):
            system_meta[sid] = data

    # 4. 收集所有 id 并批量解析名字（含星球类型 id，否则星球类型标签会是空的）
    ids: set[int] = set()
    for ref, detail in details:
        # 注意：不要把 planet_id 放进来，/universe/names/ 不支持星球，混一个就整批失败
        if ref["system_id"]:
            ids.add(ref["system_id"])
        pmeta = planet_meta.get(ref["planet_id"]) or {}
        if pmeta.get("type_id"):
            ids.add(pmeta["type_id"])
        for pin in detail.get("pins") or []:
            if pin.get("type_id"):
                ids.add(pin["type_id"])
            ed = pin.get("extractor_details") or {}
            if ed.get("product_type_id"):
                ids.add(ed["product_type_id"])
            for item in pin.get("contents") or []:
                if item.get("type_id"):
                    ids.add(item["type_id"])
        for route in detail.get("routes") or []:
            if route.get("content_type_id"):
                ids.add(route["content_type_id"])

    name_map = esi.names(ids)

    # 5. 物资类型元数据（体积 / 分组 -> 层级）
    item_type_ids = set()
    for _, detail in details:
        for pin in detail.get("pins") or []:
            ed = pin.get("extractor_details") or {}
            if ed.get("product_type_id"):
                item_type_ids.add(ed["product_type_id"])
            for item in pin.get("contents") or []:
                if item.get("type_id"):
                    item_type_ids.add(item["type_id"])
    esi.prefetch_types(item_type_ids)

    # 6. 组装
    colonies = []
    for ref, detail in details:
        colonies.append(_build_colony(esi, ref, detail, name_map, planet_meta, system_meta, now))

    # 先按行星名（星系 + 罗马数字）排，没有提取器的殖民地单独放到最后
    colonies.sort(
        key=lambda c: (
            0 if c["extractors"] else 1,
            *planet_sort_key(c["planet_name"], c["system_name"]),
        )
    )

    summary = _build_summary(colonies, characters, char_errors, now)

    try:
        assets = build_assets(esi, characters)
    except Exception as exc:  # noqa: BLE001 - 资产统计失败不应拖垮行星数据
        assets = {
            "characters": [],
            "totals": {"types": 0, "units": 0.0, "volume_m3": 0.0, "locations": 0,
                       "structure_units": 0.0, "customs_units": 0.0,
                       "structure_volume_m3": 0.0, "customs_volume_m3": 0.0},
            "needs_reauth": [],
            "enabled": False,
            "error": str(exc),
        }

    # Jita 报价（Fuzzwork，15 分钟缓存）；拿不到就整块留空，不影响主流程
    prices = market.fetch_prices(PI_TYPE_IDS)
    summary["stock_value"] = stock_value(colonies, assets, prices)
    summary["prices_available"] = bool(prices)

    return {
        "generated_at": now,
        "generated_at_iso": _iso(now),
        "prices": {str(k): v for k, v in prices.items()},
        "characters": [
            {
                "id": ch["id"],
                "name": ch["name"],
                "error": char_errors.get(ch["id"]),
            }
            for ch in characters
        ],
        "summary": summary,
        "colonies": colonies,
        "assets": assets,
        "esi_stats": dict(getattr(esi, "stats", {})),
    }


def _build_colony(esi, ref, detail, name_map, planet_meta, system_meta, now) -> dict:
    planet_id = ref["planet_id"]
    system_id = ref["system_id"]
    pins_raw = detail.get("pins") or []
    links = detail.get("links") or []
    routes = detail.get("routes") or []

    pmeta = planet_meta.get(planet_id) or {}
    smeta = system_meta.get(system_id) or {}
    planet_type_name = name_map.get(pmeta.get("type_id"), "")
    security = smeta.get("security_status")
    if security is None and system_id:
        security = None

    # 工厂产物：从出向路由推断（路由 source = 工厂，content_type_id = 产物）
    factory_output: dict[int, int] = {}
    for route in routes:
        src = route.get("source_pin_id")
        ct = route.get("content_type_id")
        if src and ct:
            factory_output.setdefault(src, ct)

    pins = []
    extractors = []
    factories = []
    storages = []
    tier_units: dict[str, float] = {}       # 仓储里实打实的库存
    tier_estimate: dict[str, float] = {}    # 提取程序全程产出（按末次周期速率外推，偏保守）

    for pin in pins_raw:
        type_id = pin.get("type_id")
        if type_id:
            type_name_en = name_map.get(type_id) or esi.type_info(type_id)["name"]
        else:
            type_name_en = "未知建筑"
        type_name = i18n.structure(type_name_en)
        lower = (type_name_en or "").lower()

        kind = "other"
        if pin.get("extractor_details"):
            kind = "extractor"
        elif pin.get("factory_details") or pin.get("schematic_id"):
            # ESI 实际返回的是引脚上的 schematic_id；factory_details 只在部分版本/演示数据里有
            kind = "factory"
        elif "command center" in lower:
            kind = "command_center"
        elif "launchpad" in lower or "storage" in lower:
            kind = "storage"

        item = {
            "pin_id": pin.get("pin_id"),
            "type_id": type_id,
            "type_name": type_name,
            "type_name_en": type_name_en,
            "kind": kind,
            "kind_label": KIND_LABEL.get(kind, "其他"),
            "expiry_time": pin.get("expiry_time"),
            "install_time": pin.get("install_time"),
            "last_cycle_start": pin.get("last_cycle_start"),
            "schematic_id": (pin.get("factory_details") or {}).get("schematic_id"),
            "contents": [],
            "capacity_m3": None,
            "used_m3": None,
            "fill": None,
            "extractor": None,
            "factory": None,
        }

        # 提取器
        ed = pin.get("extractor_details")
        if ed:
            install = parse_time(pin.get("install_time"))
            expiry = parse_time(pin.get("expiry_time"))
            cycle = float(ed.get("cycle_time") or 0) or 1.0
            qty = float(ed.get("qty_per_cycle") or 0)
            duration = max((expiry - install), 1.0) if (install and expiry) else 0.0
            remaining = max((expiry - now), 0.0) if expiry else 0.0
            cycles_total = duration / cycle if duration else 0.0
            cycles_left = remaining / cycle if cycle else 0.0
            # qty_per_cycle 是「最近一次模拟的那个周期实际采到多少」，会随矿脉衰减一路变小，
            # 不是全程平均（实测某提取器：ESI 7,341，游戏面板全程平均 8,826，差 17%）。
            # 所以任何 × 周期数的外推都只是近似，est_remaining 偏乐观、est_total 偏保守。
            est_total = qty * cycles_total
            est_remaining = qty * cycles_left
            progress = (1 - remaining / duration) if duration else 0.0
            product_id = ed.get("product_type_id")
            ext = {
                "pin_id": pin.get("pin_id"),
                "product_id": product_id,
                "product": i18n.commodity(
                    product_id,
                    name_map.get(product_id) or (esi.type_info(product_id)["name"] if product_id else ""),
                ),
                "product_en": name_map.get(product_id)
                or (esi.type_info(product_id)["name"] if product_id else ""),
                "tier": _tier_of(esi, product_id) if product_id else "?",
                "expiry": expiry,
                "expiry_iso": _iso(expiry),
                "remaining": remaining,
                "duration": duration,
                "progress": max(0.0, min(1.0, progress)),
                "cycle_time": cycle,
                "qty_per_cycle": qty,
                "heads": len(ed.get("heads") or []),
                "head_radius": ed.get("head_radius"),
                "est_total": est_total,
                "est_done": est_total * max(0.0, min(1.0, progress)),
                "est_remaining": est_remaining,
                "rate_per_hour": qty / cycle * 3600.0 if cycle else 0.0,
                "expired": remaining <= 0,
            }
            item["extractor"] = ext
            extractors.append(ext)
            tier = ext["tier"]
            if tier != "?":
                tier_estimate[tier] = tier_estimate.get(tier, 0.0) + est_total

        # 工厂
        fd = pin.get("factory_details")
        schematic_id = (fd or {}).get("schematic_id") or pin.get("schematic_id")
        if schematic_id:
            product_id = factory_output.get(pin.get("pin_id"))
            product_name = ""
            product_en = ""
            tier = "?"
            if product_id:
                product_en = name_map.get(product_id) or esi.type_info(product_id)["name"]
                product_name = i18n.commodity(product_id, product_en)
                tier = _tier_of(esi, product_id)
            # 建筑类型决定加工层级：基础= P0→P1，高级= P1→P2/P3，高科技= P3→P4
            facility = ""
            if "basic industry" in lower:
                facility = "basic"
            elif "advanced industry" in lower:
                facility = "advanced"
            elif "high-tech" in lower or "high tech" in lower:
                facility = "hightech"

            fac = {
                "pin_id": pin.get("pin_id"),
                "schematic_id": schematic_id,
                "product_id": product_id,
                "product": product_name,
                "product_en": product_en,
                "tier": tier,
                "facility": facility,
            }
            item["factory"] = fac
            factories.append(fac)

        # 仓储 / 库存
        contents = pin.get("contents") or []
        if contents or kind == "storage":
            used = 0.0
            items = []
            for entry in contents:
                ctid = entry.get("type_id")
                amount = float(entry.get("amount") or 0)
                if not ctid or amount <= 0:
                    continue
                tinfo = esi.type_info(ctid)
                vol = float(tinfo.get("volume") or DEFAULT_ITEM_VOLUME)
                tier = _tier_of(esi, ctid)
                items.append(
                    {
                        "type_id": ctid,
                        "name": i18n.commodity(ctid, tinfo["name"]),
                        "name_en": tinfo["name"],
                        "amount": amount,
                        "volume": vol,
                        "volume_m3": amount * vol,
                        "tier": tier,
                    }
                )
                used += amount * vol
                if tier != "?":
                    tier_units[tier] = tier_units.get(tier, 0.0) + amount
            items.sort(key=lambda x: -x["amount"])
            capacity = _capacity_for(type_name_en)
            item["contents"] = items
            item["used_m3"] = used
            item["capacity_m3"] = capacity
            item["fill"] = (used / capacity) if capacity else None
            # 任何引脚里的物资都算库存（指挥中心、工厂缓冲区也会存货），
            # 只有发射平台/储存设施才计入容量占比。
            # 空仓储也要留着——容量和 0% 占用本身就是信息，别让它从界面上消失。
            if items or kind == "storage":
                storages.append(
                    {
                        "pin_id": pin.get("pin_id"),
                        "type_name": type_name,
                        "type_name_en": type_name_en,
                        "kind": kind,
                        "kind_label": KIND_LABEL.get(kind, "其他"),
                        "is_storage": kind == "storage",
                        "capacity_m3": capacity,
                        "used_m3": used,
                        "fill": item["fill"],
                        "items": items,
                    }
                )

        pins.append(item)

    # 「游戏内数据时间」：优先用殖民地列表里的 last_update（CCP 最后一次重算这个殖民地的时间）。
    # 拿不到时退回引脚时间戳的最大值（工厂周期开始 / 提取程序安装）。
    # 它比 HTTP 缓存时间更能说明"这堆数字是游戏里哪一刻的"——纯工厂殖民地尤其明显。
    data_time = parse_time(ref.get("last_update"))
    if data_time is None:
        stamps = []
        for pin in pins_raw:
            for key in ("last_cycle_start", "install_time"):
                t = parse_time(pin.get(key))
                if t:
                    stamps.append(t)
        data_time = max(stamps) if stamps else None

    next_expiry = min((e["expiry"] for e in extractors if e["expiry"]), default=None)
    # 「下一次到期」只算还在运行的程序，已到期的单独统计
    next_future = min((e["expiry"] for e in extractors if e["expiry"] and e["expiry"] > now), default=None)
    storage_m3 = sum(s["used_m3"] or 0 for s in storages if s["is_storage"])
    capacity_m3 = sum(s["capacity_m3"] or 0 for s in storages if s["is_storage"])
    all_m3 = sum(s["used_m3"] or 0 for s in storages)
    remaining = (next_expiry - now) if next_expiry else None

    if not extractors:
        urgency = "idle"
    elif remaining is None or remaining <= 0:
        urgency = "expired"
    elif remaining < 2 * 3600:
        urgency = "critical"
    elif remaining < 12 * 3600:
        urgency = "warning"
    else:
        urgency = "ok"

    return {
        "character_id": ref["character_id"],
        "character_name": ref["character_name"],
        "planet_id": planet_id,
        "planet_name": pmeta.get("name") or name_map.get(planet_id) or f"Planet #{planet_id}",
        "planet_type": i18n.planet_type(_planet_type_label(planet_type_name)),
        "planet_type_raw": planet_type_name,
        "system_id": system_id,
        "system_name": smeta.get("name") or name_map.get(system_id) or "",
        "security": round(float(security), 2) if security is not None else None,
        "sec_class": _sec_class(security),
        "pin_count": len(pins),
        "link_count": len(links),
        "route_count": len(routes),
        "data_time": data_time,
        "data_time_iso": _iso(data_time),
        "data_age": (now - data_time) if data_time else None,
        "next_expiry": next_expiry,
        "next_expiry_iso": _iso(next_expiry),
        "next_future_expiry": next_future,
        "remaining": remaining,
        "urgency": urgency,
        "extractors": extractors,
        "factories": factories,
        "storages": storages,
        "storage_m3": storage_m3,
        "capacity_m3": capacity_m3,
        "all_m3": all_m3,
        "fill": (storage_m3 / capacity_m3) if capacity_m3 else None,
        "tier_units": tier_units,
        "tier_estimate": tier_estimate,
        "pins": pins,
        "links": links,
        "routes": [
            {
                "route_id": r.get("route_id"),
                "source_pin_id": r.get("source_pin_id"),
                "destination_pin_id": r.get("destination_pin_id"),
                "content_type_id": r.get("content_type_id"),
                "content": i18n.commodity(
                    r.get("content_type_id"), name_map.get(r.get("content_type_id"), "")
                ),
                "quantity": r.get("quantity"),
                "path_type": r.get("path_type"),
            }
            for r in routes
        ],
    }


def _build_summary(colonies, characters, char_errors, now) -> dict:
    tier_units: dict[str, float] = {}
    tier_estimate: dict[str, float] = {}
    for c in colonies:
        for k, v in (c.get("tier_units") or {}).items():
            tier_units[k] = tier_units.get(k, 0.0) + v
        for k, v in (c.get("tier_estimate") or {}).items():
            tier_estimate[k] = tier_estimate.get(k, 0.0) + v

    expiring_2h = expiring_24h = expired = 0
    next_expiry = None
    for c in colonies:
        if c["urgency"] == "expired":
            expired += 1
        elif c["urgency"] in ("critical", "warning"):
            if c["remaining"] is not None and c["remaining"] <= 24 * 3600:
                expiring_24h += 1
            if c["remaining"] is not None and c["remaining"] <= 2 * 3600:
                expiring_2h += 1
        if c.get("next_future_expiry") and (next_expiry is None or c["next_future_expiry"] < next_expiry):
            next_expiry = c["next_future_expiry"]

    storage = sum(c["storage_m3"] or 0 for c in colonies)
    capacity = sum(c["capacity_m3"] or 0 for c in colonies)
    storage_units = sum(
        i["amount"] for c in colonies for s in c.get("storages", []) for i in s.get("items", [])
    )

    return {
        "characters": len(characters),
        "characters_failed": len(char_errors),
        "colonies": len(colonies),
        "extractors": sum(len(c["extractors"]) for c in colonies),
        "factories": sum(len(c["factories"]) for c in colonies),
        "expired": expired,
        "expiring_2h": expiring_2h,
        "expiring_24h": expiring_24h,
        "storage_m3": storage,
        "storage_units": storage_units,
        "capacity_m3": capacity,
        "fill": (storage / capacity) if capacity else None,
        "tier_units": tier_units,
        "tier_estimate": tier_estimate,
        "next_expiry": next_expiry,
        "next_expiry_iso": _iso(next_expiry),
    }


# --------------------------------------------------------------------------
# 库存报表
# --------------------------------------------------------------------------

def build_inventory(overview: dict, prev: dict | None = None) -> dict:
    """把殖民地仓储和海关库存汇总成报表。

    prev: {(character_id, planet_id, type_id): amount} 上一次快照，只用于殖民地行
    """
    prev = prev or {}
    rows: dict[tuple, dict] = {}
    by_type: dict[int, dict] = {}

    def add_row(cid, cname, pid, pname, source, item):
        key = (cid, source, pid, item["type_id"])
        row = rows.get(key)
        if row is None:
            row = {
                "character_id": cid,
                "character_name": cname,
                "planet_id": pid,
                "planet_name": pname,
                "source": source,
                "source_label": {
                    "colony": "海关+殖民地",
                    "customs": "海关+殖民地",
                    "structure": "建筑",
                }.get(source, source),
                "type_id": item["type_id"],
                "name": item["name"],
                "name_en": item.get("name_en", ""),
                "tier": item["tier"],
                "amount": 0.0,
                "volume_m3": 0.0,
            }
            rows[key] = row
        row["amount"] += item["amount"]
        row["volume_m3"] += item["volume_m3"]

        agg = by_type.setdefault(
            item["type_id"],
            {
                "type_id": item["type_id"],
                "name": item["name"],
                "name_en": item.get("name_en", ""),
                "tier": item["tier"],
                "amount": 0.0,
                "volume_m3": 0.0,
                "colonies": 0,
                "colony_amount": 0.0,
                "customs_amount": 0.0,
                "structure_amount": 0.0,
                "sources": set(),
            },
        )
        agg["amount"] += item["amount"]
        agg["volume_m3"] += item["volume_m3"]
        agg["colonies"] += 1
        agg["sources"].add(source)
        if source in ("colony", "customs", "structure"):
            agg[f"{source}_amount"] += item["amount"]

    # 殖民地仓储
    for c in overview.get("colonies", []):
        for storage in c.get("storages", []):
            for item in storage.get("items", []):
                add_row(
                    c["character_id"],
                    c["character_name"],
                    c["planet_id"],
                    c["planet_name"],
                    "colony",
                    item,
                )

    # 资产库存：按「海关 / 空间站」两个桶分别入表
    for ch in (overview.get("assets") or {}).get("characters", []):
        if not ch.get("ok"):
            continue
        for key, bucket in (ch.get("buckets") or {}).items():
            for item in bucket.get("items", []):
                add_row(ch["id"], ch["name"], None, bucket["label"], key, item)

    out_rows = []
    for (cid, source, pid, _tid), row in rows.items():
        if source == "colony":
            prev_amount = prev.get((cid, pid, row["type_id"]))
            row["delta"] = (row["amount"] - prev_amount) if prev_amount is not None else None
        else:
            row["delta"] = None
        out_rows.append(row)

    out_rows.sort(key=lambda r: (r["tier"], -r["amount"]))

    prices = {
        int(k): v for k, v in ((overview.get("prices") or {}).items())
    }
    for tid, agg in by_type.items():
        total_prev = sum(v for k, v in prev.items() if k[2] == tid)
        agg["delta"] = (agg["amount"] - total_prev) if prev else None
        agg["sources"] = sorted(agg["sources"])
        # 报表里把「殖民地」和「海关」并成一列
        agg["colony_customs_amount"] = agg["colony_amount"] + agg["customs_amount"]
        # Jita 最高买单 / 估值（P0 也给单价，但总估值不含 P0）
        price = prices.get(tid) or {}
        agg["buy_price"] = price.get("buy") or 0.0
        agg["sell_price"] = price.get("sell") or 0.0
        agg["value"] = agg["amount"] * agg["buy_price"]

    out_by_type = sorted(by_type.values(), key=lambda r: (r["tier"], -r["amount"]))

    return {
        "rows": out_rows,
        "by_type": out_by_type,
        "totals": {
            "rows": len(out_rows),
            "types": len(out_by_type),
            "units": sum(r["amount"] for r in out_rows),
            "volume_m3": sum(r["volume_m3"] for r in out_rows),
            "colony_units": sum(r["amount"] for r in out_rows if r["source"] == "colony"),
            "colony_customs_units": sum(
                r["amount"] for r in out_rows if r["source"] in ("colony", "customs")
            ),
            "customs_units": sum(r["amount"] for r in out_rows if r["source"] == "customs"),
            "structure_units": sum(r["amount"] for r in out_rows if r["source"] == "structure"),
            "value": sum(r["value"] for r in out_by_type if r["tier"] != "P0"),
        },
    }


def snapshot_map(overview: dict) -> dict:
    """生成快照用的扁平字典。"""
    data = {}
    for c in overview.get("colonies", []):
        for storage in c.get("storages", []):
            for item in storage.get("items", []):
                key = (c["character_id"], c["planet_id"], item["type_id"])
                data[key] = data.get(key, 0.0) + item["amount"]
    return data


# --------------------------------------------------------------------------
# 海关 / 资产库存
# --------------------------------------------------------------------------

def fetch_assets(esi, character_id: int, max_pages: int = 30) -> list[dict]:
    """分页拉取角色全部资产。ESI 每页 1000 条，页数在 X-Pages 头里。"""
    path = f"/characters/{character_id}/assets/"
    data, headers = esi.get(path, params={"page": 1}, auth=True, cid=character_id, with_headers=True)
    items = list(data or [])
    try:
        pages = int((headers or {}).get("X-Pages") or 1)
    except (TypeError, ValueError):
        pages = 1
    for page in range(2, min(pages, max_pages) + 1):
        chunk = esi.get(path, params={"page": page}, auth=True, cid=character_id)
        items.extend(chunk or [])
    return items


# 资产位置分类：ESI 的 location_type 只有 station / solar_system / item / other，
# 无法区分海关和玩家建筑（官方 issue #1281 至今 open），所以只能可靠地区分"空间站"和"其它"。
BUCKET_LABELS = {
    "structure": "建筑",
    "customs": "海关",
}


def _classify_location(esi, cid, location_type, location_id, asset_index, can_resolve):
    """判断一条资产该算「建筑」还是「海关」，返回 (bucket, 位置名)。

    海关和玩家建筑在 ESI 里都是 location_type=item，只能靠解析建筑名区分：
      station                -> 建筑
      item + 能解析成建筑     -> 建筑（带回建筑名）
      item + 是自己的货柜/飞船 -> 顺着父级往上找，最终落到 station 或建筑
      item + 解析不到         -> 海关
      other / solar_system   -> 不统计
    """
    lt = (location_type or "").lower()
    loc_id = location_id
    for _ in range(10):
        if lt == "station":
            return "structure", None
        if lt != "item":
            return None, None

        parent = asset_index.get(loc_id)
        if parent:
            # 这个 location_id 本身就是自己的物品（货柜、飞船），继续往上
            lt = (parent.get("location_type") or "").lower()
            loc_id = parent.get("location_id")
            continue

        if can_resolve:
            info = esi.structure_info(cid, loc_id)
            if info:
                return "structure", info.get("name") or ""
        return "customs", None
    return "customs", None


def build_assets(esi, characters: list[dict]) -> dict:
    """统计角色资产里的 PI 物资，按「建筑 / 海关」分开，并按角色汇总。"""
    out = {
        "characters": [],
        "totals": {"types": 0, "units": 0.0, "volume_m3": 0.0, "locations": 0,
                   "structure_units": 0.0, "customs_units": 0.0,
                   "structure_volume_m3": 0.0, "customs_volume_m3": 0.0},
        "needs_reauth": [],
        "structures_available": True,
        "enabled": True,
    }

    for ch in characters:
        entry = {
            "id": ch["id"],
            "name": ch["name"],
            "ok": False,
            "error": None,
            "needs_reauth": False,
            "types": 0,
            "units": 0.0,
            "volume_m3": 0.0,
            "locations": 0,
            "items": [],
            "buckets": {},
            "location_list": [],
        }

        scopes = ch.get("scopes") or ""
        if scopes and ASSET_SCOPE not in scopes:
            entry["error"] = "缺少资产读取权限，需要重新授权"
            entry["needs_reauth"] = True
            out["needs_reauth"].append({"id": ch["id"], "name": ch["name"]})
            out["characters"].append(entry)
            continue

        can_resolve = (not scopes) or (STRUCT_SCOPE in scopes)
        if not can_resolve:
            out["structures_available"] = False

        try:
            assets = fetch_assets(esi, ch["id"])
        except Exception as exc:  # noqa: BLE001 - 单个角色失败不影响整体
            entry["error"] = str(exc)
            out["characters"].append(entry)
            continue

        asset_index = {a.get("item_id"): a for a in assets if a.get("item_id")}
        buckets: dict[str, dict] = {}
        locs: dict[int, dict] = {}

        for asset in assets:
            type_id = asset.get("type_id")
            qty = float(asset.get("quantity") or 0)
            if not type_id or qty <= 0 or int(type_id) not in PI_TYPE_IDS:
                continue

            bucket_key, loc_name = _classify_location(
                esi,
                ch["id"],
                asset.get("location_type"),
                asset.get("location_id"),
                asset_index,
                can_resolve,
            )
            if not bucket_key:
                continue

            info = esi.type_info(type_id)
            vol = float(info.get("volume") or DEFAULT_ITEM_VOLUME)

            bucket = buckets.setdefault(
                bucket_key,
                {"key": bucket_key, "label": BUCKET_LABELS[bucket_key], "items": {}, "locations": set()},
            )
            agg = bucket["items"].setdefault(
                int(type_id),
                {
                    "type_id": int(type_id),
                    "name": i18n.commodity(type_id, info.get("name")),
                    "name_en": info.get("name", ""),
                    "tier": _tier_of(esi, type_id),
                    "amount": 0.0,
                    "volume_m3": 0.0,
                },
            )
            agg["amount"] += qty
            agg["volume_m3"] += qty * vol
            bucket["locations"].add(asset.get("location_id"))

            loc = locs.setdefault(
                asset.get("location_id"),
                {
                    "location_id": asset.get("location_id"),
                    "location_type": asset.get("location_type") or "",
                    "location_name": loc_name or "",
                    "bucket": bucket_key,
                    "units": 0.0,
                    "volume_m3": 0.0,
                    "types": set(),
                },
            )
            if loc_name and not loc["location_name"]:
                loc["location_name"] = loc_name
            loc["units"] += qty
            loc["volume_m3"] += qty * vol
            loc["types"].add(int(type_id))

        flat_items: dict[int, dict] = {}
        for key in ("customs", "structure"):
            b = buckets.get(key)
            if not b:
                continue
            items = sorted(b["items"].values(), key=lambda x: (x["tier"], -x["amount"]))
            entry["buckets"][key] = {
                "key": key,
                "label": b["label"],
                "types": len(items),
                "units": sum(i["amount"] for i in items),
                "volume_m3": sum(i["volume_m3"] for i in items),
                "locations": len(b["locations"]),
                "items": items,
            }
            for i in items:
                tgt = flat_items.setdefault(
                    i["type_id"],
                    {"type_id": i["type_id"], "name": i["name"], "name_en": i["name_en"],
                     "tier": i["tier"], "amount": 0.0, "volume_m3": 0.0},
                )
                tgt["amount"] += i["amount"]
                tgt["volume_m3"] += i["volume_m3"]

        items = sorted(flat_items.values(), key=lambda x: (x["tier"], -x["amount"]))
        entry.update(
            {
                "ok": True,
                "items": items,
                "types": len(items),
                "units": sum(i["amount"] for i in items),
                "volume_m3": sum(i["volume_m3"] for i in items),
                "locations": len(locs),
                "location_list": sorted(
                    [{**v, "types": len(v["types"])} for v in locs.values()],
                    key=lambda x: -x["units"],
                )[:30],
            }
        )
        out["characters"].append(entry)

        out["totals"]["types"] += len(items)
        out["totals"]["units"] += entry["units"]
        out["totals"]["volume_m3"] += entry["volume_m3"]
        out["totals"]["locations"] += entry["locations"]
        for key in ("structure", "customs"):
            b = entry["buckets"].get(key)
            if b:
                out["totals"][f"{key}_units"] += b["units"]
                out["totals"][f"{key}_volume_m3"] += b["volume_m3"]

    return out
