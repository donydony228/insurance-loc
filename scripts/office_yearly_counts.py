"""
用通訊處設立日期算兩個衍生時間序列(給之後跟其他連續變數算相關性用):
  - 新增數:該鄉鎮市區當年新設立的通訊處數
  - 累計總數:截至該年底,該鄉鎮市區累計設立的通訊處總數

輸入:
  data/通訊處/通訊處_行政區.csv

輸出:
  data/通訊處/鄉鎮市區_通訊處_歷年設立數.csv
  欄位:縣市,鄉鎮市區,公司,年,新增數,累計總數
  公司="全部" 為該鄉鎮市區所有公司合計,另外每家公司各一列。
  每個鄉鎮市區補齊資料涵蓋年份範圍內的每一年(沒有新增的年份新增數為0),
  方便之後任選某一年份跟其他連續變數 join 算相關性。
"""

import csv
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICE_CSV = os.path.join(BASE_DIR, "data", "通訊處", "通訊處_行政區.csv")
OUT_PATH = os.path.join(BASE_DIR, "data", "通訊處", "鄉鎮市區_通訊處_歷年設立數.csv")
ALL_COMPANIES = "全部"


def main():
    with open(OFFICE_CSV, encoding="utf-8-sig") as f:
        offices = list(csv.DictReader(f))

    new_counts = {}  # (縣市,鄉鎮市區,公司,年) -> 新增數
    companies_by_key = {}  # (縣市,鄉鎮市區) -> set of 公司
    years = set()
    for r in offices:
        town = r["鄉鎮市區"]
        if "|" in town or not town:
            continue  # 跟 dashboard_build_layer.py 一致,模糊資料不用
        key = (r["縣市"], town)
        company = r["公司"]
        year = int(r["設立日期"][:4])
        companies_by_key.setdefault(key, set()).add(company)
        years.add(year)
        new_counts[(*key, ALL_COMPANIES, year)] = new_counts.get((*key, ALL_COMPANIES, year), 0) + 1
        new_counts[(*key, company, year)] = new_counts.get((*key, company, year), 0) + 1

    year_range = range(min(years), max(years) + 1)

    with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["縣市", "鄉鎮市區", "公司", "年", "新增數", "累計總數"])
        for county, town in sorted(companies_by_key):
            companies = [ALL_COMPANIES] + sorted(companies_by_key[(county, town)])
            for company in companies:
                cumulative = 0
                for year in year_range:
                    new = new_counts.get((county, town, company, year), 0)
                    cumulative += new
                    w.writerow([county, town, company, year, new, cumulative])

    print(f"寫出 {OUT_PATH}")


if __name__ == "__main__":
    main()
