"""
客戶保費資料清理:data/merged.csv -> data/customer/merged_clean.csv

處理項目:
  1. 用 3 碼郵遞區號(POSTCODE)反推縣市/鄉鎮市區,精度直接對齊現有 368 個鄉鎮市區。
     做法:從 data/reference/zipcode.json(6 碼路名對照表)取每個 3 碼字首底下出現
     次數最多的行政區當標準答案(多數決)。368 個字首全部對到至少一個鄉鎮市區,只有
     13 個字首同時橫跨多區(如 300 新竹市三區共用、600 嘉義市東西區共用),這是台灣
     郵遞區號本身的既有事實、不是資料錯誤,另存 郵遞區號_待覆核.csv 供人工確認多數決
     是否合理。優先用 POSTCODE、不用 ADDR 解析,因為 ADDR 只是縣市層級文字備註且不保證
     跟 POSTCODE 一致(例如有 1 筆 ADDR="台灣省" 這種完全無法用的髒值,但 POSTCODE
     本身仍然有效)。
  2. 標記(不刪除)無法定位的列:POSTCODE 是空字串或 NaN,共 46 筆(0.06%),幾乎都是
     ADDR/DOCCDESCO 同時缺值,推測是團體保單的行政/管理列而非個人要保人紀錄。
  3. 標記(不刪除)amount_NTD 為負值的列(推測是retreat/理賠沖銷調整)、幣別非 NTD 的列,
     留給後續分析決定要不要排除或換算,清理階段不擅自決定業務邏輯。
  4. 去除來源 CSV 每列多出的 13 個全空 trailing 欄位。

用法: python3 scripts/clean_customer_data.py
"""

import csv
import json
import os
from collections import Counter, defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PATH = os.path.join(BASE_DIR, "data", "merged.csv")
ZIP_REF_PATH = os.path.join(BASE_DIR, "data", "reference", "zipcode.json")
OUT_DIR = os.path.join(BASE_DIR, "data", "customer")
OUT_PATH = os.path.join(OUT_DIR, "merged_clean.csv")
LOOKUP_OUT_PATH = os.path.join(OUT_DIR, "郵遞區號_行政區對照表.csv")
REVIEW_OUT_PATH = os.path.join(OUT_DIR, "郵遞區號_待覆核.csv")

KEEP_COLUMNS = [
    "plan_code", "Name", "ptype1", "ptype3", "channel", "currency",
    "LIFESEX", "LIFEAGE", "WOCCUP", "POSTCODE", "ADDR", "amount_NTD", "DOCCDESCO",
]


def build_postcode_lookup():
    """3 碼郵遞區號字首 -> (縣市, 鄉鎮市區),用 zipcode.json 全部路名紀錄多數決。"""
    with open(ZIP_REF_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    votes = defaultdict(Counter)
    for city, cinfo in raw.items():
        for area, ainfo in cinfo.get("areas", {}).items():
            for road_info in ainfo.get("roads", {}).values():
                for scope in road_info.get("scopes", []):
                    prefix = str(scope["zipcode"])[:3]
                    votes[prefix][(city, area)] += 1

    lookup = {}
    ambiguous_rows = []
    for prefix, counter in votes.items():
        winner, winner_votes = counter.most_common(1)[0]
        total = sum(counter.values())
        lookup[prefix] = winner
        if len(counter) > 1:
            for (city, area), n in counter.most_common():
                ambiguous_rows.append({
                    "郵遞區號字首": prefix,
                    "縣市": city,
                    "鄉鎮市區": area,
                    "路名筆數": n,
                    "佔比": f"{n / total:.1%}",
                    "採用多數決": "是" if (city, area) == winner else "否",
                })
    return lookup, ambiguous_rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    lookup, ambiguous_rows = build_postcode_lookup()

    with open(LOOKUP_OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["郵遞區號字首", "縣市", "鄉鎮市區"])
        for prefix, (city, area) in sorted(lookup.items()):
            w.writerow([prefix, city, area])

    with open(REVIEW_OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["郵遞區號字首", "縣市", "鄉鎮市區", "路名筆數", "佔比", "採用多數決"])
        w.writeheader()
        w.writerows(ambiguous_rows)

    stats = Counter()
    with open(SRC_PATH, encoding="utf-8") as f_in, \
         open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f_out:
        reader = csv.DictReader(f_in)
        out_fields = KEEP_COLUMNS + [
            "縣市", "鄉鎮市區", "郵遞區號可定位", "郵遞區號多重行政區",
            "金額為負", "非台幣",
        ]
        writer = csv.DictWriter(f_out, fieldnames=out_fields)
        writer.writeheader()

        for row in reader:
            stats["total"] += 1
            postcode = (row.get("POSTCODE") or "").strip()

            out = {col: row.get(col, "") for col in KEEP_COLUMNS}

            match = lookup.get(postcode)
            if postcode and match:
                out["縣市"], out["鄉鎮市區"] = match
                out["郵遞區號可定位"] = "是"
                out["郵遞區號多重行政區"] = "是" if postcode in {r["郵遞區號字首"] for r in ambiguous_rows} else "否"
                stats["located"] += 1
            else:
                out["縣市"], out["鄉鎮市區"] = "", ""
                out["郵遞區號可定位"] = "否"
                out["郵遞區號多重行政區"] = ""
                stats["unlocated"] += 1
                stats["unlocated:" + (postcode if postcode else "(空白)")] += 1

            try:
                amount = float(row.get("amount_NTD") or 0)
            except ValueError:
                amount = 0
            out["金額為負"] = "是" if amount < 0 else "否"
            if amount < 0:
                stats["negative_amount"] += 1

            currency = (row.get("currency") or "").strip()
            out["非台幣"] = "是" if currency and currency != "NTD" else "否"
            if out["非台幣"] == "是":
                stats["foreign_currency"] += 1

            writer.writerow(out)

    print(f"讀入 {stats['total']} 筆")
    print(f"  可定位到鄉鎮市區: {stats['located']} 筆 ({stats['located']/stats['total']:.1%})")
    print(f"  郵遞區號空值/無效: {stats['unlocated']} 筆")
    for key in sorted(k for k in stats if k.startswith("unlocated:")):
        print(f"    - {key.split(':', 1)[1]!r}: {stats[key]} 筆")
    print(f"  金額為負(標記,未剔除): {stats['negative_amount']} 筆")
    print(f"  非台幣(標記,未剔除): {stats['foreign_currency']} 筆")
    print(f"  郵遞區號字首橫跨多個行政區(待覆核): {len({r['郵遞區號字首'] for r in ambiguous_rows})} 個")
    print(f"輸出: {OUT_PATH}")
    print(f"輸出: {LOOKUP_OUT_PATH}")
    print(f"輸出: {REVIEW_OUT_PATH}")


if __name__ == "__main__":
    main()
