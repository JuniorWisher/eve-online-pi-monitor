"""行星工业名词的简体中文本地化。

ESI 只返回英文名，这里按 type_id 映射到中文（ID 稳定，比按名字匹配可靠）。
中文名参考 EVE 中文客户端与中文社区通用译名；个别物资在国服与国际服译名有差异时，
取社区最常用的说法，同时在界面上保留英文原名作为悬浮提示。

system / region 等星系专有名词不做翻译——玩家普遍使用英文星系名。
"""

from __future__ import annotations

# (type_id, 英文名, 中文名)
COMMODITIES: list[tuple[int, str, str]] = [
    # ---- P0 原料 ----
    (2267, "Base Metals", "基础金属"),
    (2307, "Felsic Magma", "长英矿物岩浆"),
    (2272, "Heavy Metals", "重金属"),
    (2270, "Noble Metals", "贵金属"),
    (2306, "Non-CS Crystals", "非立方晶体"),
    (2268, "Aqueous Liquids", "水基液体"),
    (2309, "Ionic Solutions", "离子溶液"),
    (2310, "Noble Gas", "稀有气体"),
    (2311, "Reactive Gas", "活性气体"),
    (2308, "Suspended Plasma", "悬浮等离子"),
    (2305, "Autotrophs", "自养生物"),
    (2288, "Carbon Compounds", "碳化合物"),
    (2287, "Complex Organisms", "复杂有机生命体"),
    (2073, "Microorganisms", "微生物"),
    (2286, "Planktic Colonies", "浮游生物群"),
    # ---- P1 基础商品 ----
    (2393, "Bacteria", "细菌"),
    (2396, "Biofuels", "生物燃料"),
    (3779, "Biomass", "生物质"),
    (2401, "Chiral Structures", "手性结构"),
    (2390, "Electrolytes", "电解物"),
    (2397, "Industrial Fibers", "工业纤维"),
    (2392, "Oxidizing Compound", "氧化剂"),
    (3683, "Oxygen", "氧"),
    (2389, "Plasmoids", "等离子体团"),
    (2399, "Precious Metals", "稀有金属"),
    (2395, "Proteins", "蛋白质"),
    (2398, "Reactive Metals", "反应金属"),
    (9828, "Silicon", "硅"),
    (2400, "Toxic Metals", "有毒金属"),
    (3645, "Water", "水"),
    # ---- P2 精炼商品 ----
    (2329, "Biocells", "生物电池"),
    (3828, "Construction Blocks", "建筑模块"),
    (9836, "Consumer Electronics", "消费级电器"),
    (9832, "Coolant", "冷却剂"),
    (44, "Enriched Uranium", "核能燃料"),
    (3693, "Fertilizer", "肥料"),
    (15317, "Genetically Enhanced Livestock", "基因肉制品"),
    (3725, "Livestock", "家畜"),
    (3689, "Mechanical Parts", "机械元件"),
    (2327, "Microfiber Shielding", "微纤维护盾"),
    (9842, "Miniature Electronics", "微型电子元件"),
    (2463, "Nanites", "纳米体"),
    (2317, "Oxides", "氧化物"),
    (2321, "Polyaramids", "聚芳酰胺"),
    (3695, "Polytextiles", "合成纺织品"),
    (9830, "Rocket Fuel", "火箭燃料"),
    (3697, "Silicate Glass", "硅酸盐玻璃"),
    (9838, "Superconductors", "超导体"),
    (2312, "Supertensile Plastics", "超张力塑料"),
    (3691, "Synthetic Oil", "合成油"),
    (2319, "Test Cultures", "培养基"),
    (9840, "Transmitter", "传信器"),
    (3775, "Viral Agent", "病原体"),
    (2328, "Water-Cooled CPU", "水冷CPU"),
    # ---- P3 特种商品 ----
    (2358, "Biotech Research Reports", "生物技术研究报告"),
    (2345, "Camera Drones", "监控无人机"),
    (2344, "Condensates", "凝缩液"),
    (2367, "Cryoprotectant Solution", "冷冻保存防腐剂"),
    (17392, "Data Chips", "数据芯片"),
    (2348, "Gel-Matrix Biopaste", "凝胶基质生物胶"),
    (9834, "Guidance Systems", "制导系统"),
    (2366, "Hazmat Detection Systems", "危险物探测系统"),
    (2361, "Hermetic Membranes", "密封薄膜"),
    (17898, "High-Tech Transmitters", "高科技传信器"),
    (2360, "Industrial Explosives", "工业炸药"),
    (2354, "Neocoms", "控制面板"),
    (2352, "Nuclear Reactors", "核反应堆"),
    (9846, "Planetary Vehicles", "大气内穿梭机"),
    (9848, "Robotics", "机器人技术"),
    (2351, "Smartfab Units", "灵巧单元建筑模块"),
    (2349, "Supercomputers", "超级计算机"),
    (2346, "Synthetic Synapses", "合成神经键"),
    (12836, "Transcranial Microcontrollers", "透颅微控器"),
    (17136, "Ukomi Superconductors", "乌克米超导体"),
    (28974, "Vaccines", "疫苗"),
    # ---- P4 高级商品 ----
    (2867, "Broadcast Node", "广播节点"),
    (2868, "Integrity Response Drones", "反破损快速反应无人机"),
    (2869, "Nano-Factory", "纳米-工厂"),
    (2870, "Organic Mortar Applicators", "有机砂浆喷注器"),
    (2871, "Recursive Computing Module", "递推计算模块"),
    (2872, "Self-Harmonizing Power Core", "自协调能源核心"),
    (2875, "Sterile Conduits", "无菌管道"),
    (2876, "Wetware Mainframe", "湿件主机"),
]

