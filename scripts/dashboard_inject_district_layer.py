"""
把 data/通訊處/district_layer.json(dashboard_build_layer.py 產出)注入既有的
data/通訊處/dashboard.html,加上一個鄉鎮市區 choropleth 圖層(人口密度/通訊處密度/
年齡結構/成長遷徙等指標可切換),疊在原本的通訊處點位圖層下面。

用註解 marker 包住三段注入內容(CSS/HTML/JS),重跑這支腳本可以安全地整段換新,
不會愈疊愈多份。DISTRICT_LAYER 的 GeoJSON 內容直接從檔案讀進來字串接上,不經過
LLM 對話上下文,避免搬運 1MB+ 文字。

會出現負值的指標(近5年人口成長率、淨遷入率、自然增加率)用 diverging 色階(紅-灰-藍),
斷點以 0 為中心對稱切;其餘量級型指標維持原本的 sequential 藍色漸層(五分位數切)。
色階來自 dataviz skill 的 references/palette.md:藍臂沿用既有 SEQUENTIAL_COLORS 的
300/600 步,紅臂用 OKLCH 對齊同樣的明度(L)、以文件記錄的類別色紅 #e34948 為錨點算出來,
灰色中點是文件記錄的 neutral midpoint,通過該 skill scripts/validate_palette.js 的
CVD 分離度檢查(all-pairs ΔE 20.0 protan / 26.4 normal-vision,無 FAIL)。

另外側欄有一個「複合篩選(白地候選)」區塊:可以動態加多個條件(欄位 x 運算子 x 數值),
AND 組合,欄位涵蓋所有量級指標、原始數量(人口數/戶數/通訊處數)、還有各公司個別據點數
(如「國泰人壽據點數 = 0」),符合條件的鄉鎮市區在地圖上用白色粗邊框標出來、不符合的同色
但大幅淡出,側欄同時列出符合清單(按每萬人通訊處數由小到大排,點列表項目會飛到該鄉鎮市區)。
這個功能對應 TODO 最後一項「標出候選白地鄉鎮市區」,做成互動篩選比固定排序清單更彈性
(門檻可以隨時調)。

「客戶實績」這組指標來自 clean_customer_data.py + dashboard_build_layer.py 新接的當月客戶保費
明細(保戶數用 性別+年齡+3碼郵遞區號 去重近似):每萬人保戶數、保戶滲透率、客單價是量級型,
延用 sequential 藍色漸層;主力通路(該區保費收入最高的通路)是類別欄位,不能套連續色階,
改用固定類別色——只挑 dataviz skill categorical palette 的前 3 個色階(blue/orange/aqua),
因為那是唯一通過 all-pairs CVD 驗證的組合(4 階以上 all-pairs 會 fail,palette.md 有記載),
AG/BR/BA 三個主力通路各佔一色,量少的 EC/FS 併成「其他」用單獨的中性灰(不計入類別色驗證,
跟 NO_DATA_COLOR 是同一種處理方式)。主力通路目前不放進複合篩選欄位,因為篩選框架只吃數值
比較(>=/<=/=),類別欄位硬塞進去意義不大。

「排除投資型保單」checkbox:投資型(變額壽險,ptype3 開頭 U)金額波動遠大於其他商品
(標準差全商品類型最大),混在一起會拉歪客單價這類平均值指標。dashboard_build_layer.py
已經把 5 個客戶實績欄位(保戶數/每萬人保戶數/保戶滲透率/總保費收入/客單價/主力通路/
主力通路占比/通路別保費)各算了「全部商品」和「_不含投資型」兩份存進 GeoJSON properties,
checkbox 打勾時前端一律改讀後綴版本(custField() 集中處理),圖層著色、複合篩選、tooltip
三處都會跟著切換,不用重新載入資料。
"""

import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_PATH = os.path.join(BASE_DIR, "data", "通訊處", "dashboard.html")
LAYER_JSON_PATH = os.path.join(BASE_DIR, "data", "通訊處", "district_layer.json")

