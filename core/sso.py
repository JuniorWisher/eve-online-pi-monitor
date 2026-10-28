"""EVE SSO (OAuth 2.0) 登录流程。

流程：
1. 浏览器打开 authorize 页面（官方域名，用户在此输入账号密码 + 两步验证）
2. 授权后回调 /callback?code=...&state=...
3. 用 code + client_id/secret 换 access_token(JWT) + refresh_token
4. refresh_token 是单次有效的（每次刷新都会轮换），必须每次都写回数据库

程序全程不接触用户密码。
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from urllib.parse import urlencode

import requests

from . import config as cfgmod
from . import store

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

# 权限说明：
#   esi-planets.manage_planets.v1    读殖民地、引脚、路由、发射台库存
#   esi-assets.read_assets.v1        读角色资产（海关/建筑里的 PI 物资只能从这里拿）
#   esi-universe.read_structures.v1  解析建筑名，用来区分玩家建筑和海关
SCOPES = [
    "esi-planets.manage_planets.v1",
    "esi-assets.read_assets.v1",
    "esi-universe.read_structures.v1",
]


class SSOError(Exception):
    pass


class AuthError(SSOError):
    """refresh token 失效，需要用户重新登录。"""


def authorize_url(cfg: dict, state: str) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


def _basic_auth(cfg: dict) -> str:
    raw = f"{cfg['client_id']}:{cfg['client_secret']}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _token_request(cfg: dict, payload: dict) -> dict:
    headers = {
        "Authorization": _basic_auth(cfg),
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "login.eveonline.com",
        "User-Agent": cfgmod.user_agent(cfg),
    }
    try:
        r = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=25)
    except requests.RequestException as exc:
        raise SSOError(f"无法连接 EVE 登录服务器：{exc}") from exc

    if r.status_code != 200:
        detail = ""
        is_json = False
        try:
            body = r.json()
            is_json = True
            detail = body.get("error_description") or body.get("error") or ""
        except Exception:
            text = re.sub(r"<[^>]+>", " ", r.text or "")
            detail = " ".join(text.split())[:160]

        if r.status_code in (400, 401) and "invalid_grant" in (detail or ""):
            raise AuthError(f"授权已失效，请重新登录（{detail}）")
        if r.status_code == 401 and not is_json:
            raise SSOError("Client ID 或 Secret Key 不正确（EVE 返回 401），请到「设置」页核对后重试")
        if r.status_code == 400 and "invalid_client" in (detail or ""):
            raise SSOError("Client ID 或 Secret Key 不正确（invalid_client），请到「设置」页核对")
        raise SSOError(f"换取 token 失败（HTTP {r.status_code}）：{detail}")

    data = r.json()
    if "access_token" not in data:
        raise SSOError("token 响应格式异常")
    return data


def exchange_code(cfg: dict, code: str) -> dict:
    return _token_request(cfg, {"grant_type": "authorization_code", "code": code})


def refresh(cfg: dict, refresh_token: str) -> dict:
    return _token_request(cfg, {"grant_type": "refresh_token", "refresh_token": refresh_token})


def decode_jwt(token: str) -> dict:
    """本地解析 access_token（JWT），不做签名校验——token 直接来自 CCP 的 HTTPS 响应。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception as exc:
        raise SSOError(f"无法解析 access_token：{exc}") from exc


def character_from_token(token: str) -> dict:
    claims = decode_jwt(token)
    sub = claims.get("sub", "")
    if not sub.startswith("CHARACTER:EVE:"):
        raise SSOError("token 不是角色授权 token")
    return {
        "character_id": int(sub.split(":")[-1]),
        "name": claims.get("name", ""),
        "owner_hash": claims.get("owner", ""),
        "scopes": " ".join(claims.get("scp", []) or []),
        "exp": claims.get("exp", 0),
    }


class TokenManager:
    """按角色维护 access token，过期前自动用 refresh token 续期。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._lock = threading.RLock()
        self._cache: dict[int, tuple[str, float]] = {}

    def invalidate(self, character_id: int) -> None:
        with self._lock:
            self._cache.pop(character_id, None)

    def get_access(self, character_id: int, force: bool = False) -> str:
        with self._lock:
            token, exp = self._cache.get(character_id, ("", 0.0))
            if token and not force and exp - time.time() > 90:
                return token

            row = store.get_character(character_id)
            if not row or not row.get("refresh_token"):
                raise AuthError("该角色没有可用的授权，请重新登录")

            try:
                data = refresh(self.cfg, row["refresh_token"])
            except AuthError:
                raise
            except SSOError as exc:
                raise AuthError(str(exc)) from exc

            access = data["access_token"]
            expires_in = float(data.get("expires_in") or 1199)
            new_refresh = data.get("refresh_token")
            if new_refresh and new_refresh != row["refresh_token"]:
                store.update_refresh_token(character_id, new_refresh)

            self._cache[character_id] = (access, time.time() + expires_in)
            return access
