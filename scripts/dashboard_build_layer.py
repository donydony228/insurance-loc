"""
把目前 TODO_population_data.md 已完成的資料(通訊處地址、人口統計、年齡結構、成長遷徙、所得/消費、
客戶保費實績)合併成儀表板要用的鄉鎮市區 choropleth 圖層,輸出一份可以直接內嵌進 dashboard.html 的
JSON。之後每多完成一個 Phase,照同樣模式加進 props 即可,不用動 dashboard.html 的畫圖邏輯。

輸入:
  data/reference/twtown2010.json                          全台鄉鎮市區行政區界
  data/通訊處/通訊處_行政區.csv                             通訊處 x 縣市/鄉鎮市區
  data/population/鄉鎮市區_人口統計.csv                     人口/戶數/密度
  data/population/age_structure/鄉鎮市區_年齡結構.csv        年齡結構
  data/population/growth_migration/鄉鎮市區_成長遷徙指標.csv  成長率/遷徙率
  data/income/鄉鎮市區_綜合所得稅統計.csv                    綜合所得(鄉鎮市區級,368 筆全有)
  data/income/鄉鎮市區_平均消費支出_縣市退回值.csv            平均消費支出(縣市退回值,金門/連江缺)
  data/customer/merged_clean.csv                           客戶保費實績(clean_customer_data.py 產出,
                                                             已帶 POSTCODE 反推的 縣市/鄉鎮市區)

輸出:
  data/通訊處/district_layer.json   GeoJSON FeatureCollection,每個 feature 帶齊上述指標
                                    (合併好的資料,dashboard.html 直接讀這份就好)
"""

import csv
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEO_PATH = os.path.join(BASE_DIR, "data", "reference", "twtown2010.json")
OFFICE_CSV = os.path.join(BASE_DIR, "data", "通訊處", "通訊處_行政區.csv")
POP_CSV = os.path.join(BASE_DIR, "data", "population", "鄉鎮市區_人口統計.csv")
AGE_CSV = os.path.join(BASE_DIR, "data", "population", "age_structure", "鄉鎮市區_年齡結構.csv")
GROWTH_CSV = os.path.join(BASE_DIR, "data", "population", "growth_migration", "鄉鎮市區_成長遷徙指標.csv")
INCOME_CSV = os.path.join(BASE_DIR, "data", "income", "鄉鎮市區_綜合所得稅統計.csv")
CONSUMPTION_CSV = os.path.join(BASE_DIR, "data", "income", "鄉鎮市區_平均消費支出_縣市退回值.csv")
CUSTOMER_CSV = os.path.join(BASE_DIR, "data", "customer", "merged_clean.csv")
OUT_PATH = os.path.join(BASE_DIR, "data", "通訊處", "district_layer.json")

# 保戶數用 性別+年齡+3碼郵遞區號 當去重近似值(資料沒有真實客戶 ID);同一鄉鎮市區內
# 用 amount_NTD 總額最高的通路當「主力通路」——量級型欄位(保戶數/總保費/客單價)適合
# choropleth 連續色階,主力通路是類別欄位,dashboard 那邊要用固定色而非漸層。
CHANNEL_LABELS = {"AG": "業務員(AG)", "BR": "銀行保代(BR)", "BA": "銀保(BA)", "EC": "電商(EC)", "FS": "FS"}

# 2010 年至今的行政區劃調整,geojson 仍是舊名,轉成現名才能跟人口資料 join
TAOYUAN_OLD_TOWNS = {
    "中壢市", "平鎮市", "八德市", "楊梅市", "桃園市",
    "蘆竹鄉", "大溪鎮", "大園鄉", "龜山鄉", "龍潭鄉", "新屋鄉", "觀音鄉", "復興鄉",
}
RENAME_TOWN = {
    ("彰化縣", "員林鎮"): "員林市",
    ("苗栗縣", "頭份鎮"): "頭份市",
}


