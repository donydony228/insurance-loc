"""
建「年 x 地區」面板表,給相關性試算介面用(見 TODO_correlation_explorer.md)。

一列 = 一個地區的某一年,欄位就是各個指標。同時產出兩個層級:
  data/panel/panel_county.csv   縣市 x 年
  data/panel/panel_town.csv     鄉鎮市區 x 年

檔名用 ASCII 是因為這兩份要放上 GitHub Pages 給前端 fetch,中文檔名在 URL 裡要 percent-encode,
沒必要自找麻煩(欄位名維持中文)。

資料來源(都是 step1~step4 用過的同一批,不另找):
  戶政司 ODRP048/{yyy}  各鄉鎮市區人口密度 → 年底人口、土地面積、人口密度   民國 109-114
  戶政司 ODRP055/{yyy}  嬰兒出生數(按登記)  → 出生數                        民國 109-113
  戶政司 ODRP031/{yyy}  人口死亡數          → 死亡數                        民國 109-113
  戶政司 ODRP019/{yyy}  戶數、人口數按戶別   → 戶數                          民國 109-113
  財政部 {yyy}_165-9.csv 綜稅所得統計        → 納稅單位、所得總額、平均/中位數 民國 109-112
  data/income/縣市_平均消費支出.csv           → 平均每戶消費支出(縣市)        民國 87-113
  data/通訊處/鄉鎮市區_通訊處_歷年設立數.csv   → 新增/累計通訊處數              不限

年份範圍取 87-113:戶政司 open data 只保留約 5 年滾動窗口(實測 105 以前一律「查無資料」),
消費支出可回溯到 87。缺值一律留空,不填 0 —— 試算介面那端會成對排除,並顯示實際 n。

彙總到縣市層級時,計數型欄位直接加總,比率型欄位一律用縣市層級的分子/分母重算,
不是把鄉鎮市區的率平均起來(平均「率」會變成沒有意義的數字)。
中位數更是完全不能從鄉鎮市區合併,財政部原始檔也沒有縣市合計列,所以縣市層級的
綜合所得中位數留空,只給重算得出來的平均數 —— 這是來源限制,不是漏做。

用法:
  python3 scripts/build_panel.py
API 回應會快取在 data/panel/raw/,重跑不會重新下載(要重抓就把 raw 砍掉)。
"""

import csv
import json
import os
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_TABLE_PATH = os.path.join(BASE_DIR, "data", "reference", "鄉鎮市區代碼表.csv")
OFFICE_YEARLY_PATH = os.path.join(BASE_DIR, "data", "通訊處", "鄉鎮市區_通訊處_歷年設立數.csv")
CONSUMPTION_PATH = os.path.join(BASE_DIR, "data", "income", "縣市_平均消費支出.csv")
OUT_DIR = os.path.join(BASE_DIR, "data", "panel")
RAW_DIR = os.path.join(OUT_DIR, "raw")
OUT_COUNTY = os.path.join(OUT_DIR, "panel_county.csv")
OUT_TOWN = os.path.join(OUT_DIR, "panel_town.csv")

API_BASE = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore"
FIA_URL_TMPL = "https://www.fia.gov.tw/WEB/fia/ias/ias{yyy}/{yyy}_165-9.csv"

# 戶政司從民國 113 年度起把部分資料集的欄位名從英文改成中文(例:ODRP055 的 site_id →
# 區域別、birth_count → 嬰兒出生數),同一支 API 不同年度 schema 不一致。在 fetch_ris
# 進來的地方統一轉回英文,下游全部只認一套名字,不用每個 collect_* 各判斷一次。
FIELD_ALIASES = {
    "區域別": "site_id",
    "統計年度": "statistic_yyy",
    "嬰兒出生數": "birth_count",
    "人口數": "people_total",
    "土地面積": "area",
    "人口密度": "population_density",
    "總計_計": "death_total",   # ODRP031 113 年度的死亡總數欄位
    "共同生活戶_戶數": "household_ordinary_total",
    "共同事業戶_戶數": "household_business_total",
    "單獨生活戶_戶數": "household_single_total",
    "統計年": "statistic_yyy",
    "區域別代碼": "district_code",
    "村里名稱": "village",
}

