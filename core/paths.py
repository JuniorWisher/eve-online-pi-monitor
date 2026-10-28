"""路径解析：区分「可写数据」和「只读资源」。

数据默认放「我的文档\\行星工业监视器」：
- 用 Windows 的 Known Folder 接口取真实路径，兼容文档目录被重定向（例如 OneDrive）
- 每个用户各自一份，把 exe 分享给别人时对方会用自己的文档目录，互不影响
- exe 挪位置、升级覆盖都不影响数据

想要绿色便携版：在 exe 旁边放一个空的 portable.txt，数据改为放在 exe 同级目录
（U 盘、移动硬盘那种用法）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "行星工业监视器"

FROZEN = bool(getattr(sys, "frozen", False))

# 程序所在目录（exe 同级 / 源码根目录），也是便携模式下的数据目录
EXE_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent.parent
)

# 只读资源：打包后被解到临时目录，必须用 _MEIPASS
RES_DIR = Path(getattr(sys, "_MEIPASS", EXE_DIR)) if FROZEN else EXE_DIR

# 便携模式：exe 旁边有 portable.txt 就把数据放在旁边
PORTABLE = (EXE_DIR / "portable.txt").exists()


def _home_dir() -> Path:
    """用户主目录。用 USERPROFILE 而不是 Path.home()，避免被各种环境改写。"""
    base = os.environ.get("USERPROFILE")
    return Path(base) if base else Path.home()


def _default_data_dir() -> Path:
    return _home_dir() / APP_NAME


APP_DIR = EXE_DIR if PORTABLE else _default_data_dir()

# 文档目录被 OneDrive 接管时给个提醒（云同步 SQLite 有风险）
ONEDRIVE = "onedrive" in str(APP_DIR).lower()


def resource(*parts: str) -> Path:
    """只读资源路径（模板、静态文件）。"""
    return RES_DIR.joinpath(*parts)


def data_path(*parts: str) -> Path:
    """可写路径（配置、数据库）。"""
    return APP_DIR.joinpath(*parts)


def describe() -> str:
    """给界面/日志看的一句说明。"""
    mode = "便携模式（exe 同级目录）" if PORTABLE else "用户主目录"
    return f"{mode}：{APP_DIR}"