def normalize_feature_key(county: str, town: str):
    if town.endswith("(海)"):
        return None  # 海域重複面,丟棄,保留同名的本體面即可
    if county == "桃園縣" and town in TAOYUAN_OLD_TOWNS:
        new_town = ("桃園區" if town == "桃園市" else town[:-1] + "區")
        return ("桃園市", new_town)
    if (county, town) in RENAME_TOWN:
        return (county, RENAME_TOWN[(county, town)])
    return (county, town)


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _aggregate_customer_rows(rows):
    """一組列 -> (縣市,鄉鎮市區) 保戶數/總保費收入/客單價/主力通路,共用邏輯給「全部」和
    「排除投資型」兩種口徑各跑一次。"""
    persons = {}  # key -> set of (LIFESEX, LIFEAGE, POSTCODE) 近似客戶 ID
    revenue = {}  # key -> 總保費收入(含負值,淨額)
    channel_revenue = {}  # key -> {channel: 保費收入}

    for r in rows:
        key = (r["縣市"], r["鄉鎮市區"])
        amount = float(r["amount_NTD"] or 0)
        person_id = (r["LIFESEX"], r["LIFEAGE"], r["POSTCODE"])

        persons.setdefault(key, set()).add(person_id)
        revenue[key] = revenue.get(key, 0.0) + amount
        cr = channel_revenue.setdefault(key, {})
        cr[r["channel"]] = cr.get(r["channel"], 0.0) + amount

    stats = {}
    for key, person_set in persons.items():
        total_revenue = revenue[key]
        customer_count = len(person_set)
        dominant_channel, dominant_amount = max(channel_revenue[key].items(), key=lambda kv: kv[1])
        stats[key] = {
            "保戶數": customer_count,
            "總保費收入": round(total_revenue, 2),
            "客單價": round(total_revenue / customer_count, 2) if customer_count else None,
            "主力通路": dominant_channel,
            "主力通路占比": round(dominant_amount / total_revenue, 4) if total_revenue else None,
            "通路別保費": {CHANNEL_LABELS.get(c, c): round(v, 2) for c, v in channel_revenue[key].items()},
        }
    return stats


def build_customer_stats():
    """(縣市,鄉鎮市區) -> 客戶實績,「全部商品」和「排除投資型(ptype3 開頭 U，即變額壽險)」
    兩種口徑各算一次,存成 全部/不含投資型 兩份 dict——投資型保單金額波動大(中位數只有
    209 元但平均 32.6 萬,標準差是所有商品類型裡最大的),混在一起會拉歪客單價這類平均值指標,
    dashboard 那邊用一個 checkbox 切換要看哪個口徑。"""
    rows = [r for r in read_csv(CUSTOMER_CSV) if r["郵遞區號可定位"] == "是"]
    rows_excl_investment = [r for r in rows if not r["ptype3"].startswith("U")]
    return _aggregate_customer_rows(rows), _aggregate_customer_rows(rows_excl_investment)


