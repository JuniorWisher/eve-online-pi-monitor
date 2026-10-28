"""配置读写。

配置优先级：环境变量 > config.json > 内置默认值。
程序首次运行时若没有 config.json，会自动从 config.example.json 复制一份。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

from . import paths

BASE_DIR = paths.APP_DIR
CONFIG_PATH = paths.data_path("config.json")
# 模板示例文件在打包后位于临时解包目录
EXAMPLE_PATH = paths.resource("config.example.json")

DEFAULTS = {
    "client_id": "",
    "client_secret": "",
    "redirect_uri": "http://localhost:5000/callback",
    "host": "127.0.0.1",
    "port": 5000,
    "contact_email": "",
    "cache_ttl": 300,
    # 总览缓存多久后自动过期（秒）；0 = 只在点「刷新」时更新
    "overview_ttl": 0,
}


def ensure_file() -> None:
    """确保 config.json 存在。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return
    if EXAMPLE_PATH.exists():
        shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False), "utf-8")


def load() -> dict:
    ensure_file()
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
    except Exception:
        pass

    if os.environ.get("EVE_CLIENT_ID"):
        cfg["client_id"] = os.environ["EVE_CLIENT_ID"]
    if os.environ.get("EVE_CLIENT_SECRET"):
        cfg["client_secret"] = os.environ["EVE_CLIENT_SECRET"]
    if os.environ.get("EVE_REDIRECT_URI"):
        cfg["redirect_uri"] = os.environ["EVE_REDIRECT_URI"]
    return cfg


def save(cfg: dict) -> None:
    """只把可持久化的字段写回磁盘，环境变量不落盘。"""
    data = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in cfg:
            data[k] = cfg[k]
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("client_id") and cfg.get("client_secret"))


def user_agent(cfg: dict) -> str:
    contact = (cfg.get("contact_email") or "").strip() or "no-contact@example.com"
    return f"PlanetaryIndustryMonitor/1.0 ({contact})"


def _mask(value: str) -> str:
    """只留最后 4 位，其余打星号。"""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * min(len(value) - 4, 12) + value[-4:]


def masked(cfg: dict) -> dict:
    """给前端看的配置摘要，client_id / secret 都不返回明文。"""
    cid = cfg.get("client_id") or ""
    secret = cfg.get("client_secret") or ""
    return {
        "client_id_set": bool(cid),
        "client_id_hint": _mask(cid),
        "client_secret_set": bool(secret),
        "client_secret_hint": _mask(secret),
        "redirect_uri": cfg.get("redirect_uri") or "",
        "contact_email": cfg.get("contact_email") or "",
        "overview_ttl": int(cfg.get("overview_ttl") or 0),
        "configured": is_configured(cfg),
    }
