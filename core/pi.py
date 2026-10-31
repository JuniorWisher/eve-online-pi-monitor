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
# 行星工业产量换算（游戏内固定配方，改版才会变）
P0_VOL = 0.005          # P0 单体体积
P1_VOL = 0.19           # P1 单体体积
P2_VOL = 0.75           # P2 单体体积
P0_PER_P1 = 150         # 3000 P0 → 20 P1
P1_PER_P2 = 16          # 80 P1 → 5 P2
BASIC_CYCLE = 1800      # 基础工业设施 30 分钟一周期
ADV_CYCLE = 3600        # 高级工业设施 60 分钟一周期
P1_PER_BASIC = 20       # 每周期产出
P2_PER_ADV = 5
# 满负荷运转时每个工厂的日产量
DAILY_BY_TIER = {"P1": 960, "P2": 120, "P3": 72, "P4": 24}
# 相邻两层之间的换算：上一层数量 × 系数 = 下一层数量
STEP_TO_NEXT = {"P0": 20 / 3000, "P1": 5 / 80, "P2": 3 / 20, "P3": 1 / 12}
TIER_ORDER = ["P0", "P1", "P2", "P3", "P4"]
TIER_VOL = {"P0": P0_VOL, "P1": P1_VOL, "P2": P2_VOL, "P3": 3.0, "P4": 50.0}
# P4 配方表：type_id -> (需要的 P3 种类数, 每种 P3 需要的 P2 种类数)
# 游戏里 P4 就这 8 种，按加工类型固定：
#   3 种 P3 → 18 个 P3 出 1 个 P4
#   2 种 P3 + 1 种 P1 → 12 个 P3 加 40 个 P1 出 1 个 P4
P4_RECIPE = {
    2867: (3, 2),  # 广播节点
    2868: (3, 3),  # 反破损快速反应无人机
    2869: (2, 2),  # 纳米-工厂
    2870: (2, 2),  # 有机砂浆喷注器
    2871: (3, 2),  # 递推计算模块
    2872: (3, 2),  # 自协调能源核心
    2875: (2, 2),  # 无菌管道
    2876: (3, 3),  # 湿件主机
}

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


def _bin_entry(b, end_used: float, peak_used: float, is_target: bool) -> dict:
    """拼一个仓储的预估条目（两个分支共用）。"""
    cap = float(b.get("capacity_m3") or 0)
    used = float(b.get("used_m3") or 0)

    def pct(v):
        return (v / cap) if cap > 0 else None

    return {
        "pin_id": b.get("pin_id"),
        "name": b.get("type_name"),
        "used_m3": used,
        "capacity_m3": cap,
        "fill": pct(used),
        "end_used_m3": end_used,
        "end_fill": pct(end_used),
        "peak_used_m3": peak_used,
        "peak_fill": pct(peak_used),
        "is_target": is_target,
    }


def _max_fill(per_bin: list, key: str) -> float | None:
    vals = [b[key] for b in per_bin if b[key] is not None]
    return max(vals) if vals else None