PANEL_YEARS = range(87, 114)          # 民國,面板的列涵蓋範圍
RIS_YEARS = range(109, 114)           # 戶政司實際有資料的年份
FIA_YEARS = range(109, 113)           # 財政部實際有資料的年份(113 尚未發布)

# 計數型欄位:縣市層級直接加總。比率型欄位不列在這裡,一律重算(見 derive_rates)。
COUNT_FIELDS = [
    "新增通訊處數", "累計通訊處數",
    "年底人口", "土地面積_km2",
    "出生數", "死亡數", "戶數",
    "納稅單位_戶", "綜合所得總額_千元",
]
# 縣市層級無法從鄉鎮市區合併、且來源沒有縣市合計列的欄位
COUNTY_UNAVAILABLE = ["綜合所得中位數_千元"]

COLUMNS = COUNT_FIELDS + [
    "人口密度_人每km2", "平均戶量",
    "粗出生率_千分比", "粗死亡率_千分比", "自然增加率_千分比",
    "綜合所得平均數_千元", "綜合所得中位數_千元",
    "平均每戶消費支出_元",
    "每萬人通訊處數",
]


# ---------------------------------------------------------------- 下載 / 快取

def fetch_json(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def unify_fields(rows):
    return [{FIELD_ALIASES.get(k, k): v for k, v in r.items()} for r in rows]


def fetch_ris(code, yyy):
    """抓戶政司某資料集某年度的全部分頁,結果快取到 raw/。回傳 responseData list。"""
    cache = os.path.join(RAW_DIR, f"{code}_{yyy}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return unify_fields(json.load(f))

    rows = []
    page = 1
    while True:
        data = fetch_json(f"{API_BASE}/{code}/{yyy}?PAGE={page}")
        if data.get("responseCode") != "OD-0101-S":
            break
        rows.extend(data["responseData"])
        total_page = int(data.get("totalPage", "1") or "1")
        print(f"  {code} {yyy} 第 {page}/{total_page} 頁 ({len(rows)} 筆)")
        if page >= total_page:
            break
        page += 1

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)  # 原樣落地,正規化只在讀出來時做
    return unify_fields(rows)


def fetch_fia(yyy):
    cache = os.path.join(RAW_DIR, f"fia_{yyy}.csv")
    if not os.path.exists(cache):
        url = FIA_URL_TMPL.format(yyy=yyy)
        print(f"  下載 {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        with open(cache, "wb") as f:
            f.write(raw)
    with open(cache, encoding="utf-8-sig") as f:
        return list(csv.reader(f))


# ---------------------------------------------------------------- 共用工具

def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_site_index():
    """site_id(縣市+鄉鎮市區 連在一起)→ (縣市, 鄉鎮市區)。

    戶政司和財政部都用這種黏在一起的字串,沒有分隔符,所以用官方代碼表建精確對照,
    對不上的(如高雄市東沙群島/南沙群島,代碼表沒有)直接丟掉並計數。
    """
    index = {}
    for r in read_csv(CODE_TABLE_PATH):
        index[r["縣市"] + r["鄉鎮市區"]] = (r["縣市"], r["鄉鎮市區"])
    return index


def normalize(site_id):
    # 代碼表統一用「臺」,來源偶有「台」,對齊 step0_geocode.py 已建立的慣例
    return site_id.strip().replace("台", "臺")


def to_int(v):
    v = (v or "").strip().replace(",", "")
    if v in ("", "…", "-", "－"):
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def to_float(v):
    v = (v or "").strip().replace(",", "")
    if v in ("", "…", "-", "－"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def div(numerator, denominator, scale=1, decimals=2):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator * scale, decimals)


# ---------------------------------------------------------------- 各來源 → 鄉鎮市區

def collect_offices(cells, keys):
    """新增/累計通訊處數。只取 公司='全部' 的列,各公司列一起加會變兩倍。"""
    for r in read_csv(OFFICE_YEARLY_PATH):
        if r["公司"] != "全部":
            continue
        yyy = int(r["年"]) - 1911
        if yyy not in PANEL_YEARS:
            continue
        key = (yyy, r["縣市"], r["鄉鎮市區"])
        keys.add((r["縣市"], r["鄉鎮市區"]))
        cell = cells.setdefault(key, {})
        cell["新增通訊處數"] = int(r["新增數"])
        cell["累計通訊處數"] = int(r["累計總數"])


def collect_population(cells, site_index, unmatched):
    """ODRP048:年底人口、土地面積、人口密度。"""
    for yyy in RIS_YEARS:
        for r in fetch_ris("ODRP048", str(yyy)):
            site = site_index.get(normalize(r["site_id"]))
            if site is None:
                unmatched.add(r["site_id"])
                continue
            cell = cells.setdefault((yyy, *site), {})
            cell["年底人口"] = to_int(r["people_total"])
            cell["土地面積_km2"] = to_float(r["area"])


def collect_births(cells, site_index, unmatched):
    """ODRP055:逐列 birth_count 加總成該鄉鎮市區的年出生數。"""
    for yyy in RIS_YEARS:
        agg = {}
        for r in fetch_ris("ODRP055", str(yyy)):
            site = site_index.get(normalize(r["site_id"]))
            if site is None:
                unmatched.add(r["site_id"])
                continue
            agg[site] = agg.get(site, 0) + (to_int(r["birth_count"]) or 0)
        for site, total in agg.items():
            cells.setdefault((yyy, *site), {})["出生數"] = total


def collect_deaths(cells, site_index, unmatched):
    """ODRP031:已有 death_total 欄位,不用自己從單一年齡加。"""
    for yyy in RIS_YEARS:
        for r in fetch_ris("ODRP031", str(yyy)):
            site = site_index.get(normalize(r["site_id"]))
            if site is None:
                unmatched.add(r["site_id"])
                continue
            cells.setdefault((yyy, *site), {})["死亡數"] = to_int(r["death_total"])


def collect_households(cells, site_index, unmatched):
    """ODRP019:村里級,戶數 = 共同生活戶 + 共同事業戶 + 單獨生活戶,彙總到鄉鎮市區。"""
    for yyy in RIS_YEARS:
        agg = {}
        for r in fetch_ris("ODRP019", str(yyy)):
            site = site_index.get(normalize(r["site_id"]))
            if site is None:
                unmatched.add(r["site_id"])
                continue
            total = sum(to_int(r[k]) or 0 for k in
                        ("household_ordinary_total", "household_business_total", "household_single_total"))
            agg[site] = agg.get(site, 0) + total
        for site, total in agg.items():
            cells.setdefault((yyy, *site), {})["戶數"] = total


def collect_income(cells, site_index, unmatched):
    """財政部 165-9:村里級檔案,取政府算好的「村里=合計」列當鄉鎮市區值。

    中位數不能自己從村里重算(統計量無法合併),一定要用官方合計列 —— 這點跟
    step4_income.py 的處理一致。縣市底下的「XX其他」列(地址無法歸戶者)不屬於
    任何鄉鎮市區,這裡跳過;但縣市層級加總時會被算進去(見 aggregate_county)。
    """
    for yyy in FIA_YEARS:
        rows = fetch_fia(str(yyy))[1:]
        for r in rows:
            if r[1] != "合計":
                continue
            site_id = normalize(r[0])
            if site_id.endswith("其他"):
                continue
            site = site_index.get(site_id)
            if site is None:
                unmatched.add(r[0])
                continue
            cell = cells.setdefault((yyy, *site), {})
            cell["納稅單位_戶"] = to_int(r[2])
            cell["綜合所得總額_千元"] = to_int(r[3])
            cell["綜合所得平均數_千元"] = to_float(r[4])
            cell["綜合所得中位數_千元"] = to_float(r[5])


def load_consumption():
    """縣市_平均消費支出.csv 是寬表(一列一年、一欄一縣市),melt 成 (yyy, 縣市) -> 值。"""
    out = {}
    for r in read_csv(CONSUMPTION_PATH):
        yyy = int(r["年度"]) - 1911
        for city, value in r.items():
            if city in ("年度", "臺灣地區"):
                continue
            v = to_int(value)
            if v is not None:
                out[(yyy, normalize(city))] = v
    return out


# ---------------------------------------------------------------- 衍生 / 彙總

def derive_rates(cell):
    """比率型欄位一律從該層級自己的分子分母算,呼叫端在鄉鎮市區和縣市各算一次。"""
    pop = cell.get("年底人口")
    cell["人口密度_人每km2"] = div(pop, cell.get("土地面積_km2"), decimals=1)
    cell["平均戶量"] = div(pop, cell.get("戶數"), decimals=3)
    cell["粗出生率_千分比"] = div(cell.get("出生數"), pop, 1000)
    cell["粗死亡率_千分比"] = div(cell.get("死亡數"), pop, 1000)
    births, deaths = cell.get("出生數"), cell.get("死亡數")
    cell["自然增加率_千分比"] = (
        div(births - deaths, pop, 1000) if births is not None and deaths is not None else None
    )
    cell["每萬人通訊處數"] = div(cell.get("累計通訊處數"), pop, 10000)


def aggregate_county(town_cells, fia_other):
    """鄉鎮市區 → 縣市。計數型加總,比率型之後重算。

    綜合所得的縣市加總會把「XX其他」列(地址無法歸戶的申報戶)加回去,不然縣市總額
    會少一塊;鄉鎮市區層級則沒有這些戶的歸屬,只能不算。

    重點:只要該縣市底下有任何一個鄉鎮市區在某欄缺值,整個縣市的那一欄就留空。
    來源真的會缺(例:ODRP031 民國 113 年整份少了新北市板橋區),照常加總的話新北市
    死亡數會少四千多人,但數字看起來完全正常 —— 這種靜靜的少一塊比留空危險得多。
    """
    county_cells = {}
    town_total = {}   # (年, 縣市) -> 底下鄉鎮市區數
    filled = {}       # (年, 縣市, 欄位) -> 該欄有值的鄉鎮市區數
    for (yyy, city, _town), cell in town_cells.items():
        target = county_cells.setdefault((yyy, city), {})
        town_total[(yyy, city)] = town_total.get((yyy, city), 0) + 1
        for field in COUNT_FIELDS:
            v = cell.get(field)
            if v is not None:
                target[field] = (target.get(field) or 0) + v
                filled[(yyy, city, field)] = filled.get((yyy, city, field), 0) + 1

    gaps = []
    for (yyy, city), target in county_cells.items():
        for field in COUNT_FIELDS:
            if target.get(field) is None:
                continue
            have = filled.get((yyy, city, field), 0)
            if have < town_total[(yyy, city)]:
                target[field] = None
                gaps.append((yyy, city, field, have, town_total[(yyy, city)]))

    for (yyy, city), extra in fia_other.items():
        target = county_cells.get((yyy, city))
        if target is None:
            continue
        for field in ("納稅單位_戶", "綜合所得總額_千元"):
            if extra.get(field) is not None and target.get(field) is not None:
                target[field] += extra[field]

    for cell in county_cells.values():
        # 縣市平均所得用加總後的總額/戶數重算,不是把鄉鎮市區的平均數平均
        cell["綜合所得平均數_千元"] = div(cell.get("綜合所得總額_千元"), cell.get("納稅單位_戶"), decimals=1)
        for field in COUNTY_UNAVAILABLE:
            cell[field] = None
        cell["土地面積_km2"] = round(cell["土地面積_km2"], 4) if cell.get("土地面積_km2") else None
    return county_cells, gaps


def collect_fia_other(site_index):
    """各縣市的「XX其他」列,縣市層級加總時要補回去。"""
    cities = {city for city, _ in site_index.values()}
    out = {}
    for yyy in FIA_YEARS:
        for r in fetch_fia(str(yyy))[1:]:
            if r[1] != "合計":
                continue
            name = normalize(r[0])
            if not name.endswith("其他"):
                continue
            city = name[:-2]
            if city in cities:
                out[(yyy, city)] = {"納稅單位_戶": to_int(r[2]), "綜合所得總額_千元": to_int(r[3])}
    return out


# ---------------------------------------------------------------- 輸出

def write_panel(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"輸出: {path} ({len(rows)} 列)")


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    site_index = load_site_index()
    unmatched = set()

    town_cells = {}
    keys = set()

    print("讀取通訊處歷年設立數...")
    collect_offices(town_cells, keys)
    print("下載 ODRP048 年底人口/面積...")
    collect_population(town_cells, site_index, unmatched)
    print("下載 ODRP055 出生數...")
    collect_births(town_cells, site_index, unmatched)
    print("下載 ODRP031 死亡數...")
    collect_deaths(town_cells, site_index, unmatched)
    print("下載 ODRP019 戶數...")
    collect_households(town_cells, site_index, unmatched)
    print("下載財政部綜稅所得統計...")
    collect_income(town_cells, site_index, unmatched)
    fia_other = collect_fia_other(site_index)
    consumption = load_consumption()

    # 補齊所有 (年, 地區) 組合。通訊處資料只涵蓋有據點的地區,沒據點的縣市(如連江縣)
    # 新增/累計都是真實的 0,不是缺值,要補 0 而不是留空。
    all_sites = set(site_index.values())
    for yyy in PANEL_YEARS:
        for city, town in all_sites:
            cell = town_cells.setdefault((yyy, city, town), {})
            cell.setdefault("新增通訊處數", 0)
            cell.setdefault("累計通訊處數", 0)

    county_cells, gaps = aggregate_county(town_cells, fia_other)

    for (yyy, city, _town), cell in town_cells.items():
        derive_rates(cell)
        cell["平均每戶消費支出_元"] = consumption.get((yyy, city))  # 縣市退回值
    for (yyy, city), cell in county_cells.items():
        derive_rates(cell)
        cell["平均每戶消費支出_元"] = consumption.get((yyy, city))

    def fmt(cell):
        return ["" if cell.get(c) is None else cell[c] for c in COLUMNS]

    write_panel(
        OUT_COUNTY, ["年", "縣市"] + COLUMNS,
        [[yyy, city] + fmt(cell) for (yyy, city), cell in sorted(county_cells.items())],
    )
    write_panel(
        OUT_TOWN, ["年", "縣市", "鄉鎮市區"] + COLUMNS,
        [[yyy, city, town] + fmt(cell) for (yyy, city, town), cell in sorted(town_cells.items())],
    )

    if unmatched:
        print(f"提醒: {len(unmatched)} 個 site_id 不在鄉鎮市區代碼表裡,已略過: {sorted(unmatched)}")
    if gaps:
        print(f"\n提醒: {len(gaps)} 個縣市欄位因底下有鄉鎮市區缺值而留空(不是錯誤,是來源就缺):")
        for yyy, city, field, have, total in sorted(gaps):
            print(f"  {yyy} {city} {field}: 只有 {have}/{total} 個鄉鎮市區有值")


if __name__ == "__main__":
    main()