CSS_BLOCK = """
  /* DISTRICT_LAYER_CSS_START */
  .metric-select {
    width: 100%; font-size: 13px; padding: 7px 8px; border-radius: 6px;
    border: 1px solid var(--border); background: var(--panel-bg); color: var(--text);
    margin-bottom: 10px;
  }
  .metric-select optgroup { color: var(--text-dim); font-style: normal; font-weight: 600; }
  .metric-select option { color: var(--text); font-weight: 400; }
  .toggle-row {
    display: flex; align-items: flex-start; gap: 6px; font-size: 12px; color: var(--text-dim);
    line-height: 1.4; margin-bottom: 10px; cursor: pointer;
  }
  .toggle-row input { margin-top: 2px; flex-shrink: 0; }
  #district-legend { font-size: 11.5px; color: var(--text-dim); margin-bottom: 4px; }
  #district-legend .legend-title { color: var(--text); font-weight: 600; margin-bottom: 6px; font-size: 12.5px; }
  #district-legend .legend-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  #district-legend .legend-swatch { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }
  #district-legend .legend-note { margin-top: 6px; line-height: 1.5; }
  .district-tooltip { font-size: 12.5px; line-height: 1.6; }
  .district-tooltip .t-title { font-weight: 700; margin-bottom: 3px; }
  .district-tooltip .t-row { display: flex; justify-content: space-between; gap: 14px; }
  .district-tooltip .t-row span:last-child { font-variant-numeric: tabular-nums; font-weight: 600; }
  .filter-row { display: flex; gap: 4px; align-items: center; margin-bottom: 6px; }
  .filter-field {
    flex: 1 1 auto; min-width: 0; font-size: 12px; padding: 5px 4px; border-radius: 5px;
    border: 1px solid var(--border); background: var(--panel-bg); color: var(--text);
  }
  .filter-op {
    flex: 0 0 42px; font-size: 12px; padding: 5px 2px; border-radius: 5px;
    border: 1px solid var(--border); background: var(--panel-bg); color: var(--text);
  }
  .filter-value {
    flex: 0 0 62px; min-width: 0; font-size: 12px; padding: 5px 6px; border-radius: 5px;
    border: 1px solid var(--border); background: var(--panel-bg); color: var(--text);
  }
  .filter-remove {
    flex: 0 0 22px; height: 26px; border-radius: 5px; border: 1px solid var(--border);
    background: var(--panel-bg); color: var(--text-dim); cursor: pointer; font-size: 14px; line-height: 1;
  }
  .filter-remove:hover { background: var(--border); color: var(--text); }
  .filter-add-btn {
    width: 100%; font-size: 12.5px; padding: 6px 8px; border-radius: 6px;
    border: 1px dashed var(--border); background: transparent; color: var(--text-dim);
    cursor: pointer; margin-bottom: 8px;
  }
  .filter-add-btn:hover { color: var(--text); border-color: var(--text-dim); }
  .filter-summary { font-size: 12px; color: var(--text); font-weight: 600; margin-bottom: 6px; }
  .filter-result-list { max-height: 200px; overflow-y: auto; border-top: 1px solid var(--border); padding-top: 6px; margin-bottom: 16px; }
  .filter-result-item {
    display: flex; justify-content: space-between; gap: 8px; font-size: 12px;
    padding: 5px 4px; border-radius: 5px; cursor: pointer; color: var(--text);
  }
  .filter-result-item:hover { background: var(--border); }
  .filter-result-item span { color: var(--text-dim); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .filter-empty { color: var(--text-dim); cursor: default; }
  .filter-empty:hover { background: none; }
  /* DISTRICT_LAYER_CSS_END */
"""

HTML_BLOCK = """
    <!-- DISTRICT_LAYER_HTML_START -->
    <div class="section-title">行政區指標圖層</div>
    <select id="metricSelect" class="metric-select"></select>
    <label class="toggle-row">
      <input type="checkbox" id="excludeInvestmentToggle">
      排除投資型保單(變額壽險，金額波動大，會拉歪客單價等平均值指標)
    </label>
    <div id="district-legend"></div>

    <div class="section-title">複合篩選(白地候選)</div>
    <div id="filterConditions"></div>
    <button id="addFilterBtn" class="filter-add-btn" type="button">+ 新增條件</button>
    <div id="filterSummary"></div>
    <div id="filterResultList"></div>
    <!-- DISTRICT_LAYER_HTML_END -->
"""


