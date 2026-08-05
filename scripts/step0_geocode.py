"""
Step 0: 從通訊處地址解析出 縣市 + 鄉鎮市區。

策略(依優先序):
  1. 地址本身明確寫出「XX市/縣 + YY區/市/鎮/鄉」-> 直接讀取 (explicit)
  2. 只有路名、沒寫區(如「台北市博愛路35號」)-> 用中華郵政路名對照表反查該市裡
     這條路屬於哪一區 (road_lookup)。若同一路名在同一市裡橫跨多區則標記 ambiguous。
  3. 地址開頭直接是「XX市/鎮」但沒寫上層縣名(如「花蓮市國聯一路...」)-> 若此鄉鎮市
     名稱全國唯一,反推所屬縣市 (implicit_city)
  4. 以上都失敗 -> unresolved,留給人工檢查或線上 geocoding 補值。

參考資料來源: gnehs/TaiwanZipcode (中華郵政 6 碼郵遞區號路名對照表)
  https://github.com/gnehs/TaiwanZipcode
"""

import csv
import glob
import json
import os
import re
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "通訊處")
REF_PATH = os.path.join(BASE_DIR, "data", "reference", "zipcode.json")
OUT_PATH = os.path.join(DATA_DIR, "通訊處_行政區.csv")
REVIEW_PATH = os.path.join(DATA_DIR, "通訊處_行政區_待覆核.csv")

# 極少數路名跨區且郵政路名表用「XX號至YY巷」這種混合格式表示門牌邊界,
# scope_matches() 無法安全解析,人工核對後在此覆寫。
MANUAL_OVERRIDES = {
    "106台北市信義路4段236號10樓": ("臺北市", "大安區"),  # 信義路四段 200 號段落屬大安區(近大安森林公園),非信義區
}


FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
ARABIC_TO_CN_NUM = {str(i): c for i, c in enumerate(
    ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"])}
TOWN_SUFFIXES = ("區", "市", "鎮", "鄉")


def normalize(s: str) -> str:
    s = s.translate(FULLWIDTH_DIGITS).replace("臺", "台").replace("巿", "市")
    # 官方路名一律用中文數字表示「段」(如 信義路四段),地址常寫成阿拉伯數字(信義路4段)
    s = re.sub(r"(\d{1,2})段", lambda m: ARABIC_TO_CN_NUM.get(m.group(1), m.group(1)) + "段", s)
    return s


def load_reference():
    with open(REF_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    cities = {}  # normalized city name -> original canonical name
    districts_by_city = defaultdict(list)  # norm city -> [norm district,...] (desc length)
    road_to_districts = defaultdict(lambda: defaultdict(set))  # norm city -> norm road -> {norm district}
    district_to_cities = defaultdict(set)  # norm district -> {norm city}
    road_scopes = defaultdict(lambda: defaultdict(dict))  # norm city -> norm road -> {norm district: [scopes]}

    for city, cinfo in raw.items():
        ncity = normalize(city)
        cities[ncity] = city
        for district, dinfo in cinfo.get("areas", {}).items():
            ndistrict = normalize(district)
            districts_by_city[ncity].append(ndistrict)
            district_to_cities[ndistrict].add(ncity)
            for road, rinfo in dinfo.get("roads", {}).items():
                nroad = normalize(road)
                road_to_districts[ncity][nroad].add(ndistrict)
                road_scopes[ncity][nroad][ndistrict] = rinfo.get("scopes", [])

    for city in districts_by_city:
        districts_by_city[city] = sorted(set(districts_by_city[city]), key=len, reverse=True)

    return cities, districts_by_city, road_to_districts, district_to_cities, road_scopes


CITIES, DISTRICTS_BY_CITY, ROAD_TO_DISTRICTS, DISTRICT_TO_CITIES, ROAD_SCOPES = load_reference()
CITY_NAMES_DESC = sorted(CITIES.keys(), key=len, reverse=True)
ALL_DISTRICTS_DESC = sorted(DISTRICT_TO_CITIES.keys(), key=len, reverse=True)


def scope_matches(scope_str: str, num: int, parity: str) -> bool:
    s = re.sub(r"\s", "", scope_str)
    if "巷" in s or "弄" in s:
        return False  # 巷/弄 規則不適用於直接寫門牌號的地址
    if s == "全":
        return True
    m = re.match(r"^(\d+)號$", s)
    if m:
        return int(m.group(1)) == num
    m = re.match(r"^(單|雙)(\d+)號至(\d+)號$", s)
    if m:
        return m.group(1) == parity and int(m.group(2)) <= num <= int(m.group(3))
    m = re.match(r"^(單|雙)(\d+)號以下$", s)
    if m:
        return m.group(1) == parity and num <= int(m.group(2))
    m = re.match(r"^(單|雙)(\d+)號以上$", s)
    if m:
        return m.group(1) == parity and num >= int(m.group(2))
    return False


def disambiguate_by_house_number(city: str, road: str, candidates, remainder_after_road: str):
    m = re.match(r"^(\d+)", remainder_after_road)
    if not m:
        return None
    num = int(m.group(1))
    parity = "雙" if num % 2 == 0 else "單"
    matched = []
    for district in candidates:
        scopes = ROAD_SCOPES.get(city, {}).get(road, {}).get(district, [])
        if any(scope_matches(sc.get("scope", ""), num, parity) for sc in scopes):
            matched.append(district)
    if len(matched) == 1:
        return matched[0]
    return None


def try_town_suffix_swap(city: str, remainder: str):
    """處理行政區劃調整後地址仍用舊制的情況,例如:
    - 縣改制直轄市: 桃園縣大溪鎮 -> 桃園市大溪區 (地址仍寫「大溪鎮」)
    - 鄉鎮改制縣轄市: 彰化縣員林鎮 -> 員林市 (地址仍寫「員林鎮」)
    """
    m = re.match(r"^(.{1,3}?)(區|市|鎮|鄉)", remainder)
    if not m:
        return None, None
    base, orig_suffix = m.group(1), m.group(2)
    for suffix in TOWN_SUFFIXES:
        candidate = base + suffix
        if candidate in DISTRICTS_BY_CITY.get(city, []):
            return candidate, len(base) + len(orig_suffix)
    return None, None

LEADING_CODE_RE = re.compile(r"^\(?\d{3,6}\)?")


def strip_leading_code(addr_norm: str) -> str:
    m = LEADING_CODE_RE.match(addr_norm)
    return addr_norm[m.end():] if m else addr_norm


def match_prefix(s: str, candidates):
    for c in candidates:
        if s.startswith(c):
            return c
    return None


def road_lookup(city: str, remainder: str):
    roads = ROAD_TO_DISTRICTS.get(city, {})
    for road in sorted(roads.keys(), key=len, reverse=True):
        if remainder.startswith(road):
            return road, roads[road]
    return None, None


def parse_address(raw_addr: str):
    norm = strip_leading_code(normalize(raw_addr))

    city = match_prefix(norm, CITY_NAMES_DESC)
    if city:
        remainder = norm[len(city):]
        district = match_prefix(remainder, DISTRICTS_BY_CITY.get(city, []))
        if district:
            return CITIES[city], district, "explicit", 1.0

        # 舊制鄉鎮市名稱(縣升格直轄市 / 鄉鎮改制縣轄市後地址未更新)
        district, consumed = try_town_suffix_swap(city, remainder)
        if district:
            return CITIES[city], district, "explicit_legacy_name", 0.95

        road, candidates = road_lookup(city, remainder)
        if candidates:
            if len(candidates) == 1:
                return CITIES[city], next(iter(candidates)), "road_lookup", 0.9
            resolved = disambiguate_by_house_number(city, road, candidates, remainder[len(road):])
            if resolved:
                return CITIES[city], resolved, "road_lookup_scope", 0.85
            return CITIES[city], "|".join(sorted(candidates)), "road_lookup_ambiguous", 0.4
        return CITIES[city], "", "unresolved_no_road_match", 0.0

    # 沒有比對到市/縣開頭 -> 試著直接比對鄉鎮市名稱(如「花蓮市...」缺縣名)
    district = match_prefix(norm, ALL_DISTRICTS_DESC)
    if district:
        owning_cities = DISTRICT_TO_CITIES[district]
        if len(owning_cities) == 1:
            city = next(iter(owning_cities))
            return CITIES[city], district, "implicit_city", 0.8
        return "|".join(sorted(owning_cities)), district, "implicit_city_ambiguous", 0.3

    return "", "", "unresolved", 0.0


def main():
    rows = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        company = d["公司名稱"]
        for r in d["通訊處列表"]:
            addr = r["地址"]
            if addr in MANUAL_OVERRIDES:
                city, district = MANUAL_OVERRIDES[addr]
                method, confidence = "manual_override", 1.0
            else:
                city, district, method, confidence = parse_address(addr)
                # district 來自 normalize() 後的比對結果(統一轉「台」比對),輸出前轉回
                # 內政部官方「臺」字形,才能跟其他官方資料源(如戶政司人口統計)的鄉鎮市區
                # 名稱(如「臺東市」)對得上
                district = district.replace("台", "臺")
            rows.append({
                "公司": company,
                "通訊處名稱": r.get("通訊處名稱", ""),
                "地址": addr,
                "縣市": city,
                "鄉鎮市區": district,
                "設立日期": r.get("設立日期", ""),
                "比對方式": method,
                "信心度": confidence,
            })

    os.makedirs(DATA_DIR, exist_ok=True)
    fieldnames = ["公司", "通訊處名稱", "地址", "縣市", "鄉鎮市區", "設立日期", "比對方式", "信心度"]

    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    review_rows = [r for r in rows if r["信心度"] < 0.8]
    with open(REVIEW_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(review_rows)

    total = len(rows)
    by_method = defaultdict(int)
    for r in rows:
        by_method[r["比對方式"]] += 1

    print(f"總筆數: {total}")
    for method, cnt in sorted(by_method.items(), key=lambda x: -x[1]):
        print(f"  {method}: {cnt} ({cnt/total:.1%})")
    print(f"\n輸出: {OUT_PATH}")
    print(f"待覆核(信心度<0.8): {len(review_rows)} -> {REVIEW_PATH}")


if __name__ == "__main__":
    main()
