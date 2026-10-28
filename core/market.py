"""Jita 市场价：走 Fuzzwork 聚合接口（一次请求拿多个 type_id，免费无 key）。

为什么不用 ESI：ESI 的 /markets/prices/ 返回的是 CCP 调整价（工业计算用），
和实际成交价差好几倍；要拿真实挂单价得把整个星域的订单翻几百页。
"""

from __future__ import annotations

import json
import time

import requests

from . import i18n, store

FUZZWORK = "https://market.fuzzwork.co.uk/aggregates/"
JITA_STATION = 60003760  # Jita IV - Moon 4 - Caldari Navy Assembly Plant（吉他 4-4）
CACHE_KEY = "fuzzwork:jita:pi"
TTL = 900  # 15 分钟
TIMEOUT = 20
UA = {"User-Agent": "PlanetaryIndustryMonitor/1.0"}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# 始终整批抓全部 PI 物资：缓存只有一份，按子集抓会让后面的调用拿到不完整数据
ALL_TYPES = sorted(i18n.CN_BY_ID)


def fetch_prices(type_ids=None, force: bool = False) -> dict[int, dict]:
    """返回 {type_id: {buy, sell, buy_volume, sell_volume}}，失败时返回空字典。

    buy 是**最高买单**（立刻卖能成交的价），sell 是最低卖单。
    """
    want = {int(t) for t in type_ids if t} if type_ids else set(ALL_TYPES)
    if not want:
        return {}

    if not force:
        cached = store.get_cache(CACHE_KEY)
        if cached and cached["expires_at"] > time.time():
            try:
                raw = json.loads(cached["body"])
            except Exception:
                raw = None
            if raw is not None:
                return {int(k): v for k, v in raw.items() if int(k) in want}

    params = {"station": JITA_STATION, "types": ",".join(str(i) for i in ALL_TYPES)}
    try:
        resp = requests.get(FUZZWORK, params=params, headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:  # noqa: BLE001 - 拿不到价就当没有，不影响主流程
        return {}

    out: dict[int, dict] = {}
    for tid, row in data.items():
        buy = row.get("buy") or {}
        sell = row.get("sell") or {}
        out[int(tid)] = {
            "buy": _num(buy.get("max")),
            "sell": _num(sell.get("min")),
            "buy_volume": int(_num(buy.get("volume"))),
            "sell_volume": int(_num(sell.get("volume"))),
        }

    if out:
        store.put_cache(CACHE_KEY, None, time.time() + TTL, json.dumps(out))
    return {tid: v for tid, v in out.items() if tid in want}


def last_fetched() -> float | None:
    """上次成功抓价的时间戳（秒）。"""
    row = store.get_cache(CACHE_KEY)
    return row["fetched_at"] if row else None


def clear_cache() -> None:
    store.delete_cache(CACHE_KEY)
