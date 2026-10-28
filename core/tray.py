"""系统托盘图标：右键可「打开监控」和「退出」。

只有打包成 exe（没有控制台）时才启用——从源码跑的时候有命令行窗口可以 Ctrl+C，
再挂个托盘反而碍事。
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser

from . import paths


def _load_image():
    from PIL import Image

    ico = paths.resource("app.ico")
    if ico.exists():
        try:
            return Image.open(ico)
        except Exception:  # noqa: BLE001
            pass
    # 兜底：画一个纯色方块，至少不会因为缺图标启动失败
    return Image.new("RGBA", (64, 64), (13, 19, 28, 255))


# 连续点击托盘时，这段时间内只打开一次（双击、手抖连点都拦得住）
OPEN_COOLDOWN = 10.0


def make_opener(url: str, cooldown: float = OPEN_COOLDOWN):
    """返回一个"带冷却的打开器"：冷却期内的调用直接忽略。

    托盘图标的默认动作是左键单击，用户连点几下会弹出一堆浏览器窗口，
    所以这里做去重。返回 True 表示真的打开了。
    """
    last = [0.0]

    def open_once(*_args, **_kwargs) -> bool:
        now = time.time()
        if now - last[0] < cooldown:
            return False
        last[0] = now
        try:
            webbrowser.open(url, new=0, autoraise=True)
        except Exception:  # noqa: BLE001 - 没有浏览器也不能让托盘崩掉
            return False
        return True

    return open_once


def start(url: str, on_quit=None) -> bool:
    """启动托盘图标，成功返回 True。"""
    if not paths.FROZEN:
        return False

    try:
        import pystray
        from pystray import MenuItem as Item
    except Exception:  # noqa: BLE001 - 没装依赖就安静跳过
        return False

    _open = make_opener(url)

    def _quit(icon, _item=None):
        icon.visible = False
        icon.stop()
        if on_quit:
            try:
                on_quit()
            except Exception:  # noqa: BLE001
                pass
        os._exit(0)

    icon = pystray.Icon(
        "planetary-monitor",
        _load_image(),
        "行星工业监视器",
        menu=pystray.Menu(
            Item("打开监控", _open, default=True),
            Item("退出", _quit),
        ),
    )

    try:
        threading.Thread(target=icon.run, daemon=True, name="tray").start()
        return True
    except Exception:  # noqa: BLE001
        return False
