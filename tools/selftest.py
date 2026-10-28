"""自检脚本：用演示数据跑一遍解析流程，验证统计逻辑。

    python tools/selftest.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core import demo, pi  # noqa: E402


def main() -> int:
    esi = demo.make_esi()
    chars = demo.characters()
    overview = pi.build_overview(esi, chars)

    s = overview["summary"]
    print("=" * 66)
    print("解析结果")
    print("=" * 66)
    print(f"角色        : {s['characters']}")
    print(f"殖民地      : {s['colonies']}")
    print(f"提取器      : {s['extractors']}   工厂: {s['factories']}")
    print(f"已到期      : {s['expired']}   2h内: {s['expiring_2h']}   24h内: {s['expiring_24h']}")
    print(f"仓储        : {s['storage_m3']:.1f} / {s['capacity_m3']:.0f} m3")
    print(f"库存分层    : {s['tier_units']}")
    print(f"产出预估    : {s['tier_estimate']}")
    print()

    for c in overview["colonies"]:
        rem = "—" if c["remaining"] is None else f"{c['remaining'] / 3600:.1f}h"
        print(f"[{c['urgency']:8}] {c['planet_name']:22} {c['system_name']:10} "
              f"{c['security']}  剩余 {rem:>6}  提取器 {len(c['extractors'])} 工厂 {len(c['factories'])} "
              f"仓储 {c['storage_m3']:.0f}m3")
        for e in c["extractors"]:
            print(f"           └ {e['product']:18} {e['tier']}  每周期 {e['qty_per_cycle']:.0f}  "
                  f"{e['heads']} 头  预计总产 {e['est_total']:.0f}  已产 {e['est_done']:.0f}")
        for f in c["factories"]:
            print(f"           └ 工厂 {f['product'] or '(未推断)'} 图纸#{f['schematic_id']}")
    print()

    inv = pi.build_inventory(overview)
    print("=" * 66)
    print(f"库存报表：{inv['totals']['types']} 种物资 / {inv['totals']['units']:.0f} 单位 "
          f"/ {inv['totals']['volume_m3']:.1f} m3")
    print("=" * 66)
    for r in inv["by_type"]:
        print(f"  {r['tier']:3} {r['name']:24} {r['amount']:>9.0f} 单位  {r['volume_m3']:>8.1f} m3  "
              f"{r['colonies']} 处来源 {r['sources']}")

    cus = overview.get("assets") or {}
    print()
    print("=" * 66)
    print(f"资产库存：海关 {cus['totals']['customs_units']:.0f} 单位 / "
          f"建筑 {cus['totals']['structure_units']:.0f} 单位")
    print("=" * 66)
    for ch in cus["characters"]:
        if not ch["ok"]:
            print(f"  {ch['name']:14} 失败：{ch['error']}")
            continue
        for key in ("customs", "structure"):
            b = ch["buckets"].get(key)
            if b:
                print(f"  {ch['name']:14} {b['label']:6} {b['units']:>8.0f} 单位 "
                      f"{b['volume_m3']:>8.1f} m3  {b['locations']} 处  {b['types']} 种")

    # 断言关键字段，防止解析退化
    assert overview["colonies"], "没有解析出殖民地"
    assert s["extractors"] >= 4, f"提取器数量异常：{s['extractors']}"
    assert s["expired"] == 1, f"到期数量异常：{s['expired']}"
    assert inv["rows"], "库存报表为空"
    assert any(r["tier"] == "P3" for r in inv["by_type"]), "层级识别失败"
    assert cus["totals"]["customs_units"] > 0, "海关库存没有统计到"
    assert cus["totals"]["structure_units"] > 0, "建筑库存没有统计到"
    assert any(r["source"] == "customs" for r in inv["rows"]), "海关数据没有并入报表"
    assert any(r["source"] == "structure" for r in inv["rows"]), "建筑数据没有并入报表"
    assert all(r["source"] != "solar_system" for r in inv["rows"]), "solar_system 不该被统计"
    print("\n自检通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
