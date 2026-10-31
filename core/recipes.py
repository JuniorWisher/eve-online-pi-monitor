"""行星工业合成配方表（type_id → 原料）。

数据来源：程序从 ESI 的工厂路由自动提取（64 条），
缺的 4 条由用户在游戏内核对后补入（Recursive Computing Module 及其 3 个 P3 原料）。

数量规则（游戏内固定）：
  P1  3000 P0 → 20 P1
  P2  40+40 P1 → 5 P2
  P3  10+10（或 10+10+10）P2 → 3 P3
  P4  6+6+6 P3 → 1 P4；或 6+6 P3 + 40 P1 → 1 P4
"""

RECIPES: dict[int, dict] = {
    2393: {"name": 'Bacteria', "out": 20, "in": {2073: 3000}, "tier": "P1"},  # P1
    2396: {"name": 'Biofuels', "out": 20, "in": {2288: 3000}, "tier": "P1"},  # P1
    3779: {"name": 'Biomass', "out": 20, "in": {2286: 3000}, "tier": "P1"},  # P1
    2401: {"name": 'Chiral Structures', "out": 20, "in": {2306: 3000}, "tier": "P1"},  # P1
    2390: {"name": 'Electrolytes', "out": 20, "in": {2309: 3000}, "tier": "P1"},  # P1
    2397: {"name": 'Industrial Fibers', "out": 20, "in": {2305: 3000}, "tier": "P1"},  # P1
    2392: {"name": 'Oxidizing Compound', "out": 20, "in": {2311: 3000}, "tier": "P1"},  # P1
    3683: {"name": 'Oxygen', "out": 20, "in": {2310: 3000}, "tier": "P1"},  # P1
    2389: {"name": 'Plasmoids', "out": 20, "in": {2308: 3000}, "tier": "P1"},  # P1
    2399: {"name": 'Precious Metals', "out": 20, "in": {2270: 3000}, "tier": "P1"},  # P1
    2395: {"name": 'Proteins', "out": 20, "in": {2287: 3000}, "tier": "P1"},  # P1
    2398: {"name": 'Reactive Metals', "out": 20, "in": {2267: 3000}, "tier": "P1"},  # P1
    9828: {"name": 'Silicon', "out": 20, "in": {2307: 3000}, "tier": "P1"},  # P1
    2400: {"name": 'Toxic Metals', "out": 20, "in": {2272: 3000}, "tier": "P1"},  # P1
    3645: {"name": 'Water', "out": 20, "in": {2268: 3000}, "tier": "P1"},  # P1
    2329: {"name": 'Biocells', "out": 5, "in": {2396: 40, 2399: 40}, "tier": "P2"},  # P2
    3828: {"name": 'Construction Blocks', "out": 5, "in": {2398: 40, 2400: 40}, "tier": "P2"},  # P2
    9836: {"name": 'Consumer Electronics', "out": 5, "in": {2400: 40, 2401: 40}, "tier": "P2"},  # P2
    9832: {"name": 'Coolant', "out": 5, "in": {2390: 40, 3645: 40}, "tier": "P2"},  # P2
    44: {"name": 'Enriched Uranium', "out": 5, "in": {2399: 40, 2400: 40}, "tier": "P2"},  # P2
    3693: {"name": 'Fertilizer', "out": 5, "in": {2393: 40, 2395: 40}, "tier": "P2"},  # P2
    15317: {"name": 'Genetically Enhanced Livestock', "out": 5, "in": {2395: 40, 3779: 40}, "tier": "P2"},  # P2
    3725: {"name": 'Livestock', "out": 5, "in": {2395: 40, 2396: 40}, "tier": "P2"},  # P2
    3689: {"name": 'Mechanical Parts', "out": 5, "in": {2398: 40, 2399: 40}, "tier": "P2"},  # P2
    2327: {"name": 'Microfiber Shielding', "out": 5, "in": {2397: 40, 9828: 40}, "tier": "P2"},  # P2
    9842: {"name": 'Miniature Electronics', "out": 5, "in": {2401: 40, 9828: 40}, "tier": "P2"},  # P2
    2463: {"name": 'Nanites', "out": 5, "in": {2393: 40, 2398: 40}, "tier": "P2"},  # P2
    2317: {"name": 'Oxides', "out": 5, "in": {2392: 40, 3683: 40}, "tier": "P2"},  # P2
    2321: {"name": 'Polyaramids', "out": 5, "in": {2392: 40, 2397: 40}, "tier": "P2"},  # P2
    3695: {"name": 'Polytextiles', "out": 5, "in": {2396: 40, 2397: 40}, "tier": "P2"},  # P2
    9830: {"name": 'Rocket Fuel', "out": 5, "in": {2389: 40, 2390: 40}, "tier": "P2"},  # P2
    3697: {"name": 'Silicate Glass', "out": 5, "in": {2392: 40, 9828: 40}, "tier": "P2"},  # P2
    9838: {"name": 'Superconductors', "out": 5, "in": {2389: 40, 3645: 40}, "tier": "P2"},  # P2
    2312: {"name": 'Supertensile Plastics', "out": 5, "in": {3683: 40, 3779: 40}, "tier": "P2"},  # P2
    3691: {"name": 'Synthetic Oil', "out": 5, "in": {2390: 40, 3683: 40}, "tier": "P2"},  # P2
    2319: {"name": 'Test Cultures', "out": 5, "in": {2393: 40, 3645: 40}, "tier": "P2"},  # P2
    9840: {"name": 'Transmitter', "out": 5, "in": {2389: 40, 2401: 40}, "tier": "P2"},  # P2
    3775: {"name": 'Viral Agent', "out": 5, "in": {2393: 40, 3779: 40}, "tier": "P2"},  # P2
    2328: {"name": 'Water-Cooled CPU', "out": 5, "in": {2398: 40, 3645: 40}, "tier": "P2"},  # P2
    2358: {"name": 'Biotech Research Reports', "out": 3, "in": {2463: 10, 3725: 10, 3828: 10}, "tier": "P3"},  # P3
    2345: {"name": 'Camera Drones', "out": 3, "in": {3697: 10, 9830: 10}, "tier": "P3"},  # P3
    2344: {"name": 'Condensates', "out": 3, "in": {2317: 10, 9832: 10}, "tier": "P3"},  # P3
    2367: {"name": 'Cryoprotectant Solution', "out": 3, "in": {2319: 10, 3691: 10, 3693: 10}, "tier": "P3"},  # P3
    17392: {"name": 'Data Chips', "out": 3, "in": {2312: 10, 2327: 10}, "tier": "P3"},  # P3
    2348: {"name": 'Gel-Matrix Biopaste', "out": 3, "in": {2317: 10, 2329: 10, 9838: 10}, "tier": "P3"},  # P3
    9834: {"name": 'Guidance Systems', "out": 3, "in": {2328: 10, 9840: 10}, "tier": "P3"},  # P3
    2366: {"name": 'Hazmat Detection Systems', "out": 3, "in": {3695: 10, 3775: 10, 9840: 10}, "tier": "P3"},  # P3
    2361: {"name": 'Hermetic Membranes', "out": 3, "in": {2321: 10, 15317: 10}, "tier": "P3"},  # P3
    17898: {"name": 'High-Tech Transmitters', "out": 3, "in": {2321: 10, 9840: 10}, "tier": "P3"},  # P3
    2360: {"name": 'Industrial Explosives', "out": 3, "in": {3693: 10, 3695: 10}, "tier": "P3"},  # P3
    2354: {"name": 'Neocoms', "out": 3, "in": {2329: 10, 3697: 10}, "tier": "P3"},  # P3
    2352: {"name": 'Nuclear Reactors', "out": 3, "in": {44: 10, 2327: 10}, "tier": "P3"},  # P3
    9846: {"name": 'Planetary Vehicles', "out": 3, "in": {2312: 10, 3689: 10, 9842: 10}, "tier": "P3"},  # P3
    9848: {"name": 'Robotics', "out": 3, "in": {3689: 10, 9836: 10}, "tier": "P3"},  # P3
    2351: {"name": 'Smartfab Units', "out": 3, "in": {3828: 10, 9842: 10}, "tier": "P3"},  # P3
    2349: {"name": 'Supercomputers', "out": 3, "in": {2328: 10, 9832: 10, 9836: 10}, "tier": "P3"},  # P3
    2346: {"name": 'Synthetic Synapses', "out": 3, "in": {2312: 10, 2319: 10}, "tier": "P3"},  # P3
    12836: {"name": 'Transcranial Microcontrollers', "out": 3, "in": {2329: 10, 2463: 10}, "tier": "P3"},  # P3
    17136: {"name": 'Ukomi Superconductors', "out": 3, "in": {3691: 10, 9838: 10}, "tier": "P3"},  # P3
    28974: {"name": 'Vaccines', "out": 3, "in": {3725: 10, 3775: 10}, "tier": "P3"},  # P3
    2867: {"name": 'Broadcast Node', "out": 1, "in": {2354: 6, 17392: 6, 17898: 6}, "tier": "P4"},  # P4
    2868: {"name": 'Integrity Response Drones', "out": 1, "in": {2348: 6, 2366: 6, 9846: 6}, "tier": "P4"},  # P4
    2869: {"name": 'Nano-Factory', "out": 1, "in": {2360: 6, 2398: 40, 17136: 6}, "tier": "P4"},  # P4
    2870: {"name": 'Organic Mortar Applicators', "out": 1, "in": {2344: 6, 2393: 40, 9848: 6}, "tier": "P4"},  # P4
    2871: {"name": 'Recursive Computing Module', "out": 1, "in": {2346: 6, 9834: 6, 12836: 6}, "tier": "P4"},  # P4
    2872: {"name": 'Self-Harmonizing Power Core', "out": 1, "in": {2345: 6, 2352: 6, 2361: 6}, "tier": "P4"},  # P4
    2875: {"name": 'Sterile Conduits', "out": 1, "in": {2351: 6, 3645: 40, 28974: 6}, "tier": "P4"},  # P4
    2876: {"name": 'Wetware Mainframe', "out": 1, "in": {2349: 6, 2358: 6, 2367: 6}, "tier": "P4"},  # P4
}