CN_BY_ID = {tid: cn for tid, _en, cn in COMMODITIES}
CN_BY_EN = {en.lower(): cn for _tid, en, cn in COMMODITIES}

# 行星建筑：类型名带星球前缀（"Temperate Launchpad"），按关键字匹配
STRUCT_CN = {
    "command center": "指挥中心",
    "extractor control unit": "采集控制单元",
    "launchpad": "发射平台",
    "spaceport": "太空港",
    "storage facility": "储藏设施",
    "basic industry facility": "基础工业设施",
    "advanced industry facility": "高级工业设施",
    "high-tech production plant": "高科技工厂",
}

# 建筑名里带「行星」二字，和游戏内一致（例如「贫瘠行星发射平台」）
PLANET_PREFIX_CN = {
    "temperate": "温带行星",
    "ice": "冰封行星",
    "oceanic": "海洋行星",
    "barren": "贫瘠行星",
    "lava": "熔岩行星",
    "plasma": "等离子行星",
    "storm": "风暴行星",
    "gas": "气态行星",
    "shattered": "碎裂行星",
}

# 行星类型标签（卡片上的小标签，不带「行星」）
PLANET_TYPE_CN = {
    "temperate": "温带",
    "ice": "冰封",
    "oceanic": "海洋",
    "barren": "贫瘠",
    "lava": "熔岩",
    "plasma": "等离子",
    "storm": "风暴",
    "gas": "气态",
    "shattered": "碎裂",
}

TIER_CN = {
    "P0": "原料",
    "P1": "基础商品",
    "P2": "精炼商品",
    "P3": "特种商品",
    "P4": "高级商品",
}


def commodity(type_id: int | None, name_en: str | None = None) -> str:
    """物资中文名。优先按 type_id，其次按英文名（演示数据用），都没有就回退英文。"""
    if type_id:
        cn = CN_BY_ID.get(int(type_id))
        if cn:
            return cn
    if name_en:
        cn = CN_BY_EN.get(name_en.strip().lower())
        if cn:
            return cn
    return name_en or ""


def structure(name_en: str | None) -> str:
    """行星建筑中文名，保留星球前缀，例如 Temperate Launchpad -> 温带发射平台。"""
    if not name_en:
        return ""
    lower = name_en.lower()
    prefix = ""
    rest = lower
    for en_prefix, cn_prefix in PLANET_PREFIX_CN.items():
        if lower.startswith(en_prefix + " "):
            prefix = cn_prefix
            rest = lower[len(en_prefix) + 1 :]
            break
    for key, cn in STRUCT_CN.items():
        if key in rest:
            return prefix + cn
    return name_en


def planet_type(label_en: str | None) -> str:
    """行星类型：Temperate -> 温带。"""
    if not label_en:
        return ""
    return PLANET_TYPE_CN.get(label_en.strip().lower(), label_en)


def tier_label(tier: str) -> str:
    return TIER_CN.get(tier, tier)
