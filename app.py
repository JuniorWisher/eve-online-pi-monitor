"""行星工业监视器 —— 本地网页应用入口。

用法：
    python app.py            # 正式模式（需要先在「设置」里填 client_id/secret）
    python app.py --demo     # 演示模式，用假数据看界面
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import io
import secrets
import threading
import time
import webbrowser
from urllib.parse import quote

import requests

from flask import Flask, Response, jsonify, redirect, render_template, request, session

from core import config as cfgmod
from core import about, demo, i18n, market, paths, pi, sso, store, tray
from core.esi import ESI, ESIError

# 解析逻辑版本：改了 pi.py 的取数/分类规则时手动 +1，让总览缓存失效
LOGIC_VERSION = "4"

SNAPSHOT_MIN_GAP = 600  # 两次快照最小间隔（秒），避免刷一次存一次
OVERVIEW_MEMO_TTL = 30  # 同一份结果的内存复用时间（秒）


def create_app(cfg: dict, demo_mode: bool = False) -> Flask:
    store.init()

    # 后台每小时刷一次 Jita 报价（页面没人看也保持数据不过期）
    def _price_loop():
        while True:
            time.sleep(3600)
            try:
                market.fetch_prices(force=True)
            except Exception:  # noqa: BLE001 - 后台任务失败不能影响主服务
                pass

    threading.Thread(target=_price_loop, daemon=True, name="price-refresh").start()

    app = Flask(
        __name__,
        static_folder=str(paths.resource("static")),
        template_folder=str(paths.resource("templates")),
    )

    @app.context_processor
    def _inject_about():
        # 静态资源带上版本号，升级后浏览器不会继续用旧缓存
        return {"asset_version": about.VERSION}
    app.secret_key = store.get_meta("flask_secret") or secrets.token_hex(32)
    store.set_meta("flask_secret", app.secret_key)
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    state = {
        "tokens": sso.TokenManager(cfg),
        "esi": None,
        "memo": {"ts": 0.0, "key": None, "overview": None, "prev": None},
        "lock": threading.Lock(),
    }

    def get_esi():
        if demo_mode:
            if state["esi"] is None:
                state["esi"] = demo.make_esi()
            return state["esi"]
        if state["esi"] is None:
            state["esi"] = ESI(cfg, state["tokens"])
        return state["esi"]

    def characters():
        if demo_mode:
            return demo.characters()
        return [
            {
                "id": c["character_id"],
                "name": c["name"],
                "scopes": c.get("scopes") or "",
                "owner": c.get("owner_hash") or "",
            }
            for c in store.list_characters()
        ]

    @app.before_request
    def _normalize_host():
        """回调地址用的是 localhost，而用户可能手动输 127.0.0.1。

        两者的 cookie 不互通，会让 SSO 的 state 校验失败，这里统一跳到回调地址所在的主机。
        """
        from urllib.parse import urlparse

        if request.path != "/" or request.method != "GET":
            return None
        want = urlparse(cfg.get("redirect_uri") or "")
        if not want.hostname or want.hostname not in ("localhost", "127.0.0.1"):
            return None
        if request.host.split(":")[0] == want.hostname:
            return None
        if request.host.split(":")[0] not in ("localhost", "127.0.0.1"):
            return None
        port = request.host.split(":")[1] if ":" in request.host else "80"
        qs = request.query_string.decode("utf-8", "ignore")
        return redirect(f"http://{want.hostname}:{port}/" + (f"?{qs}" if qs else ""))

    # ------------------------------------------------------------------
    # 页面
    # ------------------------------------------------------------------

    @app.get("/")
    def index():
        return render_template("index.html", demo_mode=demo_mode)

    @app.get("/login")
    def login():
        if demo_mode:
            return redirect("/")
        if not cfgmod.is_configured(cfg):
            return redirect("/?need_config=1")
        st = secrets.token_urlsafe(24)
        session["oauth_state"] = st
        return redirect(sso.authorize_url(cfg, st))

    @app.get("/callback")
    def callback():
        if request.args.get("error"):
            err = request.args.get("error", "")
            desc = request.args.get("error_description") or err
            if err == "invalid_scope":
                desc = (
                    "EVE 应用没有勾选程序需要的权限。请到 developers.eveonline.com 的应用设置里补勾 "
                    "esi-planets.manage_planets.v1、esi-assets.read_assets.v1、"
                    "esi-universe.read_structures.v1 后再试"
                )
            return redirect("/?error=" + quote(desc))
        code = request.args.get("code")
        st = request.args.get("state")
        expected = session.pop("oauth_state", None)
        if not code:
            return redirect("/?error=" + quote("回调缺少 code 参数"))
        if not expected or st != expected:
            return redirect("/?error=" + quote("state 校验失败，请重新点击登录（可能是浏览器拦截了 cookie）"))
        try:
            data = sso.exchange_code(cfg, code)
            info = sso.character_from_token(data["access_token"])
        except sso.SSOError as exc:
            return redirect("/?error=" + quote(str(exc)))

        store.upsert_character(
            info["character_id"],
            info["name"],
            info["owner_hash"],
            info["scopes"],
            data.get("refresh_token") or "",
        )
        state["tokens"].invalidate(info["character_id"])
        with state["lock"]:
            state["memo"].update({"ts": 0, "key": None, "overview": None, "prev": None})
        return redirect("/?welcome=" + quote(info["name"]))

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    @app.get("/api/state")
    def api_state():
        return jsonify(
            {
                "demo": demo_mode,
                "config": cfgmod.masked(cfg),
                "characters": characters(),
                "encryption": store.encryption_enabled(),
                "cache": store.cache_stats(),
                "scopes": sso.SCOPES,
                "data_dir": str(paths.APP_DIR),
                "portable": paths.PORTABLE,
                "onedrive": paths.ONEDRIVE,
                "about": {
                    "version": about.VERSION,
                    "wisher": about.WISHER,
                    "creator": about.CREATOR,
                },
            }
        )

    def _refresh_prices(payload: dict) -> None:
        """按 15 分钟缓存规则拿报价，并就地更新总览里的价格和库存估值。

        fetch_prices 自己有 TTL：没超过 15 分钟就直接返回缓存，不会发请求。
        """
        prices = market.fetch_prices()
        if not prices:
            payload["prices_at"] = market.last_fetched()
            return
        payload["prices"] = {str(k): v for k, v in prices.items()}
        payload["prices_at"] = market.last_fetched()
        summary = payload.get("summary")
        if summary:
            summary["stock_value"] = pi.stock_value(
                payload.get("colonies") or [], payload.get("assets") or {}, prices
            )

    def _data_version() -> str:
        """译文或解析逻辑变了就让缓存失效，否则界面会一直显示旧名字。"""
        h = hashlib.sha1()
        h.update(repr(i18n.COMMODITIES).encode("utf-8"))
        h.update(repr(sorted(i18n.STRUCT_CN.items())).encode("utf-8"))
        h.update(repr(sorted(i18n.PLANET_PREFIX_CN.items())).encode("utf-8"))
        h.update(LOGIC_VERSION.encode())
        return h.hexdigest()[:10]

    def _signature(chars) -> str:
        return _data_version() + "|" + ",".join(str(c["id"]) for c in chars)

    def current_overview():
        """取当前总览：内存 → SQLite，全程不碰 ESI。"""
        memo = state["memo"]
        if memo["overview"]:
            return memo["overview"]
        cached = None if demo_mode else store.load_overview()
        if cached:
            with state["lock"]:
                memo["overview"] = cached["data"]
                memo["key"] = cached["signature"]
                memo["ts"] = cached["ts"]
            return cached["data"]
        return None

    def subset(overview: dict, ids: set[int] | None) -> dict:
        if not ids:
            return overview
        out = dict(overview)
        out["colonies"] = [c for c in overview.get("colonies", []) if c["character_id"] in ids]
        assets = dict(overview.get("assets") or {})
        assets["characters"] = [c for c in assets.get("characters", []) if c["id"] in ids]
        out["assets"] = assets
        return out

    @app.get("/api/overview")
    def api_overview():
        """默认直接返回上次构建的结果，只有 force=1（点刷新）才重新请求 ESI。"""
        force = request.args.get("force") in ("1", "true", "yes")
        chars = characters()
        if not chars:
            return jsonify(
                {
                    "characters": [],
                    "summary": None,
                    "colonies": [],
                    "needs_login": True,
                    "demo": demo_mode,
                }
            )

        sig = _signature(chars)
        if not force:
            overview = current_overview()
            if overview and state["memo"].get("key") == sig:
                ttl = int(cfg.get("overview_ttl") or 0)
                age = time.time() - (state["memo"].get("ts") or 0)
                if ttl <= 0 or age <= ttl:  # ttl=0 表示手动刷新，永不过期
                    payload = dict(overview)
                    payload["cached"] = True
                    _refresh_prices(payload)
                    return jsonify(payload)

        try:
            overview = pi.build_overview(get_esi(), chars)
        except ESIError as exc:
            return jsonify({"error": f"ESI 请求失败：{exc.message}"}), 502
        except sso.AuthError as exc:
            return jsonify({"error": str(exc), "needs_login": True}), 401
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"构建数据失败：{exc}"}), 500

        prev = _record_snapshots(overview, demo_mode)
        if not demo_mode:
            store.save_overview(sig, overview)

        with state["lock"]:
            state["memo"].update({"ts": time.time(), "key": sig, "overview": overview, "prev": prev})
        payload = dict(overview)
        payload["cached"] = False
        payload["prices_at"] = market.last_fetched()
        return jsonify(payload)

    @app.get("/api/colony/<int:cid>/<int:pid>")
    def api_colony(cid: int, pid: int):
        """原始 ESI 数据，带缩进方便在浏览器里直接看，用于排查数据对不上的问题。"""
        try:
            detail = get_esi().get(f"/characters/{cid}/planets/{pid}/", auth=True, cid=cid, force=True)
        except ESIError as exc:
            return jsonify({"error": exc.message}), 502
        if not detail:
            return jsonify({"error": "殖民地不存在或无权限"}), 404
        return Response(
            json.dumps(detail, indent=2, ensure_ascii=False),
            mimetype="application/json; charset=utf-8",
        )

    @app.get("/api/inventory")
    def api_inventory():
        overview = current_overview()
        if not overview:
            return jsonify({"rows": [], "by_type": [], "totals": {}, "empty": True})

        raw = request.args.get("chars", "").strip()
        ids = {int(x) for x in raw.split(",") if x.strip().isdigit()} if raw else None
        return jsonify(pi.build_inventory(subset(overview, ids), state["memo"].get("prev")))

    @app.post("/api/shutdown")
    def api_shutdown():
        """只允许本机调用；给"静默启动"提供一种不用开任务管理器的退出方式。"""
        if request.remote_addr not in ("127.0.0.1", "::1"):
            return jsonify({"error": "只允许本机操作"}), 403
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return jsonify({"ok": True})

    @app.get("/api/export.csv")
    def api_export():
        overview = current_overview()
        if not overview:
            return Response("no data", status=400, mimetype="text/plain")
        raw = request.args.get("chars", "").strip()
        ids = {int(x) for x in raw.split(",") if x.strip().isdigit()} if raw else None
        inv = pi.build_inventory(subset(overview, ids), state["memo"].get("prev"))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["角色", "位置", "来源", "物资", "英文名", "层级", "数量", "体积(m3)", "较上次变化"])
        for r in inv["rows"]:
            w.writerow(
                [
                    r["character_name"],
                    r["planet_name"],
                    r["source_label"],
                    r["name"],
                    r.get("name_en", ""),
                    r["tier"],
                    int(r["amount"]),
                    f"{r['volume_m3']:.2f}",
                    "" if r["delta"] is None else int(r["delta"]),
                ]
            )
        w.writerow([])
        w.writerow(["汇总（按物资）"])
        w.writerow(["物资", "英文名", "层级", "总数量", "总体积(m3)", "来源数", "较上次变化"])
        for r in inv["by_type"]:
            w.writerow(
                [
                    r["name"],
                    r.get("name_en", ""),
                    r["tier"],
                    int(r["amount"]),
                    f"{r['volume_m3']:.2f}",
                    r["colonies"],
                    "" if r["delta"] is None else int(r["delta"]),
                ]
            )
        data = "\ufeff" + buf.getvalue()
        fname = time.strftime("PI_inventory_%Y%m%d_%H%M.csv")
        return Response(
            data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.get("/api/settings")
    def api_settings_get():
        return jsonify(cfgmod.masked(cfg))

    @app.post("/api/settings")
    def api_settings_post():
        body = request.get_json(silent=True) or {}
        # 留空表示不修改（界面上是星号占位，看不到原值）
        if body.get("client_id"):
            cfg["client_id"] = str(body["client_id"]).strip()
        if body.get("client_secret"):
            cfg["client_secret"] = str(body["client_secret"]).strip()
        if body.get("overview_ttl") is not None:
            try:
                cfg["overview_ttl"] = max(0, int(body["overview_ttl"]))
            except (TypeError, ValueError):
                pass
        if body.get("redirect_uri"):
            cfg["redirect_uri"] = str(body["redirect_uri"]).strip()
        if body.get("contact_email") is not None:
            cfg["contact_email"] = str(body["contact_email"]).strip()
        cfgmod.save(cfg)
        state["esi"] = None  # 让客户端用新配置重建
        store.clear_overview()
        with state["lock"]:
            state["memo"].update({"ts": 0, "key": None, "overview": None, "prev": None})
        return jsonify({"ok": True, "config": cfgmod.masked(cfg)})

    @app.post("/api/logout/<int:cid>")
    def api_logout(cid: int):
        if demo_mode:
            return jsonify({"ok": True, "demo": True})
        store.delete_character(cid)
        state["tokens"].invalidate(cid)
        store.clear_overview()
        with state["lock"]:
            state["memo"].update({"ts": 0, "key": None, "overview": None, "prev": None})
        return jsonify({"ok": True})

    @app.post("/api/cache/clear")
    def api_cache_clear():
        n = store.clear_cache()
        store.clear_overview()
        with state["lock"]:
            state["memo"].update({"ts": 0, "key": None, "overview": None, "prev": None})
        return jsonify({"ok": True, "cleared": n})

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "time": time.time()})

    @app.teardown_request
    def _teardown(_exc=None):
        return None

    return app


def _record_snapshots(overview: dict, demo_mode: bool) -> dict:
    """记录库存快照，并返回「上一次快照」用于算增减。演示模式不落库。"""
    if demo_mode:
        return {}

    now = overview.get("generated_at") or time.time()
    prev: dict[tuple, float] = {}
    for colony in overview.get("colonies", []):
        cid, pid = colony["character_id"], colony["planet_id"]
        contents = {}
        for storage in colony.get("storages", []):
            for item in storage.get("items", []):
                contents[item["type_id"]] = contents.get(item["type_id"], 0) + item["amount"]

        older = store.last_snapshot(cid, pid, before=now - SNAPSHOT_MIN_GAP)
        if older:
            for tid, amount in older["data"].items():
                prev[(cid, pid, int(tid))] = amount

        latest = store.last_snapshot(cid, pid)
        if not latest or now - latest["ts"] > SNAPSHOT_MIN_GAP:
            if contents:
                store.add_snapshot(cid, pid, contents)
    return prev


def _show_error(msg: str) -> None:
    """打包成 exe 后没有控制台，用系统弹窗把错误显示出来。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "行星工业监视器", 0x10)
    except Exception:  # noqa: BLE001
        print(msg)


