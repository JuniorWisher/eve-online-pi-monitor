"""本地 SQLite 存储：角色与 refresh token、ESI 响应缓存、类型元数据、库存快照。

设计要点：
- 每个操作单独取连接，配合 WAL，多线程安全且无长事务。
- refresh token 默认用 Fernet 加密后落盘；cryptography 不可用时降级为明文并打标记。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import paths

DATA_DIR = paths.data_path("data")
DB_PATH = DATA_DIR / "pi.db"
KEY_PATH = DATA_DIR / "key.bin"

_init_lock = threading.Lock()
_initialized = False
_fernet_cache = {"obj": None, "tried": False}

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    character_id  INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    owner_hash    TEXT,
    scopes        TEXT,
    refresh_token TEXT,
    added_at      REAL,
    last_login    REAL
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    etag       TEXT,
    expires_at REAL,
    body       TEXT,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS type_info (
    type_id    INTEGER PRIMARY KEY,
    name       TEXT,
    group_id   INTEGER,
    volume     REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS group_info (
    group_id   INTEGER PRIMARY KEY,
    name       TEXT,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL,
    character_id INTEGER,
    planet_id    INTEGER,
    data         TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_lookup ON snapshots (character_id, planet_id, ts);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- 最近一次构建好的总览，F5 刷新直接读它，不重新请求 ESI
CREATE TABLE IF NOT EXISTS overview_cache (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    signature TEXT,
    ts        REAL,
    data      TEXT
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _conn() as c:
            c.executescript(SCHEMA)
        _initialized = True


# --------------------------------------------------------------------------
# 加密
# --------------------------------------------------------------------------

def _fernet():
    if _fernet_cache["tried"]:
        return _fernet_cache["obj"]
    _fernet_cache["tried"] = True
    try:
        from cryptography.fernet import Fernet
    except Exception:
        _fernet_cache["obj"] = None
        return None
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        try:
            KEY_PATH.chmod(0o600)
        except Exception:
            pass
    try:
        _fernet_cache["obj"] = Fernet(KEY_PATH.read_bytes())
    except Exception:
        _fernet_cache["obj"] = None
    return _fernet_cache["obj"]


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    f = _fernet()
    if f is None:
        return "plain:" + plain
    return "enc:" + f.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(blob: str) -> str:
    if not blob:
        return ""
    if blob.startswith("plain:"):
        return blob[6:]
    if blob.startswith("enc:"):
        f = _fernet()
        if f is None:
            return ""
        try:
            return f.decrypt(blob[4:].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    return blob


def encryption_enabled() -> bool:
    return _fernet() is not None


# --------------------------------------------------------------------------
# 角色
# --------------------------------------------------------------------------

def list_characters() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT character_id, name, owner_hash, scopes, added_at, last_login "
            "FROM characters ORDER BY added_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_character(character_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM characters WHERE character_id = ?", (character_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["refresh_token"] = decrypt(data.get("refresh_token") or "")
    return data


def upsert_character(character_id: int, name: str, owner_hash: str, scopes: str, refresh_token: str) -> None:
    now = time.time()
    with _conn() as c:
        exists = c.execute("SELECT 1 FROM characters WHERE character_id = ?", (character_id,)).fetchone()
        if exists:
            c.execute(
                "UPDATE characters SET name=?, owner_hash=?, scopes=?, refresh_token=?, last_login=? "
                "WHERE character_id=?",
                (name, owner_hash, scopes, encrypt(refresh_token), now, character_id),
            )
        else:
            c.execute(
                "INSERT INTO characters (character_id, name, owner_hash, scopes, refresh_token, added_at, last_login) "
                "VALUES (?,?,?,?,?,?,?)",
                (character_id, name, owner_hash, scopes, encrypt(refresh_token), now, now),
            )


def update_refresh_token(character_id: int, refresh_token: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE characters SET refresh_token=?, last_login=? WHERE character_id=?",
            (encrypt(refresh_token), time.time(), character_id),
        )


def delete_character(character_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM characters WHERE character_id = ?", (character_id,))
        c.execute("DELETE FROM snapshots WHERE character_id = ?", (character_id,))


# --------------------------------------------------------------------------
# ESI 响应缓存
# --------------------------------------------------------------------------

def get_cache(key: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM cache WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def put_cache(key: str, etag: str | None, expires_at: float, body: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO cache (key, etag, expires_at, body, fetched_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET etag=excluded.etag, expires_at=excluded.expires_at, "
            "body=excluded.body, fetched_at=excluded.fetched_at",
            (key, etag, expires_at, body, time.time()),
        )


def delete_cache(key: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM cache WHERE key = ?", (key,))


def touch_cache(key: str, expires_at: float) -> None:
    with _conn() as c:
        c.execute("UPDATE cache SET expires_at=?, fetched_at=? WHERE key=?", (expires_at, time.time(), key))


def clear_cache() -> int:
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        c.execute("DELETE FROM cache")
    return n


def cache_stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        fresh = c.execute("SELECT COUNT(*) FROM cache WHERE expires_at > ?", (time.time(),)).fetchone()[0]
    return {"entries": total, "fresh": fresh}


# --------------------------------------------------------------------------
# 类型 / 分组元数据（永久缓存）
# --------------------------------------------------------------------------

def get_type(type_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM type_info WHERE type_id = ?", (type_id,)).fetchone()
    return dict(row) if row else None


def put_type(type_id: int, name: str, group_id: int | None, volume: float | None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO type_info (type_id, name, group_id, volume, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET name=excluded.name, group_id=excluded.group_id, "
            "volume=excluded.volume, updated_at=excluded.updated_at",
            (type_id, name, group_id, volume, time.time()),
        )


def get_group(group_id: int) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT name FROM group_info WHERE group_id = ?", (group_id,)).fetchone()
    return row["name"] if row else None


def put_group(group_id: int, name: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO group_info (group_id, name, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(group_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            (group_id, name, time.time()),
        )


# --------------------------------------------------------------------------
# 库存快照
# --------------------------------------------------------------------------

def add_snapshot(character_id: int, planet_id: int, contents: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO snapshots (ts, character_id, planet_id, data) VALUES (?,?,?,?)",
            (time.time(), character_id, planet_id, json.dumps(contents)),
        )


def last_snapshot(character_id: int, planet_id: int, before: float | None = None) -> dict | None:
    """取最近一次快照（可限定早于某个时间点）。"""
    q = "SELECT ts, data FROM snapshots WHERE character_id=? AND planet_id=?"
    args: list = [character_id, planet_id]
    if before is not None:
        q += " AND ts < ?"
        args.append(before)
    q += " ORDER BY ts DESC LIMIT 1"
    with _conn() as c:
        row = c.execute(q, args).fetchone()
    if not row:
        return None
    return {"ts": row["ts"], "data": json.loads(row["data"])}


def snapshot_count(character_id: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM snapshots WHERE character_id=?", (character_id,)).fetchone()[0]


# --------------------------------------------------------------------------
# 杂项
# --------------------------------------------------------------------------

def get_meta(k: str, default: str | None = None) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return row["v"] if row else default


def set_meta(k: str, v: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO meta (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )


# --------------------------------------------------------------------------
# 总览缓存（避免每次刷新网页都打 ESI）
# --------------------------------------------------------------------------

def save_overview(signature: str, data: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO overview_cache (id, signature, ts, data) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET signature=excluded.signature, ts=excluded.ts, data=excluded.data",
            (signature, time.time(), json.dumps(data, ensure_ascii=False)),
        )


def load_overview() -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT signature, ts, data FROM overview_cache WHERE id = 1").fetchone()
    if not row:
        return None
    try:
        return {"signature": row["signature"], "ts": row["ts"], "data": json.loads(row["data"])}
    except Exception:
        return None


def clear_overview() -> None:
    with _conn() as c:
        c.execute("DELETE FROM overview_cache WHERE id = 1")