def build_js_block(layer_json_text: str) -> str:
    return f"""
// DISTRICT_LAYER_JS_START
const DISTRICT_LAYER = {layer_json_text};

// group 對應 TODO_population_data.md 的 Phase 分類,同一類指標在下拉選單裡連在一起,
// 不要規模/年齡/成長/所得/供給這些性質不同的指標穿插。
const METRICS = [
  {{ id: 'none', label: '不顯示(只看通訊處點位)' }},
  {{ id: '人口密度', label: '人口密度(人/km²)', unit: '', decimals: 0, group: '規模指標' }},
  {{ id: '65歲以上占比', label: '65歲以上人口占比', unit: '%', decimals: 1, group: '年齡結構' }},
  {{ id: '老化指數', label: '老化指數(65+ / 0-14 x100)', unit: '', decimals: 1, group: '年齡結構' }},
  {{ id: '扶養比', label: '扶養比(對15-64歲人口)', unit: '', decimals: 1, group: '年齡結構' }},
  {{ id: '近5年人口成長率', label: '近5年人口成長率', unit: '%', decimals: 2, diverging: true, group: '成長與遷徙' }},
  {{ id: '淨遷入率', label: '淨遷入率(近12月，千分比)', unit: '‰', decimals: 2, diverging: true, group: '成長與遷徙' }},
  {{ id: '自然增加率', label: '自然增加率(出生-死亡，近12月，千分比)', unit: '‰', decimals: 2, diverging: true, group: '成長與遷徙' }},
  {{ id: '所得中位數', label: '綜合所得中位數(每申報戶，千元)', unit: ' 千元', decimals: 0, group: '所得與消費' }},
  {{ id: '平均消費支出', label: '平均每戶消費支出(縣市退回值，元)', unit: ' 元', decimals: 0, group: '所得與消費' }},
  {{ id: '每萬人通訊處數', label: '每萬人通訊處數(6家合計，數字低＝相對白地)', unit: '', decimals: 2, group: '競爭/供給' }},
  {{ id: '每萬人保戶數', label: '每萬人保戶數(當月，人數近似值)', unit: '', decimals: 2, group: '客戶實績' }},
  {{ id: '保戶滲透率', label: '保戶滲透率(保戶數/人口數)', unit: '%', decimals: 3, group: '客戶實績' }},
  {{ id: '客單價', label: '客單價(每保戶當月保費，元)', unit: ' 元', decimals: 0, group: '客戶實績' }},
  {{ id: '主力通路', label: '主力通路(當月保費收入最高的通路)', group: '客戶實績', categorical: true }},
];

// 複合篩選(白地候選)用的欄位清單,比 METRICS 更廣 —— 包含原始數量(人口數/戶數/通訊處數)
// 和各公司個別據點數,METRICS 只挑了適合當 choropleth 著色的量級指標。group 一樣對齊
// TODO_population_data.md 的 Phase 分類。
const FILTER_FIELDS = [
  {{ id: '人口數', label: '人口數', unit: '', decimals: 0, group: '規模指標' }},
  {{ id: '戶數', label: '戶數', unit: '', decimals: 0, group: '規模指標' }},
  {{ id: '平均戶量', label: '平均戶量', unit: '', decimals: 2, group: '規模指標' }},
  {{ id: '人口密度', label: '人口密度(人/km²)', unit: '', decimals: 0, group: '規模指標' }},
  {{ id: '65歲以上占比', label: '65歲以上人口占比', unit: '%', decimals: 1, group: '年齡結構' }},
  {{ id: '40_64歲占比', label: '40-64歲人口占比', unit: '%', decimals: 1, group: '年齡結構' }},
  {{ id: '老化指數', label: '老化指數', unit: '', decimals: 1, group: '年齡結構' }},
  {{ id: '扶養比', label: '扶養比', unit: '', decimals: 1, group: '年齡結構' }},
  {{ id: '近5年人口成長率', label: '近5年人口成長率', unit: '%', decimals: 2, group: '成長與遷徙' }},
  {{ id: '淨遷入率', label: '淨遷入率(近12月)', unit: '‰', decimals: 2, group: '成長與遷徙' }},
  {{ id: '自然增加率', label: '自然增加率(近12月)', unit: '‰', decimals: 2, group: '成長與遷徙' }},
  {{ id: '所得中位數', label: '綜合所得中位數(千元)', unit: ' 千元', decimals: 0, group: '所得與消費' }},
  {{ id: '所得平均數', label: '綜合所得平均數(千元)', unit: ' 千元', decimals: 0, group: '所得與消費' }},
  {{ id: '納稅單位數', label: '納稅單位數(戶)', unit: '', decimals: 0, group: '所得與消費' }},
  {{ id: '平均消費支出', label: '平均每戶消費支出(縣市退回值)', unit: ' 元', decimals: 0, group: '所得與消費' }},
  {{ id: '通訊處數', label: '通訊處數(6家合計)', unit: '', decimals: 0, group: '競爭/供給' }},
  {{ id: '每萬人通訊處數', label: '每萬人通訊處數', unit: '', decimals: 2, group: '競爭/供給' }},
  {{ id: '保戶數', label: '保戶數(當月，人數近似值)', unit: '', decimals: 0, group: '客戶實績' }},
  {{ id: '每萬人保戶數', label: '每萬人保戶數', unit: '', decimals: 2, group: '客戶實績' }},
  {{ id: '保戶滲透率', label: '保戶滲透率', unit: '%', decimals: 3, group: '客戶實績' }},
  {{ id: '總保費收入', label: '總保費收入(當月，元)', unit: ' 元', decimals: 0, group: '客戶實績' }},
  {{ id: '客單價', label: '客單價(元)', unit: ' 元', decimals: 0, group: '客戶實績' }},
];
const FILTER_COMPANIES = Array.from(new Set(
  DISTRICT_LAYER.features.flatMap(f => Object.keys(f.properties['通訊處數_by公司'] || {{}}))
)).sort();
FILTER_COMPANIES.forEach(c => {{
  FILTER_FIELDS.push({{ id: 'company:' + c, label: c + ' 據點數', unit: '', decimals: 0, group: '各公司據點數' }});
}});

// 7 階 sequential 藍色漸層,直接取 dataviz skill palette.md 裡文件記錄的 100/200/300/400/500/600/700 步,
// 不用內插。
const SEQUENTIAL_COLORS = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
// 紅(負)-灰(近0)-藍(正) diverging pair,7 階(3 紅 + 1 灰 + 3 藍)。藍臂沿用 SEQUENTIAL_COLORS
// 的 300/500/700 步,紅臂在 OKLCH 上取相同明度、以文件記錄的類別紅 #e34948 為色相/彩度錨點算出來,
// 灰色中點是文件記錄的 neutral midpoint。用 scripts/validate_palette.js 驗過 CVD 分離度
// (worst all-pairs ΔE 13.9 protan / 16.0 normal-vision,都在 target 之上;深紅/深藍兩端明度較低,
// 落在 categorical 六項檢查的明度區間之外——這跟文件裡本來的深藍 700 步一樣,是 sequential/diverging
// 專用的預期行為,六項檢查是給 categorical 識別色用的,不適用在這裡)。
const DIVERGING_COLORS = ['#7e0000', '#c1252c', '#ff6964', '#f0efec', '#6da7ec', '#256abf', '#0d366b'];
const NO_DATA_COLOR = '#c3c2b7';

// 主力通路是類別欄位,用固定色而非漸層。只取 dataviz skill categorical palette 的前 3 色
// (blue/orange/aqua)——這是唯一通過 all-pairs CVD 驗證的子集(見本檔開頭註解),AG/BR/BA
// 三個主要通路各佔一色;EC/FS 量少併成「其他」,用單獨的中性灰(不算進類別色驗證範圍,
// 跟 NO_DATA_COLOR 是同一種處理方式,只是換一個灰階以跟「無資料」區分開)。
const CHANNEL_COLORS = {{ 'AG': '#2a78d6', 'BR': '#eb6834', 'BA': '#1baf7a' }};
const CHANNEL_LABELS = {{ 'AG': '業務員(AG)', 'BR': '銀行保代(BR)', 'BA': '銀保(BA)' }};
const OTHER_CHANNEL_COLOR = '#6b6a63';
const OTHER_CHANNEL_LABEL = '其他(EC/FS)';

function channelColor(channel) {{
  return CHANNEL_COLORS[channel] || (channel ? OTHER_CHANNEL_COLOR : NO_DATA_COLOR);
}}

function channelLabel(channel) {{
  if (!channel) return '無資料';
  return CHANNEL_LABELS[channel] || OTHER_CHANNEL_LABEL;
}}

// 客戶實績欄位在 dashboard_build_layer.py 同時算了「全部商品」和「_不含投資型」兩份,
// 排除投資型 checkbox 打勾時,圖層著色/篩選/tooltip 一律改讀後綴版本,其他非客戶欄位
// (人口、通訊處等)不受這個開關影響,custField() 只對客戶欄位 id 做後綴替換。
const CUSTOMER_FIELD_IDS = new Set(['保戶數', '每萬人保戶數', '保戶滲透率', '總保費收入', '客單價', '主力通路', '主力通路占比', '通路別保費']);
let excludeInvestment = false;
function custField(props, fieldId) {{
  const resolved = (excludeInvestment && CUSTOMER_FIELD_IDS.has(fieldId)) ? fieldId + '_不含投資型' : fieldId;
  return props[resolved];
}}

function quantileBreaks(values) {{
  const v = values.filter(x => x != null && !Number.isNaN(x)).sort((a, b) => a - b);
  if (v.length === 0) return null;
  const steps = [1, 2, 3, 4, 5, 6].map(k => k / 7);
  const pick = p => v[Math.min(v.length - 1, Math.floor(p * (v.length - 1)))];
  return steps.map(pick);
}}

// diverging 用的斷點以 0 為中心對稱切,而不是資料分位數 —— polarity 圖層的重點是
// 「哪一側偏離 0」,斷點固定在資料絕對值最大值的比例上,讀者才能一眼看出 0 在哪一階。
// 7 階(3 紅 + 1 灰 + 3 藍)需要 6 個斷點,依 0.6/0.3/0.05 三個比例左右對稱切。
function divergingBreaks(values) {{
  const v = values.filter(x => x != null && !Number.isNaN(x));
  if (v.length === 0) return null;
  const hi = Math.max(...v.map(Math.abs));
  if (hi === 0) return null;
  const ratios = [0.6, 0.3, 0.05];
  return [...ratios.map(r => -hi * r), ...ratios.slice().reverse().map(r => hi * r)];
}}

function colorFor(value, breaks, diverging) {{
  if (value == null || Number.isNaN(value) || !breaks) return NO_DATA_COLOR;
  const palette = diverging ? DIVERGING_COLORS : SEQUENTIAL_COLORS;
  let i = 0;
  while (i < breaks.length && value > breaks[i]) i++;
  return palette[i];
}}

function fmt(value, metric) {{
  if (value == null || Number.isNaN(value)) return '無資料';
  const n = Number(value).toLocaleString('zh-TW', {{ minimumFractionDigits: metric.decimals ?? 0, maximumFractionDigits: metric.decimals ?? 0 }});
  return n + (metric.unit || '');
}}

let currentMetricId = '人口密度';
let currentBreaks = null;
let matchedKeys = null; // null = 沒有篩選中;否則是符合所有條件的「縣市鄉鎮市區」key 集合

function districtKey(props) {{
  return props['縣市'] + props['鄉鎮市區'];
}}

const districtLayer = L.geoJSON(DISTRICT_LAYER, {{
  style: featureStyle,
  onEachFeature: (feature, layer) => {{
    const p = feature.properties;
    const companies = Object.entries(p['通訊處數_by公司'] || {{}}).map(([k, v]) => `${{escapeHtml(k)}} ${{v}}`).join('、') || '無';
    // 內容用 function 而不是字串:排除投資型 checkbox 打勾時要重算,tooltip 每次打開才會讀到
    // 當下的 excludeInvestment 狀態,不用整份圖層重建。
    layer.bindTooltip(() => {{
      const 主力通路占比 = custField(p, '主力通路占比');
      return `
      <div class="district-tooltip">
        <div class="t-title">${{escapeHtml(p['縣市'])}}${{escapeHtml(p['鄉鎮市區'])}}</div>
        <div class="t-row"><span>人口數</span><span>${{p['人口數'].toLocaleString('zh-TW')}}</span></div>
        <div class="t-row"><span>人口密度</span><span>${{fmt(p['人口密度'], {{decimals:0}})}} 人/km²</span></div>
        <div class="t-row"><span>65歲以上占比</span><span>${{fmt(p['65歲以上占比'], {{decimals:1}})}}%</span></div>
        <div class="t-row"><span>老化指數</span><span>${{fmt(p['老化指數'], {{decimals:1}})}}</span></div>
        <div class="t-row"><span>扶養比</span><span>${{fmt(p['扶養比'], {{decimals:1}})}}</span></div>
        <div class="t-row"><span>近5年人口成長率</span><span>${{fmt(p['近5年人口成長率'], {{decimals:2}})}}%</span></div>
        <div class="t-row"><span>淨遷入率</span><span>${{fmt(p['淨遷入率'], {{decimals:2}})}}‰</span></div>
        <div class="t-row"><span>自然增加率</span><span>${{fmt(p['自然增加率'], {{decimals:2}})}}‰</span></div>
        <div class="t-row"><span>綜合所得中位數</span><span>${{fmt(p['所得中位數'], {{decimals:0}})}} 千元</span></div>
        <div class="t-row"><span>平均消費支出${{p['消費支出資料層級'] ? '（縣市）' : ''}}</span><span>${{fmt(p['平均消費支出'], {{decimals:0}})}} 元</span></div>
        <div class="t-row"><span>通訊處數(6家合計)</span><span>${{p['通訊處數']}}</span></div>
        <div class="t-row"><span>每萬人通訊處數</span><span>${{fmt(p['每萬人通訊處數'], {{decimals:2}})}}</span></div>
        <div style="margin-top:4px;color:var(--text-dim);">${{companies}}</div>
        <div class="t-row" style="margin-top:4px;"><span>保戶數(當月)${{excludeInvestment ? '（不含投資型）' : ''}}</span><span>${{fmt(custField(p, '保戶數'), {{decimals:0}})}}</span></div>
        <div class="t-row"><span>每萬人保戶數</span><span>${{fmt(custField(p, '每萬人保戶數'), {{decimals:2}})}}</span></div>
        <div class="t-row"><span>保戶滲透率</span><span>${{fmt(custField(p, '保戶滲透率'), {{decimals:3}})}}%</span></div>
        <div class="t-row"><span>總保費收入</span><span>${{fmt(custField(p, '總保費收入'), {{decimals:0}})}} 元</span></div>
        <div class="t-row"><span>客單價</span><span>${{fmt(custField(p, '客單價'), {{decimals:0}})}} 元</span></div>
        <div class="t-row"><span>主力通路</span><span>${{escapeHtml(channelLabel(custField(p, '主力通路')))}}${{主力通路占比 != null ? ' (' + fmt(主力通路占比 * 100, {{decimals:1}}) + '%)' : ''}}</span></div>
        <div style="margin-top:2px;color:var(--text-dim);">${{
          Object.entries(custField(p, '通路別保費') || {{}}).map(([k, v]) => `${{escapeHtml(k)}} ${{Math.round(v).toLocaleString('zh-TW')}}元`).join('、') || '無資料'
        }}</div>
      </div>
    `;
    }}, {{ sticky: true }});
  }},
}}).addTo(map);

function featureStyle(feature) {{
  const p = feature.properties;
  const isFiltering = matchedKeys != null;
  const matched = !isFiltering || matchedKeys.has(districtKey(p));

  if (currentMetricId === 'none') {{
    if (!isFiltering) {{
      return {{ fillOpacity: 0, weight: 0.5, color: 'rgba(120,120,120,0.35)' }};
    }}
    // 沒選圖層指標、但篩選中:只把符合條件的鄉鎮市區塗成金色高亮,其餘不畫
    return matched
      ? {{ fillColor: '#e0a300', fillOpacity: 0.5, weight: 1.4, color: 'rgba(224,163,0,0.95)' }}
      : {{ fillOpacity: 0, weight: 0.3, color: 'rgba(120,120,120,0.15)' }};
  }}

  const metric = METRICS.find(m => m.id === currentMetricId);
  const v = custField(p, currentMetricId);
  const fillColor = metric.categorical ? channelColor(v) : colorFor(v, currentBreaks, metric.diverging);
  if (!isFiltering) {{
    return {{ fillColor, fillOpacity: 0.55, weight: 0.6, color: 'rgba(255,255,255,0.6)' }};
  }}
  // 篩選中:符合條件的維持正常顏色 + 加粗白邊「跳出來」,不符合的同色但大幅降低不透明度淡出
  return matched
    ? {{ fillColor, fillOpacity: 0.7, weight: 1.6, color: 'rgba(255,255,255,0.95)' }}
    : {{ fillColor, fillOpacity: 0.08, weight: 0.3, color: 'rgba(255,255,255,0.15)' }};
}}

function renderLegend(metric) {{
  const el = document.getElementById('district-legend');
  if (metric.id === 'none') {{ el.innerHTML = ''; return; }}
  const investmentNote = (metric.group === '客戶實績' && excludeInvestment) ? '目前已排除投資型保單(變額壽險)。' : '';
  if (metric.categorical) {{
    const rows = [...Object.keys(CHANNEL_COLORS).map(c => [channelColor(c), channelLabel(c)]), [OTHER_CHANNEL_COLOR, OTHER_CHANNEL_LABEL], [NO_DATA_COLOR, '無資料']]
      .map(([color, label]) => `<div class="legend-row"><span class="legend-swatch" style="background:${{color}}"></span>${{escapeHtml(label)}}</div>`).join('');
    el.innerHTML = `<div class="legend-title">${{escapeHtml(metric.label)}}</div>${{rows}}<div class="legend-note">${{investmentNote}}滑鼠移到行政區上可看該區各通路的保費收入細項。</div>`;
    return;
  }}
  if (!currentBreaks) {{ el.innerHTML = ''; return; }}
  const palette = metric.diverging ? DIVERGING_COLORS : SEQUENTIAL_COLORS;
  const edges = [null, ...currentBreaks, null];
  const rows = palette.map((color, i) => {{
    const lo = edges[i], hi = edges[i + 1];
    let label;
    if (lo == null) label = `≤ ${{fmt(hi, metric)}}`;
    else if (hi == null) label = `> ${{fmt(lo, metric)}}`;
    else label = `${{fmt(lo, metric)}} ~ ${{fmt(hi, metric)}}`;
    return `<div class="legend-row"><span class="legend-swatch" style="background:${{color}}"></span>${{label}}</div>`;
  }}).join('');
  const note = metric.diverging
    ? '滑鼠移到行政區上可看完整數據；斷點以 0 為中心對稱切(依資料最大絕對值的比例),灰色系中間色代表接近 0,深紅/深藍代表偏離 0 的兩端；淺灰代表無資料。'
    : '滑鼠移到行政區上可看完整數據；灰色代表無資料。';
  el.innerHTML = `<div class="legend-title">${{escapeHtml(metric.label)}}</div>${{rows}}<div class="legend-note">${{investmentNote}}${{note}}</div>`;
}}

function applyMetric(metricId) {{
  currentMetricId = metricId;
  const metric = METRICS.find(m => m.id === metricId);
  if (metricId === 'none' || metric.categorical) {{
    currentBreaks = null;
  }} else {{
    const values = DISTRICT_LAYER.features.map(f => custField(f.properties, metricId));
    currentBreaks = metric.diverging ? divergingBreaks(values) : quantileBreaks(values);
  }}
  districtLayer.setStyle(featureStyle);
  renderLegend(metric);
}}

// 通用的「分組下拉選單」建構工具,圖層指標選單和篩選條件的欄位選單共用同一套分組邏輯,
// 沒有 group 的項目(如「不顯示」)直接掛在最外層,不建 optgroup。
function buildGroupedOptions(selectEl, items) {{
  const groups = {{}};
  items.forEach(m => {{
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.label;
    if (!m.group) {{
      selectEl.appendChild(opt);
      return;
    }}
    if (!groups[m.group]) {{
      const g = document.createElement('optgroup');
      g.label = m.group;
      selectEl.appendChild(g);
      groups[m.group] = g;
    }}
    groups[m.group].appendChild(opt);
  }});
}}

const metricSelectEl = document.getElementById('metricSelect');
buildGroupedOptions(metricSelectEl, METRICS);
metricSelectEl.value = currentMetricId;
metricSelectEl.addEventListener('change', (e) => applyMetric(e.target.value));
applyMetric(currentMetricId);

document.getElementById('excludeInvestmentToggle').addEventListener('change', (e) => {{
  excludeInvestment = e.target.checked;
  applyMetric(currentMetricId);
  recomputeFilter();
}});

// ---- 複合篩選(白地候選):多個條件用 AND 組合,綜合任意指標(含各公司通訊處據點數)篩鄉鎮市區 ----

function filterFieldValue(props, fieldId) {{
  if (fieldId.startsWith('company:')) {{
    return (props['通訊處數_by公司'] || {{}})[fieldId.slice('company:'.length)] || 0;
  }}
  return custField(props, fieldId);
}}

function readFilterConditions() {{
  return Array.from(document.querySelectorAll('#filterConditions .filter-row'))
    .map(row => ({{
      field: row.querySelector('.filter-field').value,
      op: row.querySelector('.filter-op').value,
      value: row.querySelector('.filter-value').value,
    }}))
    .filter(c => c.value !== '')
    .map(c => ({{ ...c, value: Number(c.value) }}))
    .filter(c => !Number.isNaN(c.value));
}}

function matchesFilterConditions(props, conditions) {{
  return conditions.every(c => {{
    const v = filterFieldValue(props, c.field);
    if (v == null || Number.isNaN(v)) return false;
    if (c.op === '>=') return v >= c.value;
    if (c.op === '<=') return v <= c.value;
    return Math.abs(v - c.value) < 1e-9; // '='
  }});
}}

function flyToDistrict(key) {{
  districtLayer.eachLayer(layer => {{
    if (districtKey(layer.feature.properties) === key) {{
      map.fitBounds(layer.getBounds(), {{ maxZoom: 13 }});
      layer.openTooltip();
    }}
  }});
}}

function renderFilterResults() {{
  const summaryEl = document.getElementById('filterSummary');
  const listEl = document.getElementById('filterResultList');
  if (matchedKeys == null) {{
    summaryEl.textContent = '';
    listEl.innerHTML = '';
    return;
  }}
  summaryEl.textContent = `符合條件:${{matchedKeys.size}} 個鄉鎮市區`;
  const matchedFeatures = DISTRICT_LAYER.features
    .filter(f => matchedKeys.has(districtKey(f.properties)))
    .sort((a, b) => (a.properties['每萬人通訊處數'] ?? Infinity) - (b.properties['每萬人通訊處數'] ?? Infinity));
  listEl.innerHTML = matchedFeatures.length
    ? matchedFeatures.map(f => {{
        const p = f.properties;
        const key = districtKey(p);
        return `<div class="filter-result-item" data-key="${{escapeHtml(key)}}">` +
          `<span style="color:var(--text);flex-shrink:1;">${{escapeHtml(p['縣市'])}}${{escapeHtml(p['鄉鎮市區'])}}</span>` +
          `<span>每萬人通訊處數 ${{fmt(p['每萬人通訊處數'], {{decimals: 2}})}}</span></div>`;
      }}).join('')
    : '<div class="filter-result-item filter-empty">沒有符合所有條件的鄉鎮市區</div>';
}}

function recomputeFilter() {{
  const conditions = readFilterConditions();
  if (conditions.length === 0) {{
    matchedKeys = null;
  }} else {{
    matchedKeys = new Set(
      DISTRICT_LAYER.features
        .filter(f => matchesFilterConditions(f.properties, conditions))
        .map(f => districtKey(f.properties))
    );
  }}
  districtLayer.setStyle(featureStyle);
  renderFilterResults();
}}

function addFilterRow() {{
  const container = document.getElementById('filterConditions');
  const row = document.createElement('div');
  row.className = 'filter-row';

  const fieldSelect = document.createElement('select');
  fieldSelect.className = 'filter-field';
  buildGroupedOptions(fieldSelect, FILTER_FIELDS);

  const opSelect = document.createElement('select');
  opSelect.className = 'filter-op';
  [['>=', '≥'], ['<=', '≤'], ['=', '=']].forEach(([value, label]) => {{
    const o = document.createElement('option');
    o.value = value;
    o.textContent = label;
    opSelect.appendChild(o);
  }});

  const valueInput = document.createElement('input');
  valueInput.type = 'number';
  valueInput.step = 'any';
  valueInput.className = 'filter-value';
  valueInput.placeholder = '數值';

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'filter-remove';
  removeBtn.textContent = '×';
  removeBtn.addEventListener('click', () => {{ row.remove(); recomputeFilter(); }});

  fieldSelect.addEventListener('change', recomputeFilter);
  opSelect.addEventListener('change', recomputeFilter);
  valueInput.addEventListener('input', recomputeFilter);

  row.appendChild(fieldSelect);
  row.appendChild(opSelect);
  row.appendChild(valueInput);
  row.appendChild(removeBtn);
  container.appendChild(row);
}}

document.getElementById('addFilterBtn').addEventListener('click', addFilterRow);
document.getElementById('filterResultList').addEventListener('click', (e) => {{
  const item = e.target.closest('.filter-result-item[data-key]');
  if (item) flyToDistrict(item.dataset.key);
}});
recomputeFilter();
// DISTRICT_LAYER_JS_END
"""