def _forecast(esi, extractors: list, factories: list, storages: list, decay: float = 0.4) -> dict | None:
    """按提取器全程外推，估算 P0/P1/P2 产量、大周期结束时的仓储占用。

    只对两类殖民地出预估：
      P1 星球 —— 有提取器，工厂全是基础（P0→P1）
      P2 星球 —— 有提取器，有基础 + 高级工厂，且高级工厂产物都是 P2（不能有 P3/P4）
    其余（纯加工、带 P3/P4、没有提取器）返回 None。
    """
    bins = [s for s in storages if s.get("is_storage")]
    cap = sum(float(s.get("capacity_m3") or 0) for s in bins)
    used = sum(float(s.get("used_m3") or 0) for s in bins)

    # 纯合成殖民地：没有提取器，只有工厂。
    # 按仓储里现有的原料、结合配方算最终产物。P0 无法跨星球运输，直接忽略。
    # 收益 = 产物价值 − 实际消耗掉的原料价值。
    has_p4 = any((f.get("tier") or "?") == "P4" for f in factories)
    if not extractors or has_p4:
        tiers = [(f.get("tier") or "?") for f in factories]
        known = [t for t in tiers if t in DAILY_BY_TIER]
        if not known:
            return None
        top = max(known, key=lambda t: int(t[1]))
        top_facs = [f for f in factories if (f.get("tier") or "?") == top]
        out_id = next((f.get("product_id") for f in top_facs if f.get("product_id")), None)
        recipe = P4_RECIPE.get(out_id) if top == "P4" else None

        # 仓储里各层的直接库存（忽略 P0）
        direct: dict[str, float] = {}
        inputs: list[dict] = []
        for st in bins:
            for it in st.get("items") or []:
                t = it.get("tier")
                if t not in TIER_ORDER or t == "P0":
                    continue
                amt = float(it.get("amount") or 0)
                if amt <= 0:
                    continue
                direct[t] = direct.get(t, 0.0) + amt
                inputs.append({"type_id": it.get("type_id"), "amount": amt, "tier": t})

        # P2 → P3 的系数：2 种 P2 各 10 个出 3 个 P3（3/20），3 种就是 3/30
        p3_n = recipe[1] if recipe else 2
        if not recipe:
            p3_facs = [f for f in factories if (f.get("tier") or "?") == "P3"]
            if p3_facs:
                # 用"原料种类数"（2 种或 3 种 P2），不是层级数
                p3_n = max(f.get("in_count") or 0 for f in p3_facs) or 2
        step = dict(STEP_TO_NEXT)
        step["P2"] = 3.0 / (10.0 * p3_n)

        consumed: dict[str, float] = {}
        if top == "P4":
            # 1 个 P3 需要多少 P2，1 个 P2 需要多少 P1
            k23 = 10.0 * p3_n / 3.0
            k12 = 16.0
            d1 = direct.get("P1", 0.0)
            d2 = direct.get("P2", 0.0)
            d3 = direct.get("P3", 0.0)
            if recipe and recipe[0] == 2:
                # 2 种 P3 + 1 种 P1：12 个 P3 加 40 个 P1 出 1 个
                # P1 既是直接原料，又要往上去做 P2→P3，联立解：
                #   12x = D3 + D2/k23 + (D1 - 40x)/(k12*k23)
                denom = 12.0 + 40.0 / (k12 * k23)
                out_units = (d3 + d2 / k23 + d1 / (k12 * k23)) / denom
                need_p3 = 12.0 * out_units
                direct_p1 = 40.0 * out_units
            else:
                # 3 种 P3：6+6+6 = 18 个 P3 出 1 个
                out_units = (d3 + d2 / k23 + d1 / (k12 * k23)) / 18.0
                need_p3 = 18.0 * out_units
                direct_p1 = 0.0

            # 按「直接用 P3 → 拿 P2 现做 → 拿 P1 现做」的顺序算实际消耗
            use3 = min(need_p3, d3)
            rest = need_p3 - use3
            use2 = min(rest * k23, d2)
            rest -= use2 / k23
            use1 = min(rest * k12 * k23, max(0.0, d1 - direct_p1))
            consumed = {"P3": use3, "P2": use2, "P1": direct_p1 + use1}
        else:
            # 其它层级沿用逐层折算
            out_units = 0.0
            for t2, amt in direct.items():
                i0, j0 = TIER_ORDER.index(t2), TIER_ORDER.index(top)
                if i0 >= j0:
                    continue
                f = 1.0
                for k in range(i0, j0):
                    f *= step[TIER_ORDER[k]]
                out_units += amt * f
                consumed[t2] = consumed.get(t2, 0.0) + amt

        input_volume = sum(amt * TIER_VOL.get(t, 0.0) for t, amt in consumed.items())
        output_volume = out_units * TIER_VOL.get(top, 0.0)

        # 产物堆到路由指向的那个仓储；没有路由就选容量最大的
        bin_ids = {b.get("pin_id") for b in bins}
        target = None
        for f in top_facs:
            if f.get("out_pin") in bin_ids:
                target = f.get("out_pin")
                break
        if target is None and bins:
            target = max(bins, key=lambda b: float(b.get("capacity_m3") or 0)).get("pin_id")

        per_bin = []
        for b in bins:
            cap_i = float(b.get("capacity_m3") or 0)
            used_i = float(b.get("used_m3") or 0)
            # 每个仓储扣掉的是"它自己装着的那些物资被消耗的部分"
            used_here = 0.0
            for it in b.get("items") or []:
                t = it.get("tier")
                tot = float(direct.get(t) or 0)
                if not tot:
                    continue
                ratio = min(1.0, float(consumed.get(t, 0)) / tot)
                used_here += float(it.get("amount") or 0) * float(it.get("volume") or 0) * ratio
            delta = -used_here
            if b.get("pin_id") == target:
                delta += output_volume
            end_i = max(0.0, used_i + delta)
            per_bin.append(
                {
                    "pin_id": b.get("pin_id"),
                    "name": b.get("type_name"),
                    "used_m3": used_i,
                    "capacity_m3": cap_i,
                    "fill": (used_i / cap_i) if cap_i > 0 else None,
                    "end_used_m3": end_i,
                    "end_fill": (end_i / cap_i) if cap_i > 0 else None,
                    "peak_used_m3": end_i,
                    "peak_fill": (end_i / cap_i) if cap_i > 0 else None,
                    "is_target": b.get("pin_id") == target,
                }
            )

        # 爆仓看的是"最满的那个仓储"，不是把所有仓储加起来的平均值
        fills = [b["end_fill"] for b in per_bin if b["end_fill"] is not None]
        end_fill = max(fills) if fills else None
        peak_fill = end_fill
        end_used = used - input_volume + output_volume

        return {
            "kind": f"{top}合成",
            "pure": True,
            "duration": 0.0,
            "p0_total": 0.0,
            "p0_by_product": {},
            "p1": 0.0,
            "p2": 0.0,
            "output": out_units,
            "output_tier": top,
            "output_id": out_id,
            "output_name": next((f.get("product") for f in top_facs if f.get("product")), ""),
            "recipe": list(recipe) if recipe else None,
            "inputs": inputs,
            "direct": direct,
            "consumed": consumed,
            "input_volume": input_volume,
            "p0_left": 0.0,
            "storages": per_bin,
            "accumulate_m3": max(0.0, end_used - used),
            "used_m3": used,
            "capacity_m3": cap,
            "end_fill": end_fill,
            "peak_fill": peak_fill,
            "peak_used_m3": max((b["peak_used_m3"] for b in per_bin), default=used),
            "peak_hour": 0.0,
            "overflow": bool(peak_fill is not None and peak_fill > 0.95),
        }

    duration = max((e.get("duration") or 0.0) for e in extractors)
    if duration <= 0:
        return None

    basic = [f for f in factories if f.get("facility") == "basic"]
    advanced = [f for f in factories if f.get("facility") == "advanced"]
    hightech = [f for f in factories if f.get("facility") == "hightech"]

    adv_tiers = {(f.get("tier") or "?") for f in advanced}
    if not hightech and advanced and adv_tiers <= {"P2", "?"}:
        kind = "P2"
    elif not advanced and not hightech:
        # 只有提取器、或提取器 + 基础工厂，都按 P1 星球算（纯采集时 P1 产量为 0）
        kind = "P1"
    else:
        return None

    # 预估 P0 产量：多个同类提取器合并，按产物分组
    p0_by_product: dict[int, float] = {}
    p0_total = 0.0
    for e in extractors:
        amt = float(e.get("est_total") or 0.0)
        p0_total += amt
        pid = e.get("product_id")
        if pid:
            p0_by_product[pid] = p0_by_product.get(pid, 0.0) + amt

    # P0 → P1：受基础工厂产能和 P0 总量双重限制
    blocks = duration / BASIC_CYCLE
    p1_cap = len(basic) * P1_PER_BASIC * blocks
    p1 = min(p1_cap, p0_total / P0_PER_P1) if basic else 0.0
    p0_left = max(0.0, p0_total - p1 * P0_PER_P1)

    if kind == "P2":
        adv_cycles = duration / ADV_CYCLE
        p2_cap = len(advanced) * P2_PER_ADV * adv_cycles
        p2 = min(p2_cap, p1 / P1_PER_P2)
        p1_left = max(0.0, p1 - p2 * P1_PER_P2)
        accumulate = p2 * P2_VOL + p1_left * P1_VOL + p0_left * P0_VOL
        out_id = next((f.get("product_id") for f in advanced if f.get("product_id")), None)
        out_name = next((f.get("product") for f in advanced if f.get("product")), "")
        output, tier = p2, "P2"
    else:
        p2 = 0.0
        accumulate = p1 * P1_VOL + p0_left * P0_VOL
        out_id = next((f.get("product_id") for f in basic if f.get("product_id")), None)
        out_name = next((f.get("product") for f in basic if f.get("product")), "")
        output, tier = p1, "P1"

    # ---- 峰值模拟 ----
    # 提取器产量随进度线性衰减：初期 S、末期 L = decay·S，平均 = q_now × (1 + 修正)
    #   S = 2·q_now·(1+修正)/(1+decay)
    # 工厂按固定速率消耗，所以占用在中途达到最高点，结束时反而回落。
    def rate_at(e, t: float) -> float:
        """第 t 秒时该提取器的产出速率（个/秒）。"""
        d_e = float(e.get("duration") or 0)
        c_e = float(e.get("cycle_time") or 0) or 1.0
        if d_e <= 0:
            return 0.0
        cycles = d_e / c_e
        avg = (float(e.get("est_total") or 0) / cycles) if cycles > 0 else 0.0
        if avg <= 0:
            return 0.0
        start = 2.0 * avg / (1.0 + decay)
        end_yield = decay * start
        prog = min(1.0, max(0.0, t / d_e))
        return (start + (end_yield - start) * prog) / c_e

    consume_p0 = 3000.0 * len(basic)              # 每 30 分钟消耗的 P0
    consume_p1 = (80.0 / 2.0) * len(advanced)     # 高级工厂 1 小时 80 P1，每 30 分钟 40

    # 各层产物分别落在哪个仓储：P0 跟采集器走，P1/P2 跟工厂走
    bin_ids = {b.get("pin_id") for b in bins}
    biggest = max(bins, key=lambda b: float(b.get("capacity_m3") or 0)).get("pin_id") if bins else None

    def _pick(cands, fallback):
        for c in cands:
            if c in bin_ids:
                return c
        return fallback

    p0_bin = _pick([e.get("out_pin") for e in extractors], biggest)
    p1_bin = _pick([f.get("out_pin") for f in basic], p0_bin or biggest)
    p2_bin = _pick([f.get("out_pin") for f in advanced], p1_bin or biggest)

    # 每个仓储里"不属于 P0/P1/P2 的那部分体积"，模拟时保持不变
    other: dict = {}
    cur = {"P0": 0.0, "P1": 0.0, "P2": 0.0}
    for b in bins:
        o = float(b.get("used_m3") or 0)
        for it in b.get("items") or []:
            t = it.get("tier")
            if t in cur:
                amt = float(it.get("amount") or 0)
                cur[t] += amt
                o -= amt * TIER_VOL[t]
        other[b.get("pin_id")] = max(0.0, o)

    def bin_vol(pin_id, q0, q1, q2):
        v = other.get(pin_id, 0.0)
        if pin_id == p0_bin:
            v += q0 * P0_VOL
        if pin_id == p1_bin:
            v += q1 * P1_VOL
        if pin_id == p2_bin:
            v += q2 * P2_VOL
        return v

    p0, p1_, p2_ = cur["P0"], cur["P1"], cur["P2"]
    peak_by_bin = {b.get("pin_id"): float(b.get("used_m3") or 0) for b in bins}
    peak_vol, peak_hour = used, 0.0
    n_blocks = max(1, int(duration / BASIC_CYCLE))
    for k in range(n_blocks + 1):
        for pid in peak_by_bin:
            v = bin_vol(pid, p0, p1_, p2_)
            if v > peak_by_bin[pid]:
                peak_by_bin[pid] = v
        top = max(bin_vol(pid, p0, p1_, p2_) for pid in peak_by_bin) if peak_by_bin else used
        if top > peak_vol:
            peak_vol, peak_hour = top, k * BASIC_CYCLE / 3600.0
        if k == n_blocks:
            break
        t = k * BASIC_CYCLE
        produced = sum(rate_at(e, t) * BASIC_CYCLE for e in extractors)
        use0 = min(p0 + produced, consume_p0)
        p0 += produced - use0
        made1 = use0 / P0_PER_P1
        use1 = min(p1_ + made1, consume_p1) if consume_p1 > 0 else 0.0
        p1_ += made1 - use1
        p2_ += use1 / P1_PER_P2

    target = p1_bin or p0_bin or biggest

    per_bin = [
        _bin_entry(
            b,
            bin_vol(b.get("pin_id"), p0, p1_, p2_),
            peak_by_bin.get(b.get("pin_id"), float(b.get("used_m3") or 0)),
            b.get("pin_id") == target,
        )
        for b in bins
    ]
    end_fill = _max_fill(per_bin, "end_fill")
    peak_fill = _max_fill(per_bin, "peak_fill")

    return {
        "kind": kind,
        "duration": duration,
        "p0_total": p0_total,
        "p0_by_product": p0_by_product,
        "p1": p1,
        "p2": p2,
        "output": output,
        "output_tier": tier,
        "output_id": out_id,
        "output_name": out_name,
        "accumulate_m3": accumulate,
        "p0_left": p0_left,
        "storages": per_bin,
        "used_m3": used,
        "capacity_m3": cap,
        "end_fill": end_fill,
        "peak_fill": peak_fill,
        "peak_used_m3": peak_vol,
        "peak_hour": peak_hour,
        "overflow": bool((peak_fill if peak_fill is not None else end_fill) is not None
                         and (peak_fill if peak_fill is not None else end_fill) > 0.95),
    }


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

