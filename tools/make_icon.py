"""从 docs/icon-source.jpg 生成多尺寸 app.ico。

小尺寸（16/32/48）单独处理：裁掉外围一点星空并稍微提亮，
否则在任务栏和托盘里就是一团黑，看不出是什么。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "icon-source.jpg"
OUT = ROOT / "app.ico"

# Windows 会用到的尺寸，全部塞进一个 ico
SIZES = [256, 128, 64, 48, 32, 16]


def _prepare(size: int) -> Image.Image:
    src = Image.open(SRC).convert("RGB")
    w, h = src.size

    # 小图标：裁掉外围星空让星球更占画面，再稍微提亮，否则缩小后就是一团黑
    pad_ratio = {16: 0.16, 32: 0.10, 48: 0.06}.get(size, 0)
    if pad_ratio:
        pad = int(w * pad_ratio)
        src = src.crop((pad, pad, w - pad, h - pad))
        src = ImageEnhance.Brightness(src).enhance(1.20)

    return src.resize((size, size), Image.LANCZOS)


def main() -> None:
    frames = [_prepare(s) for s in SIZES]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"已生成 {OUT}（{OUT.stat().st_size} 字节，尺寸 {SIZES}）")


if __name__ == "__main__":
    main()
