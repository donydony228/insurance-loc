"""
面板表的檢查。build_panel.py 跑完後執行:

  python3 scripts/test_panel.py

檢查兩件會安靜壞掉的事:
  1. 全國加總對不對得上內政部公布值(抓得到但對不上 = 彙總邏輯或欄位對應錯了)
  2. 縣市層級 vs 鄉鎮市區層級加總一不一致(不一致 = aggregate_county 有問題)
"""

import csv
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTY = os.path.join(BASE_DIR, "data", "panel", "panel_county.csv")
TOWN = os.path.join(BASE_DIR, "data", "panel", "panel_town.csv")

# 內政部戶政司公布的全國年度數字(按登記),用來確認彙總沒有多算/少算
OFFICIAL_BIRTHS = {109: 165249, 110: 153820, 111: 138986, 112: 135571, 113: 134856}
OFFICIAL_YEAR_END_POP = {109: 23561236, 113: 23400220}


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def total(rows, year, field):
    """該年度全國合計。缺值當 0 —— 呼叫端只在確認該年度無缺值的欄位上用。"""
    return sum(float(r[field]) for r in rows if int(r["年"]) == year and r[field] != "")


def main():
    county, town = read(COUNTY), read(TOWN)

    assert len(county) == 594, f"縣市面板應為 22 縣市 x 27 年 = 594 列,實際 {len(county)}"
    assert len(town) == 9936, f"鄉鎮市區面板應為 368 區 x 27 年 = 9936 列,實際 {len(town)}"
    assert len({r["縣市"] for r in county}) == 22
    assert len({(r["縣市"], r["鄉鎮市區"]) for r in town}) == 368

    for year, expected in OFFICIAL_BIRTHS.items():
        got = total(county, year, "出生數")
        assert got == expected, f"{year} 年全國出生數 {got:,.0f} != 官方 {expected:,}"

    for year, expected in OFFICIAL_YEAR_END_POP.items():
        got = total(county, year, "年底人口")
        assert got == expected, f"{year} 年底全國人口 {got:,.0f} != 官方 {expected:,}"

    # 縣市加總必須等於鄉鎮市區加總。所得欄位除外:縣市層級另外加了「XX其他」
    # (地址無法歸戶的申報戶),本來就會比鄉鎮市區加總多,是預期行為。
    for field in ["出生數", "年底人口", "戶數", "新增通訊處數", "累計通訊處數"]:
        for year in range(109, 114):
            c, t = total(county, year, field), total(town, year, field)
            assert abs(c - t) < 0.5, f"{year} {field}: 縣市 {c:,.0f} != 鄉鎮市區 {t:,.0f}"

    # 死亡數 113 年新北市因來源缺板橋區而留空,縣市層級加總會少掉整個新北市,
    # 這是刻意的(見 aggregate_county),所以只驗有值的年份。
    for year in [109, 110, 111, 112]:
        c, t = total(county, year, "死亡數"), total(town, year, "死亡數")
        assert abs(c - t) < 0.5, f"{year} 死亡數: 縣市 {c:,.0f} != 鄉鎮市區 {t:,.0f}"
    blank = [r for r in county if r["縣市"] == "新北市" and int(r["年"]) == 113]
    assert blank[0]["死亡數"] == "", "113 新北市死亡數應留空(來源缺板橋區),不應給加總值"

    # 年齡結構(ODRP014)跟年底人口(ODRP048)是兩支不同的資料集,三段年齡合計
    # 必須等於年底人口 —— 對不上就是年齡分段漏了某些歲數,或彙總層級錯了。
    for year in range(109, 114):
        segments = sum(total(county, year, f) for f in ["0_14歲人口", "15_64歲人口", "65歲以上人口"])
        pop = total(county, year, "年底人口")
        assert segments == pop, f"{year} 年齡三段合計 {segments:,.0f} != 年底人口 {pop:,.0f}"

    # 40-64 歲是 15-64 歲的子集,不是獨立一段(加進總數會重複計算)
    for r in town:
        if r["40_64歲人口"] and r["15_64歲人口"]:
            assert float(r["40_64歲人口"]) <= float(r["15_64歲人口"]), \
                f'{r["年"]} {r["縣市"]}{r["鄉鎮市區"]}:40-64 歲人口大於 15-64 歲'

    # 占比要用年齡資料自己的三段合計當分母,不能混用 年底人口
    for r in town:
        if not r["65歲以上占比"]:
            continue
        age_total = sum(float(r[f]) for f in ["0_14歲人口", "15_64歲人口", "65歲以上人口"])
        expected = round(float(r["65歲以上人口"]) / age_total * 100, 2)
        assert abs(float(r["65歲以上占比"]) - expected) < 0.02, \
            f'{r["年"]} {r["縣市"]}{r["鄉鎮市區"]}:65 歲以上占比對不上重算值'

    # 沒有據點的縣市,通訊處數是真實的 0 而不是缺值
    matsu = [r for r in county if r["縣市"] == "連江縣"]
    assert all(r["累計通訊處數"] == "0" for r in matsu), "連江縣沒有通訊處,應為 0 而非空值"

    # 比率型欄位必須是從該層級自己的分子分母重算,不是鄉鎮市區的率平均
    for r in county:
        if r["出生數"] and r["年底人口"] and r["粗出生率_千分比"]:
            expected = round(float(r["出生數"]) / float(r["年底人口"]) * 1000, 2)
            assert abs(float(r["粗出生率_千分比"]) - expected) < 0.01, \
                f'{r["年"]} {r["縣市"]} 粗出生率對不上重算值'

    print("面板表檢查全部通過")
    print(f"  縣市面板 {len(county)} 列、鄉鎮市區面板 {len(town)} 列")
    print(f"  出生數/年底人口與內政部公布值完全一致({min(OFFICIAL_BIRTHS)}-{max(OFFICIAL_BIRTHS)} 年)")


if __name__ == "__main__":
    main()