def main():
    with open(GEO_PATH, encoding="utf-8") as f:
        geo = json.load(f)

    offices = read_csv(OFFICE_CSV)
    pop = read_csv(POP_CSV)
    age = read_csv(AGE_CSV)
    growth = read_csv(GROWTH_CSV)
    income = read_csv(INCOME_CSV)
    consumption = read_csv(CONSUMPTION_CSV)
    customer_stats, customer_stats_excl_investment = build_customer_stats()

    office_counts = {}  # (縣市,鄉鎮市區) -> {'total': n, 'by_company': {...}}
    for r in offices:
        key = (r["縣市"], r["鄉鎮市區"])
        if "|" in r["鄉鎮市區"] or not r["鄉鎮市區"]:
            continue  # 極少數仍模糊的資料,choropleth 不用它
        entry = office_counts.setdefault(key, {"total": 0, "by_company": {}})
        entry["total"] += 1
        entry["by_company"][r["公司"]] = entry["by_company"].get(r["公司"], 0) + 1

    pop_by_key = {(r["縣市"], r["鄉鎮市區"]): r for r in pop}
    age_by_key = {(r["縣市"], r["鄉鎮市區"]): r for r in age}
    growth_by_key = {(r["縣市"], r["鄉鎮市區"]): r for r in growth}
    income_by_key = {(r["縣市"], r["鄉鎮市區"]): r for r in income}
    consumption_by_key = {(r["縣市"], r["鄉鎮市區"]): r for r in consumption}

    features_out = []
    seen_keys = set()
    dropped = []

    for feat in geo["features"]:
        p = feat["properties"]
        county, town = p.get("county"), p.get("town")
        key = normalize_feature_key(county, town)
        if key is None:
            continue
        if key in seen_keys:
            continue  # 同名重複面(海域面已濾掉,這裡防其他重複)

        pr = pop_by_key.get(key)
        ar = age_by_key.get(key)
        gr = growth_by_key.get(key)
        ir = income_by_key.get(key)
        cr = consumption_by_key.get(key)
        if not pr or not ar:
            dropped.append(key)
            continue
        seen_keys.add(key)

        oc = office_counts.get(key, {"total": 0, "by_company": {}})
        population = int(pr["人口數"])
        office_per_10k = round(oc["total"] / population * 10000, 3) if population else None

        cs = customer_stats.get(key)
        cs_excl = customer_stats_excl_investment.get(key)

        def customer_props(stats_entry, suffix):
            count = stats_entry["保戶數"] if stats_entry else 0
            return {
                "保戶數" + suffix: count,
                "每萬人保戶數" + suffix: round(count / population * 10000, 3) if population else None,
                "保戶滲透率" + suffix: round(count / population * 100, 3) if population else None,
                "總保費收入" + suffix: stats_entry["總保費收入"] if stats_entry else None,
                "客單價" + suffix: stats_entry["客單價"] if stats_entry else None,
                "主力通路" + suffix: stats_entry["主力通路"] if stats_entry else None,
                "主力通路占比" + suffix: stats_entry["主力通路占比"] if stats_entry else None,
                "通路別保費" + suffix: stats_entry["通路別保費"] if stats_entry else {},
            }

        props = {
            "縣市": key[0],
            "鄉鎮市區": key[1],
            "人口數": population,
            "戶數": int(pr["戶數"]),
            "平均戶量": float(pr["平均戶量"]),
            "土地面積_km2": float(pr["土地面積_km2"]),
            "人口密度": float(pr["人口密度_人每km2"]),
            "65歲以上占比": float(ar["65歲以上占比"]),
            "40_64歲占比": float(ar["40_64歲占比"]),
            "老化指數": float(ar["老化指數"]) if ar["老化指數"] not in ("", None) else None,
            "扶養比": float(ar["扶養比"]) if ar["扶養比"] not in ("", None) else None,
            "通訊處數": oc["total"],
            "通訊處數_by公司": oc["by_company"],
            "每萬人通訊處數": office_per_10k,
            "近5年人口成長率": float(gr["近5年人口成長率_百分比"]) if gr else None,
            "淨遷入率": float(gr["淨遷入率_千分比"]) if gr else None,
            "自然增加率": float(gr["自然增加率_千分比"]) if gr else None,
            "所得中位數": float(ir["中位數"]) if ir else None,
            "所得平均數": float(ir["平均數"]) if ir else None,
            "納稅單位數": int(ir["納稅單位(戶)"]) if ir else None,
            "平均消費支出": float(cr["平均每戶消費支出_元"]) if cr and cr["平均每戶消費支出_元"] else None,
            "消費支出資料層級": cr["資料層級"] if cr and cr["平均每戶消費支出_元"] else None,
            **customer_props(cs, ""),
            **customer_props(cs_excl, "_不含投資型"),
        }
        features_out.append({
            "type": "Feature",
            "properties": props,
            "geometry": feat["geometry"],
        })

    print(f"輸出 {len(features_out)} 個鄉鎮市區 feature")
    if dropped:
        print(f"缺人口/年齡資料被跳過的 geo feature ({len(dropped)}): {dropped}")

    missing_geo = (set(pop_by_key.keys()) - seen_keys)
    if missing_geo:
        print(f"人口表裡有、但 geojson 找不到對應面的鄉鎮市區 ({len(missing_geo)}): {sorted(missing_geo)}")

    missing_income = seen_keys - set(income_by_key.keys())
    if missing_income:
        print(f"缺所得稅資料的鄉鎮市區 ({len(missing_income)}): {sorted(missing_income)}")
    missing_consumption = [k for k in seen_keys if not (consumption_by_key.get(k, {}).get("平均每戶消費支出_元"))]
    if missing_consumption:
        print(f"缺消費支出資料的鄉鎮市區(金門/連江家庭收支調查未涵蓋,預期內) ({len(missing_consumption)}): {sorted(missing_consumption)}")
    missing_customer = seen_keys - set(customer_stats.keys())
    if missing_customer:
        print(f"這個月客戶保費資料裡沒有紀錄的鄉鎮市區 ({len(missing_customer)}): {sorted(missing_customer)}")

    out = {"type": "FeatureCollection", "features": features_out}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"-> {OUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
