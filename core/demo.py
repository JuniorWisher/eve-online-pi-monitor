"""演示模式：不连 EVE，用一套假的 ESI 数据走完整解析流程。

用途：没配 client_id / 没登录时也能看到界面和统计逻辑是否正常。
运行 `python app.py --demo` 启用。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

DEMO_CHARACTERS = [
    {
        "id": 1001,
        "name": "Kadir Vex",
        "owner": "owner-hash-a",
        "scopes": "esi-planets.manage_planets.v1 esi-assets.read_assets.v1 esi-universe.read_structures.v1",
    },
    {
        "id": 1002,
        "name": "Solan Marr",
        "owner": "owner-hash-a",
        "scopes": "esi-planets.manage_planets.v1 esi-assets.read_assets.v1 esi-universe.read_structures.v1",
    },
    {
        "id": 1003,
        "name": "Vex Amarr",
        "owner": "owner-hash-a",
        "scopes": "esi-planets.manage_planets.v1 esi-assets.read_assets.v1 esi-universe.read_structures.v1",
    },
]

# 演示用的资产样本，覆盖各种 location_type
DEMO_STRUCTURES = {61000001: "星城 · 测试建筑"}  # 能解析出名字的玩家建筑

DEMO_ASSETS = {
    1001: [
        # 空间站机库（station -> 建筑）
        {"item_id": 900000004, "type_id": 34, "quantity": 5000, "location_id": 60060001,
         "location_type": "station", "location_flag": "Hangar", "is_singleton": False},
        {"item_id": 900000008, "type_id": 2267, "quantity": 5000, "location_id": 60060001,
         "location_type": "station", "location_flag": "Hangar", "is_singleton": False},
        {"item_id": 900000009, "type_id": 2867, "quantity": 6, "location_id": 60060001,
         "location_type": "station", "location_flag": "Hangar", "is_singleton": False},
        # 货柜本身（放在空间站里），供下面的 item 溯源
        {"item_id": 60070001, "type_id": 3465, "quantity": 1, "location_id": 60060001,
         "location_type": "station", "location_flag": "Hangar", "is_singleton": True},
        # 货柜里的 PI（item -> 顺着父级找到空间站 -> 建筑）
        {"item_id": 900000010, "type_id": 2398, "quantity": 1000, "location_id": 60070001,
         "location_type": "item", "location_flag": "Unlocked", "is_singleton": False},
        # 玩家建筑里（item -> 解析出建筑名 -> 建筑）
        {"item_id": 900000012, "type_id": 2867, "quantity": 30, "location_id": 61000001,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
        # 海关（item -> 解析不出建筑名 -> 海关）
        {"item_id": 900000013, "type_id": 2398, "quantity": 500, "location_id": 61000002,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
        {"item_id": 900000001, "type_id": 2267, "quantity": 30000, "location_id": 61000003,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
        # 太空里（solar_system -> 不统计）
        {"item_id": 900000011, "type_id": 2398, "quantity": 999, "location_id": 30000142,
         "location_type": "solar_system", "location_flag": "AutoFit", "is_singleton": False},
    ],
    1002: [
        {"item_id": 900000005, "type_id": 2867, "quantity": 18, "location_id": 61000004,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
        {"item_id": 900000006, "type_id": 9834, "quantity": 96, "location_id": 61000004,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
        {"item_id": 900000007, "type_id": 2396, "quantity": 4200, "location_id": 61000005,
         "location_type": "item", "location_flag": "Hangar", "is_singleton": False},
    ],
}

STRUCTS = {
    10001: ("Temperate Extractor Control Unit", 1063),
    10002: ("Temperate Launchpad", 1030),
    10003: ("Temperate Storage Facility", 1029),
    10004: ("Temperate Basic Industry Facility", 1028),
    10005: ("Temperate Advanced Industry Facility", 1028),
    10006: ("Temperate High-Tech Production Plant", 1028),
    10007: ("Standard Temperate Command Center", 1027),
}

COMMODITIES = {
    2267: ("Base Metals", 1032),
    2268: ("Aqueous Liquids", 1033),
    2073: ("Microorganisms", 1035),
    2398: ("Reactive Metals", 1042),
    2396: ("Biofuels", 1042),
    3645: ("Water", 1042),
    2401: ("Chiral Structures", 1042),
    2400: ("Toxic Metals", 1042),
    9832: ("Coolant", 1034),
    3689: ("Mechanical Parts", 1034),
    9836: ("Consumer Electronics", 1034),
    9848: ("Robotics", 1040),
    9834: ("Guidance Systems", 1040),
    2358: ("Biotech Research Reports", 1040),
    2867: ("Broadcast Node", 1041),
    2875: ("Sterile Conduits", 1041),
}

GROUPS = {
    1027: "Command Centers",
    1028: "Processors",
    1029: "Storage Facilities",
    1030: "Spaceports",
    1032: "Planet Solid - Raw Resource",
    1033: "Planet Liquid-Gas - Raw Resource",
    1035: "Planet Organic - Raw Resource",
    1034: "Refined Commodities - Tier 2",
    1040: "Specialized Commodities - Tier 3",
    1041: "Advanced Commodities - Tier 4",
    1042: "Basic Commodities - Tier 1",
    1063: "Extractor Control Units",
}

# 真实体积（m3/单位），来自 ESI /universe/types/
VOLUME_BY_GROUP = {
    1032: 0.005,
    1033: 0.005,
    1035: 0.005,
    1042: 0.19,
    1034: 0.75,
    1040: 3.0,
    1041: 50.0,
}

PLANETS = {
    40000001: ("Jita IV", 11, 30000142),
    40000002: ("Amarr VIII (Oris)", 13, 30002187),
    40000003: ("Rens VI - Moon 8", 12, 30002510),
    40000004: ("Hek VIII - Moon 12", 2016, 30002053),
    40000005: ("Perimeter II", 11, 30000144),
    40000006: ("Jita II", 11, 30000142),
}

PLANET_TYPES = {
    11: "Planet (Temperate)",
    12: "Planet (Ice)",
    13: "Planet (Oceanic)",
    2016: "Planet (Barren)",
}

SYSTEMS = {
    30000142: ("Jita", 0.9465),
    30002187: ("Amarr", 0.9645),
    30002510: ("Rens", 0.8645),
    30002053: ("Hek", 0.5),
    30000144: ("Perimeter", 0.9412),
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeESI:
    """模拟 ESI 的 get/post 接口，签名与真实客户端一致。"""

    def __init__(self, now: float | None = None):
        self.now = now or time.time()
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}
        self._colonies = self._build_colonies()

    # -- 数据构造 ---------------------------------------------------------

    def _build_colonies(self) -> dict:
        n = self.now
        return {
            1001: [
                {
                    "planet_id": 40000001,
                    "system_id": 30000142,
                    "pins": [
                        {
                            "pin_id": 101,
                            "type_id": 10007,
                            "install_time": _iso(n - 86400 * 30),
                            "expiry_time": None,
                            "contents": [{"type_id": 2267, "amount": 4200}],
                        },
                        {
                            "pin_id": 102,
                            "type_id": 10001,
                            "install_time": _iso(n - 8 * 3600),
                            "expiry_time": _iso(n + 45 * 60),
                            "extractor_details": {
                                "cycle_time": 300,
                                "head_radius": 0.018,
                                "heads": [{"head_id": i, "latitude": 0.1 * i, "longitude": -0.2 * i} for i in range(1, 7)],
                                "product_type_id": 2268,
                                "qty_per_cycle": 1480,
                            },
                        },
                        {
                            "pin_id": 103,
                            "type_id": 10004,
                            "factory_details": {"schematic_id": 121},
                        },
                        {
                            "pin_id": 104,
                            "type_id": 10002,
                            "contents": [
                                # 仿照真实场景：P1 一堆 + 少量 P4，验证不会被截断
                                {"type_id": 2398, "amount": 3360},
                                {"type_id": 2401, "amount": 3360},
                                {"type_id": 2400, "amount": 3360},
                                {"type_id": 3645, "amount": 3100},
                                {"type_id": 2875, "amount": 116},
                            ],
                        },
                    ],
                    "links": [
                        {"source_pin_id": 102, "destination_pin_id": 104, "link_level": 0},
                        {"source_pin_id": 104, "destination_pin_id": 103, "link_level": 0},
                    ],
                    "routes": [
                        {
                            "route_id": 1,
                            "source_pin_id": 103,
                            "destination_pin_id": 104,
                            "content_type_id": 2398,
                            "quantity": 20,
                            "path_type": 0,
                        }
                    ],
                },
                {
                    "planet_id": 40000002,
                    "system_id": 30002187,
                    "pins": [
                        {
                            "pin_id": 201,
                            "type_id": 10001,
                            "install_time": _iso(n - 3600),
                            "expiry_time": _iso(n + 6 * 3600),
                            "extractor_details": {
                                "cycle_time": 900,
                                "head_radius": 0.021,
                                "heads": [{"head_id": i, "latitude": 0.3, "longitude": 0.1 * i} for i in range(1, 5)],
                                "product_type_id": 2267,
                                "qty_per_cycle": 5100,
                            },
                        },
                        {
                            "pin_id": 202,
                            "type_id": 10005,
                            "factory_details": {"schematic_id": 205},
                        },
                        {
                            "pin_id": 203,
                            "type_id": 10002,
                            "contents": [
                                {"type_id": 3689, "amount": 9900},
                                {"type_id": 9836, "amount": 640},
                                {"type_id": 9832, "amount": 210},
                            ],
                        },
                    ],
                    "links": [{"source_pin_id": 201, "destination_pin_id": 203, "link_level": 0}],
                    "routes": [
                        {
                            "route_id": 2,
                            "source_pin_id": 202,
                            "destination_pin_id": 203,
                            "content_type_id": 3689,
                            "quantity": 10,
                            "path_type": 0,
                        }
                    ],
                },
            ],
            1003: [
                {
                    # 提取器 + 4 个 P1 工厂：用来验证"预测公式"分支
                    "planet_id": 40000006,
                    "system_id": 30000142,
                    "pins": [
                        {"pin_id": 601, "type_id": 10007},
                        {
                            "pin_id": 602,
                            "type_id": 10001,
                            "install_time": _iso(n - 6 * 3600),
                            "expiry_time": _iso(n + 24 * 3600),
                            "extractor_details": {
                                "cycle_time": 300,
                                "head_radius": 0.02,
                                "heads": [
                                    {"head_id": i, "latitude": 0.05 * i, "longitude": -0.1 * i}
                                    for i in range(1, 11)
                                ],
                                "product_type_id": 2268,
                                "qty_per_cycle": 2100,
                            },
                        },
                        {"pin_id": 611, "type_id": 10004, "factory_details": {"schematic_id": 121}},
                        {"pin_id": 612, "type_id": 10004, "factory_details": {"schematic_id": 122}},
                        {"pin_id": 613, "type_id": 10004, "factory_details": {"schematic_id": 123}},
                        {"pin_id": 614, "type_id": 10004, "factory_details": {"schematic_id": 124}},
                        {"pin_id": 603, "type_id": 10002, "contents": [{"type_id": 2398, "amount": 47000}]},
                    ],
                    "links": [],
                    "routes": [],
                },
            ],
            1002: [
                {
                    "planet_id": 40000003,
                    "system_id": 30002510,
                    "pins": [
                        {
                            "pin_id": 301,
                            "type_id": 10001,
                            "install_time": _iso(n - 20 * 3600),
                            "expiry_time": _iso(n + 30 * 3600),
                            "extractor_details": {
                                "cycle_time": 1800,
                                "head_radius": 0.03,
                                "heads": [{"head_id": i, "latitude": 0.5, "longitude": -0.4} for i in range(1, 11)],
                                "product_type_id": 2073,
                                "qty_per_cycle": 9600,
                            },
                        },
                        {
                            "pin_id": 302,
                            "type_id": 10006,
                            "factory_details": {"schematic_id": 310},
                        },
                        {
                            "pin_id": 303,
                            "type_id": 10003,
                            "contents": [
                                {"type_id": 9848, "amount": 320},
                                {"type_id": 9834, "amount": 96},
                            ],
                        },
                        {
                            "pin_id": 304,
                            "type_id": 10002,
                            "contents": [{"type_id": 2396, "amount": 5400}],
                        },
                    ],
                    "links": [],
                    "routes": [
                        {
                            "route_id": 3,
                            "source_pin_id": 302,
                            "destination_pin_id": 304,
                            "content_type_id": 9848,
                            "quantity": 5,
                            "path_type": 0,
                        }
                    ],
                },
                {
                    # 纯加工殖民地：没有提取器，排序时应该被排到最后
                    "planet_id": 40000005,
                    "system_id": 30000144,
                    "pins": [
                        {
                            "pin_id": 501,
                            "type_id": 10006,
                            "factory_details": {"schematic_id": 402},
                        },
                        {
                            "pin_id": 502,
                            "type_id": 10002,
                            "contents": [
                                {"type_id": 2867, "amount": 24},     # 已产出的广播节点 P4
                                {"type_id": 9848, "amount": 1200},   # 待加工的 P3 原料（机器人技术）
                                {"type_id": 9834, "amount": 1200},   # 制导系统
                            ],
                        },
                    ],
                    "links": [],
                    "routes": [
                        {
                            "route_id": 5,
                            "source_pin_id": 501,
                            "destination_pin_id": 502,
                            "content_type_id": 2867,
                            "quantity": 5,
                            "path_type": 0,
                        }
                    ],
                },
                {
                    "planet_id": 40000004,
                    "system_id": 30002053,
                    "pins": [
                        {
                            "pin_id": 401,
                            "type_id": 10001,
                            "install_time": _iso(n - 40 * 3600),
                            "expiry_time": _iso(n - 4 * 3600),
                            "extractor_details": {
                                "cycle_time": 300,
                                "head_radius": 0.015,
                                "heads": [{"head_id": i, "latitude": 0.05, "longitude": 0.05} for i in range(1, 8)],
                                "product_type_id": 2268,
                                "qty_per_cycle": 2200,
                            },
                        },
                        {
                            "pin_id": 402,
                            "type_id": 10002,
                            "contents": [
                                {"type_id": 2867, "amount": 42},
                                {"type_id": 9848, "amount": 180},
                            ],
                        },
                    ],
                    "links": [],
                    "routes": [],
                },
            ],
        }

    # -- 模拟接口 ---------------------------------------------------------

    def get(self, path, params=None, *, auth=False, cid=None, force=False, ttl=None,
            with_headers=False):
        data = self._get(path, params, auth=auth, cid=cid)
        if with_headers:
            return data, {"X-Pages": "1"}
        return data

    def _get(self, path, params=None, *, auth=False, cid=None):
        self.stats["requests"] += 1
        if m := re.fullmatch(r"/characters/(\d+)/assets/", path):
            ch = int(m.group(1))
            page = int((params or {}).get("page") or 1)
            return list(DEMO_ASSETS.get(ch, [])) if page == 1 else []
        if m := re.fullmatch(r"/characters/(\d+)/planets/", path):
            ch = int(m.group(1))
            return [{"planet_id": c["planet_id"], "solar_system_id": c["system_id"]} for c in self._colonies.get(ch, [])]
        if m := re.fullmatch(r"/characters/(\d+)/planets/(\d+)/", path):
            ch, pid = int(m.group(1)), int(m.group(2))
            for c in self._colonies.get(ch, []):
                if c["planet_id"] == pid:
                    return {"links": c["links"], "pins": c["pins"], "routes": c["routes"]}
            return None
        if m := re.fullmatch(r"/universe/planets/(\d+)/", path):
            pid = int(m.group(1))
            name, tid, sid = PLANETS[pid]
            return {"planet_id": pid, "name": name, "system_id": sid, "type_id": tid}
        if m := re.fullmatch(r"/universe/systems/(\d+)/", path):
            sid = int(m.group(1))
            name, sec = SYSTEMS[sid]
            return {"system_id": sid, "name": name, "security_status": sec}
        if m := re.fullmatch(r"/universe/types/(\d+)/", path):
            tid = int(m.group(1))
            if tid in STRUCTS:
                name, gid = STRUCTS[tid]
                return {"type_id": tid, "name": name, "group_id": gid, "volume": 0.0}
            name, gid = COMMODITIES[tid]
            return {
                "type_id": tid,
                "name": name,
                "group_id": gid,
                "volume": VOLUME_BY_GROUP.get(gid, 0.01),
            }
        if m := re.fullmatch(r"/universe/groups/(\d+)/", path):
            gid = int(m.group(1))
            return {"group_id": gid, "name": GROUPS.get(gid, "Unknown")}
        return None

    def post(self, path, body):
        self.stats["requests"] += 1
        if path == "/universe/names/":
            out = []
            for i in body:
                i = int(i)
                if i in PLANETS:
                    out.append({"id": i, "name": PLANETS[i][0], "category": "planet"})
                elif i in SYSTEMS:
                    out.append({"id": i, "name": SYSTEMS[i][0], "category": "solar_system"})
                elif i in PLANET_TYPES:
                    out.append({"id": i, "name": PLANET_TYPES[i], "category": "type"})
                elif i in STRUCTS:
                    out.append({"id": i, "name": STRUCTS[i][0], "category": "type"})
                elif i in COMMODITIES:
                    out.append({"id": i, "name": COMMODITIES[i][0], "category": "type"})
            return out
        return []

    def names(self, ids):
        out = {}
        for i in ids:
            name = self._name_one(i)
            if name:
                out[int(i)] = name
        return out

    def _name_one(self, i):
        i = int(i)
        for table, idx in ((PLANETS, 0), (SYSTEMS, 0), (PLANET_TYPES, None), (STRUCTS, 0), (COMMODITIES, 0)):
            if i in table:
                return table[i] if idx is None else table[i][idx]
        return None

    def type_info(self, type_id):
        type_id = int(type_id)
        if type_id in STRUCTS:
            name, gid = STRUCTS[type_id]
            return {"type_id": type_id, "name": name, "group_id": gid, "volume": 0.0}
        name, gid = COMMODITIES.get(type_id, (f"Type #{type_id}", 0))
        return {
            "type_id": type_id,
            "name": name,
            "group_id": gid,
            "volume": VOLUME_BY_GROUP.get(gid, 0.01),
        }

    def prefetch_types(self, type_ids, workers=6):
        return None

    def group_name(self, group_id):
        return GROUPS.get(int(group_id or 0), "")

    def structure_info(self, cid, structure_id):
        name = DEMO_STRUCTURES.get(int(structure_id or 0))
        if not name:
            return None
        return {"structure_id": int(structure_id), "name": name}


def make_esi(now: float | None = None) -> FakeESI:
    return FakeESI(now)


def characters():
    return list(DEMO_CHARACTERS)