def _already_running(port: int) -> bool:
    """端口上是不是已经跑着本程序（而不是别的软件占了端口）。"""
    try:
        r = requests.get(f"http://127.0.0.1:{port}/api/state", timeout=1.5)
        return r.ok and "config" in r.json()
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="EVE 行星工业监视器")
    parser.add_argument("--demo", action="store_true", help="演示模式：用假数据看界面")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    cfg = cfgmod.load()
    host = args.host or cfg.get("host") or "127.0.0.1"
    port = args.port or int(cfg.get("port") or 5000)

    url = f"http://localhost:{port}/"

    # 已经在跑：不再重复启动，直接把页面打开（这样双击多少次都安全）
    if _already_running(port):
        print(f"程序已在运行，直接打开：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    app = create_app(cfg, demo_mode=args.demo)
    print("=" * 62)
    print("  EVE 行星工业监视器")
    print(f"  地址：{url}")
    if args.demo:
        print("  模式：演示（假数据，未连接 EVE）")
    elif not cfgmod.is_configured(cfg):
        print("  提示：还没配置 client_id/secret，请到页面「设置」里填写")
    print(f"  回调地址：{cfg.get('redirect_uri')}")
    print("=" * 62)

    # 打包成 exe 时挂个托盘图标，右键能打开页面或退出
    tray.start(url)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=host, port=port, debug=False, threaded=True)
    except OSError as exc:
        msg = f"端口 {port} 无法使用：{exc}\n\n可能被别的程序占用了，换个端口再试。"
        if paths.FROZEN:
            _show_error(msg)
        else:
            print(f"[错误] {msg}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        if paths.FROZEN:
            _show_error(f"程序启动失败：\n\n{exc}\n\n{traceback.format_exc()[-900:]}")
        else:
            raise
