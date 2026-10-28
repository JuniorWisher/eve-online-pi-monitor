"""ESI 客户端：ETag 条件请求 + 本地缓存 + 错误限流保护。

CCP 的规则：
- 必须带可识别的 User-Agent
- 返回头 X-ESI-Error-Limit-Remain / X-ESI-Error-Limit-Reset 用于限流，耗尽会被 420 封禁
- 建议用 If-None-Match 复用缓存，304 不消耗缓存额度
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import requests

from . import config as cfgmod
from . import store
from .sso import AuthError, TokenManager

BASE_URL = "https://esi.evetech.net/latest"


class ESIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ESI:
    def __init__(self, cfg: dict, tokens: TokenManager):
        self.cfg = cfg
        self.tokens = tokens
        self._local = threading.local()
        self._limit_lock = threading.Lock()
        self._err_remain = 100
        self._err_reset = 60
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}
        self._type_memo: dict[int, dict] = {}
        self._group_memo: dict[int, str] = {}
        self._struct_memo: dict[tuple, dict | None] = {}
        self._memo_lock = threading.Lock()

    # -- 基础设施 ---------------------------------------------------------

    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update(
                {
                    "User-Agent": cfgmod.user_agent(self.cfg),
                    "Accept": "application/json",
                    "Accept-Language": "en",
                }
            )
            self._local.session = s
        return s

    def _read_limit(self, resp: requests.Response) -> None:
        try:
            remain = int(resp.headers.get("X-ESI-Error-Limit-Remain", ""))
            reset = int(resp.headers.get("X-ESI-Error-Limit-Reset", ""))
        except (TypeError, ValueError):
            return
        with self._limit_lock:
            self._err_remain = remain
            self._err_reset = reset

    def _wait_for_limit(self) -> None:
        with self._limit_lock:
            remain, reset = self._err_remain, self._err_reset
        if remain < 15:
            time.sleep(min(max(reset, 1), 60))

    def _raw(self, method: str, path: str, *, params=None, headers=None, json_body=None) -> requests.Response:
        url = BASE_URL + path
        last_exc: Exception | None = None
        for attempt in range(4):
            self._wait_for_limit()
            try:
                self.stats["requests"] += 1
                resp = self._session().request(
                    method, url, params=params, headers=headers, json=json_body, timeout=30
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
                continue

            self._read_limit(resp)
            if resp.status_code in (420, 429):
                time.sleep(min(max(self._err_reset, 5), 65))
                continue
            if resp.status_code in (500, 502, 503, 504):
                last_exc = ESIError(resp.status_code, f"ESI 服务端错误 {resp.status_code}")
                time.sleep(0.8 * (attempt + 1))
                continue
            return resp

        raise ESIError(0, f"ESI 请求失败：{last_exc}")

    @staticmethod
    def _expires_at(resp: requests.Response, default_ttl: float) -> float:
        raw = resp.headers.get("Expires")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                ts = dt.timestamp()
                if ts > time.time() - 5:
                    return ts
            except Exception:
                pass
        return time.time() + default_ttl

    # -- 主要接口 ---------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict | None = None,
        *,
        auth: bool = False,
        cid: int | None = None,
        force: bool = False,
        ttl: float | None = None,
        with_headers: bool = False,
        cache_key: str | None = None,
    ):
        """with_headers=True 时返回 (body, headers)，用于读取 X-Pages 等分页信息。

        cache_key 用于覆盖默认缓存键——建筑信息是按角色权限返回的，必须带上角色 id。
        """
        ttl = ttl if ttl is not None else float(self.cfg.get("cache_ttl") or 300)
        key = cache_key or (path + ("?" + urlencode(sorted(params.items())) if params else ""))

        def _ret(body, hdrs):
            if with_headers:
                return body, hdrs
            return body

        cached = store.get_cache(key)
        if cached and not force and cached["expires_at"] > time.time():
            self.stats["cache_hits"] += 1
            try:
                return _ret(
                    json.loads(cached["body"]),
                    {"X-Pages": store.get_meta("pages:" + key) or "1"},
                )
            except Exception:
                pass

        headers: dict[str, str] = {}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]

        token = None
        if auth:
            if cid is None:
                raise ValueError("auth 请求必须提供 cid")
            token = self.tokens.get_access(cid)
            headers["Authorization"] = "Bearer " + token

        resp = self._raw("GET", path, params=params, headers=headers)

        if resp.status_code == 401 and auth:
            # token 可能被提前吊销，强制刷新后重试一次
            token = self.tokens.get_access(cid, force=True)
            headers["Authorization"] = "Bearer " + token
            resp = self._raw("GET", path, params=params, headers=headers)

        if resp.status_code == 304 and cached:
            store.touch_cache(key, self._expires_at(resp, ttl))
            self.stats["cache_hits"] += 1
            return _ret(
                json.loads(cached["body"]),
                {"X-Pages": store.get_meta("pages:" + key) or "1"},
            )

        if resp.status_code == 404:
            return _ret(None, {})

        if resp.status_code >= 400:
            self.stats["errors"] += 1
            try:
                msg = resp.json().get("error", resp.text[:200])
            except Exception:
                msg = resp.text[:200]
            if resp.status_code == 403:
                msg = f"权限不足（缺少对应 scope）：{msg}"
            raise ESIError(resp.status_code, msg)

        try:
            body = resp.json()
        except ValueError:
            raise ESIError(resp.status_code, "ESI 返回了非 JSON 内容")

        store.put_cache(key, resp.headers.get("ETag"), self._expires_at(resp, ttl), json.dumps(body))
        pages = resp.headers.get("X-Pages")
        if pages:
            store.set_meta("pages:" + key, str(pages))
        return _ret(body, dict(resp.headers))

    def post(self, path: str, body):
        resp = self._raw("POST", path, json_body=body)
        if resp.status_code >= 400:
            self.stats["errors"] += 1
            raise ESIError(resp.status_code, resp.text[:200])
        return resp.json()

    # -- 元数据（永久缓存） -----------------------------------------------

    def names(self, ids) -> dict[int, str]:
        """批量把 id 解析成名字（类型、星系、空间站等）。

        注意：/universe/names/ 不支持星球 id，只要混进一个就会整批 404，
        所以调用方不要把 planet_id 传进来；星球名请用 /universe/planets/。
        """
        ids = [int(i) for i in dict.fromkeys(ids) if i]
        if not ids:
            return {}
        out: dict[int, str] = {}
        for i in range(0, len(ids), 900):
            for item in self._names_batch(ids[i : i + 900]):
                if item.get("id") is not None and item.get("name"):
                    out[int(item["id"])] = item["name"]
        return out

    def _names_batch(self, chunk: list[int]) -> list[dict]:
        """整批失败时二分拆包，把非法 id 隔离出来，而不是退化成逐条请求。"""
        if not chunk:
            return []
        try:
            return self.post("/universe/names/", chunk) or []
        except ESIError:
            if len(chunk) <= 1:
                return []
            mid = len(chunk) // 2
            return self._names_batch(chunk[:mid]) + self._names_batch(chunk[mid:])

    def type_info(self, type_id: int) -> dict:
        """返回 {type_id, name, group_id, volume}，带内存 + SQLite 永久缓存。"""
        type_id = int(type_id)
        with self._memo_lock:
            hit = self._type_memo.get(type_id)
        if hit:
            return hit
        cached = store.get_type(type_id)
        if cached and cached.get("name"):
            info = dict(cached)
            with self._memo_lock:
                self._type_memo[type_id] = info
            return info
        try:
            data = self.get(f"/universe/types/{type_id}/", ttl=86400)
        except ESIError:
            data = None
        if not data:
            info = {"type_id": type_id, "name": f"Type #{type_id}", "group_id": None, "volume": 0.01}
            store.put_type(type_id, info["name"], None, info["volume"])
            with self._memo_lock:
                self._type_memo[type_id] = info
            return info
        info = {
            "type_id": type_id,
            "name": data.get("name") or f"Type #{type_id}",
            "group_id": data.get("group_id"),
            "volume": float(data.get("volume") or 0.01),
        }
        store.put_type(type_id, info["name"], info["group_id"], info["volume"])
        with self._memo_lock:
            self._type_memo[type_id] = info
        return info

    def prefetch_types(self, type_ids, workers: int = 6) -> None:
        todo = [int(t) for t in dict.fromkeys(type_ids) if t]
        todo = [t for t in todo if not (store.get_type(t) or {}).get("name")]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self.type_info, todo))

    def group_name(self, group_id: int) -> str:
        if not group_id:
            return ""
        with self._memo_lock:
            hit = self._group_memo.get(group_id)
        if hit is not None:
            return hit
        cached = store.get_group(group_id)
        if cached:
            with self._memo_lock:
                self._group_memo[group_id] = cached
            return cached
        try:
            data = self.get(f"/universe/groups/{group_id}/", ttl=86400)
        except ESIError:
            data = None
        name = (data or {}).get("name") or ""
        if name:
            store.put_group(group_id, name)
        with self._memo_lock:
            self._group_memo[group_id] = name
        return name


    # -- 建筑解析 ---------------------------------------------------------

    def structure_info(self, cid: int, structure_id: int) -> dict | None:
        """解析玩家建筑（星城/铁壁等）。

        只有在该建筑 ACL 里才返回数据；海关、货柜等非建筑一律返回 None。
        结果按 (角色, 建筑) 缓存——权限是按角色区分的，不能共用缓存。
        """
        key = (int(cid), int(structure_id))
        with self._memo_lock:
            if key in self._struct_memo:
                return self._struct_memo[key]

        data = None
        try:
            data = self.get(
                f"/universe/structures/{structure_id}/",
                auth=True,
                cid=cid,
                ttl=3600,
                cache_key=f"structure:{cid}:{structure_id}",
            )
        except ESIError:
            data = None  # 403（无权限或根本不是建筑）当作解析失败

        with self._memo_lock:
            self._struct_memo[key] = data
        return data