def strip_block(text: str, start_marker: str, end_marker: str) -> str:
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    return pattern.sub("", text)


def main():
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        html = f.read()
    with open(LAYER_JSON_PATH, encoding="utf-8") as f:
        layer_json_text = f.read()

    html = strip_block(html, "/* DISTRICT_LAYER_CSS_START */", "/* DISTRICT_LAYER_CSS_END */")
    html = strip_block(html, "<!-- DISTRICT_LAYER_HTML_START -->", "<!-- DISTRICT_LAYER_HTML_END -->")
    html = strip_block(html, "// DISTRICT_LAYER_JS_START", "// DISTRICT_LAYER_JS_END")

    css_anchor = "</style>"
    assert css_anchor in html, "找不到 </style> 錨點"
    html = html.replace(css_anchor, CSS_BLOCK + css_anchor, 1)

    html_anchor = '<div class="section-title">顯示公司</div>'
    assert html_anchor in html, "找不到 顯示公司 錨點"
    html = html.replace(html_anchor, HTML_BLOCK + "\n    " + html_anchor, 1)

    # 一定要放在 `const map = L.map(...)` 之後(district layer 要 addTo(map)),
    # 用 PRECISION_LABEL 這行當錨點,插在它前面剛好在 tile layer 設定完之後。
    js_anchor = "const PRECISION_LABEL"
    assert html.count(js_anchor) == 1, "PRECISION_LABEL 錨點不唯一或找不到"
    js_block = build_js_block(layer_json_text)
    html = html.replace(js_anchor, js_block + "\n" + js_anchor, 1)

    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size_mb = os.path.getsize(DASHBOARD_PATH) / 1024 / 1024
    print(f"已注入 district layer -> {DASHBOARD_PATH} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
