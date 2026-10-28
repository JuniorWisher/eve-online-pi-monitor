"""系统托盘图标：右键可「打开监控」和「退出」。

只有打包成 exe（没有控制台）时才启用——从源码跑的时候有命令行窗口可以 Ctrl+C，
再挂个托盘反而碍事。
"""

from __future__ import annotations

import os
import threading
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


def start(url: str, on_quit=None) -> bool:
    """启动托盘图标，成功返回 True。"""
    if not paths.FROZEN:
        return False

    try:
        import pystray
        from pystray import MenuItem as Item
    except Exception:  # noqa: BLE001 - 没装依赖就安静跳过
        return False

    def _open(_icon=None, _item=None):
        webbrowser.open(url)

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