def build_overview(esi, characters: list[dict], now: float | None = None, correction: float = 0.0, decay: float = 0.4) -> dict:
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
        colonies.append(_build_colony(esi, ref, detail, name_map, planet_meta, system_meta, now, correction))

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

    apply_correction(esi, colonies, summary, prices, correction, decay)


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


def apply_correction(esi, colonies, summary, prices, correction: float, decay: float = 0.4) -> None:
    """按修正系数重算预估产量、收益和汇总。

    纯计算：只用已经拿到的殖民地和价格，不访问网络。
    改设置里的修正值时直接对内存里的总览重跑这个函数即可。
    """
    factor = 1.0 + correction
    for c in colonies:
        tier_estimate: dict[str, float] = {}
        for e in c.get("extractors") or []:
            e["est_total"] = float(e.get("est_total_raw") or 0.0) * factor
            e["est_remaining"] = float(e.get("est_remaining_raw") or 0.0) * factor
            e["est_done"] = e["est_total"] * float(e.get("progress") or 0.0)
            t = e.get("tier")
            if t and t != "?":
                tier_estimate[t] = tier_estimate.get(t, 0.0) + e["est_total"]
        c["tier_estimate"] = tier_estimate
        c["forecast"] = _forecast(
            esi,
            c.get("extractors") or [],
            c.get("factories") or [],
            c.get("storages") or [],
            decay,
        )

    fc_p1 = fc_p2 = fc_p3 = fc_p4 = fc_isk = fc_net = 0.0
    for c in colonies:
        f = c.get("forecast")
        if not f:
            continue
        out_price = (prices.get(int(f["output_id"])) or {}).get("buy") if f.get("output_id") else None
        if f.get("pure"):
            # 纯合成：收益 = 产物价值 − 原料价值（一次把库存原料加工完）
            in_value = 0.0
            ok = True
            direct = f.get("direct") or {}
            consumed = f.get("consumed") or {}
            for it in f.get("inputs") or []:
                ip = (prices.get(int(it["type_id"])) or {}).get("buy") if it.get("type_id") else None
                if ip is None:
                    ok = False
                    break
                # 只算真正被消耗掉的那部分原料
                t = it.get("tier")
                tot = float(direct.get(t) or 0)
                ratio = (float(consumed.get(t, 0)) / tot) if tot > 0 else 0.0
                in_value += float(it["amount"]) * ratio * ip
            f["net_isk"] = (f["output"] * out_price - in_value) if (out_price and ok) else None
            f["isk_day"] = None
        else:
            days = (f["duration"] or 0) / 86400.0
            f["isk_day"] = (f["output"] / days * out_price) if (out_price and days > 0) else None
        tier = f.get("output_tier")
        if tier == "P1":
            fc_p1 += f["output"]
        elif tier == "P2":
            fc_p2 += f["output"]
        elif tier == "P3":
            fc_p3 += f["output"]
        elif tier == "P4":
            fc_p4 += f["output"]
        if f.get("isk_day"):
            fc_isk += f["isk_day"]
        if f.get("net_isk"):
            fc_net += f["net_isk"]
    summary["forecast_p1"] = fc_p1
    summary["forecast_p2"] = fc_p2
    summary["forecast_p3"] = fc_p3
    summary["forecast_p4"] = fc_p4
    summary["forecast_isk_day"] = fc_isk
    summary["forecast_net_isk"] = fc_net

