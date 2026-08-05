"""
Step 4: 所得/消費能力指標(鄉鎮市區級)。

資料來源:財政部財政資訊中心「綜稅綜合所得總額全國各縣市鄉鎮村里統計分析表」
  https://www.fia.gov.tw/WEB/fia/ias/ias{yyy}/{yyy}_165-9.csv
  (data.gov.tw 資料集 103066 的原始檔案來源;data.gov.tw 頁面目錄只列到 111 年度,
   但 fia.gov.tw 實際上已經有 112 年度可以直接下載,故直接連來源站抓最新一年)

這份檔案本身是「村里」級,但每個鄉鎮市區都已經有政府算好的「合計」列(村里="合計"),
直接拿來用即可,不用自己重新加總 79 萬筆村里資料去算中位數/分位數
——中位數這種統計量本來就不能用村里的中位數再平均,一定要用官方在村里原始樣本上算好的合計列。

另外每個縣市底下還有一列「XX縣市其他」(村里="合計"、鄉鎮市區="XX其他"),是地址無法歸戶到
特定鄉鎮市區的申報戶,不算進 368 個鄉鎮市區,直接濾掉。

金額單位:千元(新臺幣)。

用法:
  python3 scripts/step4_income.py [yyy]
  預設抓最新可用年度(目前 112)。
"""

import csv
import json
import os
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_PATH = os.path.join(BASE_DIR, "data", "reference", "zipcode.json")
CODE_TABLE_PATH = os.path.join(BASE_DIR, "data", "reference", "鄉鎮市區代碼表.csv")
OUT_DIR = os.path.join(BASE_DIR, "data", "income")
OUT_PATH = os.path.join(OUT_DIR, "鄉鎮市區_綜合所得稅統計.csv")

SOURCE_URL_TMPL = "https://www.fia.gov.tw/WEB/fia/ias/ias{yyy}/{yyy}_165-9.csv"
DEFAULT_YYY = "112"

FIELDS = ["納稅單位(戶)", "綜合所得總額", "平均數", "中位數", "第一分位數", "第三分位數", "標準差", "變異係數"]


def load_city_names():
    with open(REF_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return sorted(raw.keys(), key=len, reverse=True)


def split_site_id(site_id, city_names):
    for city in city_names:
        if site_id.startswith(city):
            return city, site_id[len(city):]
    return site_id, ""


def load_code_table():
    with open(CODE_TABLE_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return {(r["縣市"], r["鄉鎮市區"]): r["行政區代碼"] for r in rows}


def main():
    yyy = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_YYY
    url = SOURCE_URL_TMPL.format(yyy=yyy)

    city_names = load_city_names()
    code_table = load_code_table()

    print(f"下載 {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig")
    reader = csv.reader(text.splitlines())
    header = next(reader)
    rows = list(reader)
    print(f"  原始村里筆數: {len(rows)}")

    town_rows = [r for r in rows if r[1] == "合計" and not r[0].endswith("其他")]
    print(f"  鄉鎮市區合計列: {len(town_rows)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    unmatched = []
    with open(OUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["行政區代碼", "縣市", "鄉鎮市區", "統計年度"] + FIELDS)
        for r in town_rows:
            site_id = r[0]
            city, town = split_site_id(site_id, city_names)
            code = code_table.get((city, town))
            if code is None:
                unmatched.append(site_id)
                continue
            w.writerow([code, city, town, yyy] + r[2:2 + len(FIELDS)])

    print(f"\n輸出: {OUT_PATH} ({len(town_rows) - len(unmatched)} 筆)")
    if unmatched:
        print(f"警告: {len(unmatched)} 筆對不到鄉鎮市區代碼表,名稱需人工核對: {unmatched}")

    covered = len(town_rows) - len(unmatched)
    if covered < len(code_table):
        missing = sorted(set(code_table) - {(city, town) for r in town_rows
                                             for city, town in [split_site_id(r[0], city_names)]})
        print(f"提醒: 代碼表裡有 {len(code_table) - covered} 個鄉鎮市區在所得稅資料裡找不到(可能是申報戶數過少被遮蔽),"
              f"依 TODO 註記需退回縣市層級或標記缺值: {missing}")


if __name__ == "__main__":
    main()