def _build_colony(esi, ref, detail, name_map, planet_meta, system_meta, now, correction=0.0) -> dict:
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
    # 工厂原料：从入向路由推断，用来判断配方需要哪几层、各几种
    factory_inputs: dict[int, set] = {}
    factory_dest: dict[int, int] = {}
    for route in routes:
        src = route.get("source_pin_id")
        dst = route.get("destination_pin_id")
        ct = route.get("content_type_id")
        if src and ct:
            factory_output.setdefault(src, ct)
            if dst:
                factory_dest.setdefault(src, dst)
        if dst and ct:
            factory_inputs.setdefault(dst, set()).add(ct)

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
            # 原始外推（未修正）。修正系数由 apply_correction 在展示层叠加，
            # 这样改修正值不用重新拉 ESI。
            est_total_raw = qty * cycles_total
            est_remaining_raw = qty * cycles_left
            est_total = est_total_raw
            est_remaining = est_remaining_raw
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
                "est_total_raw": est_total_raw,
                "out_pin": factory_dest.get(pin.get("pin_id")),
                "in_count": len(factory_inputs.get(pin.get("pin_id")) or ()),
                "est_remaining_raw": est_remaining_raw,
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
                "out_pin": factory_dest.get(pin.get("pin_id")),
                "in_count": len(factory_inputs.get(pin.get("pin_id")) or ()),
                "in_tiers": sorted(
                    {t for t in (_tier_of(esi, x) for x in factory_inputs.get(pin.get("pin_id")) or ())
                     if t in TIER_ORDER}
                ),
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
        "forecast": _forecast(esi, extractors, factories, storages),
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
