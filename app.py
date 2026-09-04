# -*- coding: utf-8 -*-
"""
永寬化學股份有限公司
ISO 50001 重大能源使用設備 (SEUs) 網頁版管理系統
v2.1 - 修正圓餅圖空值錯誤、強化資料讀取穩定性
"""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import base64, json, os, math, glob, re
from io import BytesIO
from PIL import Image
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Helper: 照片壓縮
#   目的：使用者手機拍照隨便就是 3~8MB 一張，335 台設備 x 2 張(外觀+銘牌)
#   累積起來很快就會撞到 Streamlit Cloud 的記憶體上限（約 1GB）跟 Supabase
#   免費方案的 1GB 儲存空間。統一在「存進資料庫之前」做兩件事：
#     1. 縮小尺寸：長邊超過 MAX_DIM 就等比例縮小（現場設備照片不需要原始解析度）
#     2. 轉成 JPEG 並壓縮品質：PNG/HEIC 等格式統一轉為 JPEG，去除不需要的透明度、
#        大幅縮小檔案體積
# ─────────────────────────────────────────────────────────────────────────────
PHOTO_MAX_DIM = 1600   # 長邊像素上限，一般設備現場照片這個解析度已經很夠看
PHOTO_QUALITY = 82     # JPEG 壓縮品質（1~95），82 是畫質與檔案大小的合理平衡點

def compress_photo_bytes(raw_bytes, max_dim=PHOTO_MAX_DIM, quality=PHOTO_QUALITY):
    """把任意圖片位元組壓縮成較小的 JPEG 位元組。
    輸入格式失敗時（例如檔案損毀），直接回傳原始位元組，不讓壓縮失敗擋住整個上傳流程。"""
    try:
        img = Image.open(BytesIO(raw_bytes))
        img = img.convert("RGB")   # 去除透明度／調色盤，JPEG 不支援透明通道
        w, h = img.size
        longest = max(w, h)
        if longest > max_dim:
            scale = max_dim / longest
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return raw_bytes

def compress_photo_to_b64(uploaded_file, max_dim=PHOTO_MAX_DIM, quality=PHOTO_QUALITY):
    """接收 st.file_uploader 回傳的檔案物件，壓縮後回傳 (base64字串, 原始KB, 壓縮後KB)"""
    raw_bytes = uploaded_file.read()
    original_kb = len(raw_bytes) / 1024
    compressed_bytes = compress_photo_bytes(raw_bytes, max_dim, quality)
    compressed_kb = len(compressed_bytes) / 1024
    return base64.b64encode(compressed_bytes).decode(), original_kb, compressed_kb

# ─────────────────────────────────────────────────────────────────────────────
# Helper: 置中表格
# ─────────────────────────────────────────────────────────────────────────────
def centered_table(df, context="default"):
    """將 DataFrame 轉為可自訂字體與對齊的 HTML 表格
    context: dash / equip / score / energy / load / default
    """
    fmt = st.session_state.get("fmt", {})
    ff  = fmt.get("font_family", "Noto Sans TC")
    key_size  = f"{context}_table_size"
    key_align = f"{context}_table_align"
    fs    = fmt.get(key_size,  st.session_state.get("font_size", 14))
    align = fmt.get(key_align, st.session_state.get("table_align", "center"))
    styles = f"""
    <style>
    .ctable {{ width:100%; border-collapse:collapse; font-size:{fs}px;
               margin-bottom:8px; font-family:'{ff}',sans-serif; }}
    .ctable th {{
        background:#1a3a5c; color:#fff; padding:9px 12px;
        text-align:center; font-weight:700; border:1px solid #334155;
        font-size:{fs}px; font-family:'{ff}',sans-serif;
    }}
    .ctable td {{
        padding:8px 12px; text-align:{align};
        border:1px solid #e2e8f0; color:#1e293b;
        font-size:{fs}px; font-family:'{ff}',sans-serif;
    }}
    .ctable tr:nth-child(even) td {{ background:#f8fafc; }}
    .ctable tr:hover td {{ background:#eff6ff; }}
    </style>
    """
    rows_html = ""
    for _, row in df.iterrows():
        cells = "".join(f"<td>{v}</td>" for v in row.values)
        rows_html += f"<tr>{cells}</tr>"
    headers = "".join(f"<th>{col}</th>" for col in df.columns)
    html = f"""{styles}<table class="ctable"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>"""
    st.markdown(html, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# 0. 頁面設定
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="永寬化學 ISO 50001",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="⚡"
)

st.markdown("""
<style>
  [data-testid="stSidebar"] { background:#1a3a5c !important; }
  [data-testid="stSidebar"] * { color:#e2e8f0 !important; }
  [data-testid="stSidebar"] .stButton button {
    background:#2563a8; color:#fff; border-radius:8px; border:none; width:100%;
  }
  .kpi {
    background:#fff; border-radius:12px; padding:14px 10px;
    box-shadow:0 1px 6px rgba(0,0,0,.08); text-align:center;
    min-width:0; overflow:hidden;
  }
  .kpi-v { font-size:22px; font-weight:800; color:#1a3a5c;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .kpi-l { font-size:11px; color:#64748b; margin-top:4px;
            line-height:1.3; word-break:keep-all; }
  .mode-edit {
    background:#fef3c7; border-left:5px solid #f59e0b;
    padding:12px 16px; border-radius:6px; color:#92400e; font-weight:600;
  }
  .mode-view {
    background:#eff6ff; border-left:5px solid #2563a8;
    padding:12px 16px; border-radius:6px; color:#1e40af; font-size:14px;
  }
  /* 表格文字置中 */
  [data-testid="stDataFrame"] td,
  [data-testid="stDataFrame"] th {
    text-align: center !important;
    justify-content: center !important;
  }
  [data-testid="stDataFrame"] [data-testid="glideDataEditor"] .dvn-stack {
    justify-content: center !important;
  }
  .dvn-stack span { text-align: center !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# 1. 常數
# ─────────────────────────────────────────────────────────────────────────────
def _get_admin_password():
    """優先從 Streamlit Secrets 讀取管理員密碼（.streamlit/secrets.toml 或
    Streamlit Cloud 的 Secrets 設定裡寫 ADMIN_PASSWORD = "你的密碼"），
    避免密碼明碼寫死在原始碼、進了 Git 版本紀錄就外洩。
    找不到 Secrets 設定時，退回程式內建的預設密碼（僅供本機測試，正式環境請務必改用 Secrets）。"""
    try:
        return st.secrets["ADMIN_PASSWORD"]
    except Exception:
        return "yk50001"   # ← 本機測試用預設密碼，正式部署請改在 Secrets 設定

ADMIN_PASSWORD = _get_admin_password()
TOTAL_KWH      = 1_911_641
FLOOR_AREA     = 12_378.27
DB_JSON        = "equipment_db.json"
ENB_JSON       = "enb_data.json"      # ← 能源基線追蹤資料存檔
LAYOUT_JSON    = "layout_settings.json"   # ← 版面格式設定存檔
ACTIVITY_LOG_JSON = "activity_log.json"   # ← 操作日誌存檔
BUILTIN_DATA_JSON = "builtin_equipment_data.json"  # ← 內建示範設備資料（含照片），與 app.py 分離存放

def _find_excel_file(keyword, fallback):
    """在目前工作目錄尋找檔名內含 keyword 的 .xlsx 檔（自動忽略 Excel 開啟時產生的
    ~$ 暫存鎖定檔）。因為實際檔案常有日期前綴（如「2025_12_15-XXX.xlsx」），
    用關鍵字比對比要求檔名完全一致更穩妥。

    若有多個符合，優先依『檔名中的日期』挑最新的一個（例如 2026_01_10 排在 2025_12_15 前面）；
    這比用檔案的『最後修改時間』可靠——因為部署在 Streamlit Cloud 時，
    每次重新部署都是重新 git checkout 整個專案，所有檔案的修改時間會被重設成同一個時間點，
    用修改時間排序在雲端環境會抓不準誰是真正比較新的檔案。
    完全找不到的話，回傳 fallback（維持原行為，後續程式用 os.path.exists 判斷即可）。"""
    try:
        candidates = [c for c in glob.glob(f"*{keyword}*.xlsx")
                      if not os.path.basename(c).startswith("~$")]
        if not candidates:
            return fallback

        def _sort_key(path):
            name = os.path.basename(path)
            m = re.search(r"(\d{4})[_-](\d{1,2})[_-](\d{1,2})", name)
            if m:
                y, mo, d = (int(x) for x in m.groups())
                return (1, y, mo, d)          # 檔名含日期：依日期排序，越新越前面
            return (0, 0, 0, 0)                # 檔名沒有日期：視為最舊，排在有日期的檔案之後
        candidates.sort(key=_sort_key, reverse=True)
        return candidates[0]
    except Exception:
        pass
    return fallback

# 這兩個檔名關鍵字要對應您實際的 Excel 檔名（不含日期前綴的部分即可）
EXCEL_FILE     = _find_excel_file("重大能源使用設備評估表", "重大能源使用設備評估表.xlsx")
ENB_EXCEL_FILE = _find_excel_file("各項能源基線追蹤表",     "各項能源基線追蹤表.xlsx")

SYSTEM_SHEETS = {
    "空壓系統": "表4-1、空壓系統",
    "空調系統": "表4-2、空調系統",
    "照明系統": "表4-3、照明系統",
    "製程系統": "表4-4、製程系統",
    "其他系統": "表4-5、其他系統",
}
SYSTEM_ICONS = {
    "空壓系統":"💨","空調系統":"❄️",
    "照明系統":"💡","製程系統":"⚙️","其他系統":"🔧",
}

# 能源基線追蹤（EnB）資料位於「另一份」Excel（ENB_EXCEL_FILE），
# 「每月單位產量耗能」與「整廠用電量」兩張表實際上疊放在同一個分頁裡，
# 分頁名稱含「全廠」與「能源基線」字樣（例如「2025年能源績效指標與能源基線-全廠」），
# 程式會自動抓最新年度、名稱最相符的分頁，不用每年手動改分頁名稱。


# ─────────────────────────────────────────────────────────────────────────────
# 2. ISO 50001 計算公式
# ─────────────────────────────────────────────────────────────────────────────
def score_consumption(kwh):
    if kwh < 2500:   return 1
    elif kwh < 5500: return 2
    elif kwh < 7500: return 3
    elif kwh < 10000: return 4
    else:            return 5

def score_power(kw):
    if kw < 2.5:   return 1
    elif kw < 5.0: return 2
    elif kw < 7.5: return 3
    elif kw < 9.0: return 4
    else:          return 5

def calc_row(rec):
    try:
        kw   = float(rec.get("消耗功率(kW)") or 0)
        load = float(rec.get("負載率") or 0)
        hrs  = float(rec.get("運轉時數(hr/年)") or 0)
        qty  = float(rec.get("設備數量") or 1)
        crit = float(rec.get("自評重大性") or 3)
        kwh  = kw * load * hrs * qty
        sc   = round(score_consumption(kwh)*0.3 + score_power(kw)*0.4 + crit*0.3, 2)
        seu  = "A" if sc >= 4.0 else "-"
        return kwh, sc, seu
    except:
        return 0.0, 0.0, "-"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Excel 讀取
# ─────────────────────────────────────────────────────────────────────────────
def _sf(v):
    try:
        if v is None: return None
        if isinstance(v, float) and math.isnan(v): return None
        return float(v)
    except:
        return None

def _si(v):
    """安全轉整數（給 Supabase 的 integer 欄位用，例如 install_year／age_years）。
    PostgREST 對 integer 欄位很嚴格，收到帶小數點的數字（如 18.0）會直接報錯，
    必須送真正的整數，不能送浮點數。"""
    try:
        if v is None: return None
        if isinstance(v, float) and math.isnan(v): return None
        return int(float(v))
    except:
        return None

def read_system(sheet, sys_label):
    try:
        df = pd.read_excel(EXCEL_FILE, sheet_name=sheet, header=None)
    except Exception as e:
        st.warning(f"無法讀取工作表 {sheet}：{e}")
        return []

    lit   = "照明" in sheet
    kw_c  = 9  if lit else 7
    qty_c = 10 if lit else 8
    ld_c  = 11 if lit else 9
    hrs_c = 13 if lit else 11
    yr_c  = 14 if lit else 12
    age_c = 15 if lit else 13
    cr_c  = 20 if lit else 18

    skip = {
        '', 'nan', '設備名稱', '設備總耗電量', 'A級設備耗電量',
        'A', 'I', '設備總耗能量', 'A級設備耗能量'
    }

    def g(row, c):
        if c >= len(row): return None
        v = row.iloc[c]
        if v is None: return None
        try:
            if isinstance(v, float) and math.isnan(v): return None
        except: pass
        return v

    recs = []
    for i, row in df.iterrows():
        if i < 2: continue
        name = row.iloc[1] if len(row) > 1 else None
        if name is None: continue
        try:
            if isinstance(name, float) and math.isnan(name): continue
        except: pass
        if str(name).strip() in skip: continue

        recs.append({
            "系統別":         sys_label,
            "設備名稱":       str(g(row, 1) or ""),
            "設備編號":       str(g(row, 2) or ""),
            "設備型式":       str(g(row, 3) or ""),
            "設備部門":       str(g(row, 6 if lit else 4) or ""),
            "所在棟別":       str(g(row, 7 if lit else 5) or ""),
            "所在樓層":       str(g(row, 8 if lit else 6) or ""),
            "消耗功率(kW)":   _sf(g(row, kw_c)),
            "設備數量":       _sf(g(row, qty_c)),
            "負載率":         _sf(g(row, ld_c)),
            "運轉時數(hr/年)": _sf(g(row, hrs_c)),
            "設備年份":       _sf(g(row, yr_c)),
            "使用年數":       _sf(g(row, age_c)),
            "自評重大性":     _sf(g(row, cr_c)),
            "設備管理者":     str(g(row, 34) or ""),
            "外包商承攬商":   str(g(row, 35 if not lit else 37) or ""),
            "相關變數":       str(g(row, 36 if not lit else 38) or ""),
            "外觀照片":       None,
            "銘牌照片":       None,
        })
    return recs

# ─────────────────────────────────────────────────────────────────────────────
# 3-1. 能源基線追蹤（EnB）Excel 匯入
#      設計原則：用「關鍵字」比對 B 欄文字內容來定位資料列，不依賴固定的列號，
#      這樣即使日後在 Excel 裡插入/刪除列、或年度字樣從「114年」變成「115年」，
#      只要關鍵字還在，程式仍然讀得到正確的資料。
# ─────────────────────────────────────────────────────────────────────────────
def _find_label_rows(df, keyword, col=1, exclude=None):
    """在指定欄（預設 B 欄，index=1）中尋找所有『內含』keyword 的列，依序回傳列索引清單"""
    exclude = exclude or []
    idxs = []
    for i in range(len(df)):
        v = df.iloc[i, col] if col < df.shape[1] else None
        if v is None:
            continue
        s = str(v).strip()
        if s and keyword in s and not any(ex in s for ex in exclude):
            idxs.append(i)
    return idxs

def _find_cell(df, text):
    """尋找內容『內含』text 的第一個儲存格，回傳 (row, col)；找不到回傳 None"""
    for r in range(len(df)):
        for c in range(df.shape[1]):
            v = df.iloc[r, c]
            if v is not None and text in str(v):
                return (r, c)
    return None

def _read_month_row(df, row_idx, col_start=2, n=12):
    """從指定列讀出連續 n 個月份的數值（欄位從 col_start，預設 C 欄=index 2 起算）"""
    vals = []
    for i in range(n):
        c = col_start + i
        v = df.iloc[row_idx, c] if (row_idx is not None and c < df.shape[1]) else None
        vals.append(_sf(v))
    return vals

def _read_diff_table(df):
    """讀取『差異分析』附表（月份／原因／預防處置），回傳 (reason[12], action[12])；找不到回傳 (None, None)"""
    pos = _find_cell(df, "原因")
    if not pos:
        return None, None
    r0, c_reason = pos
    c_action = None
    for c in range(df.shape[1]):
        v = df.iloc[r0, c]
        if v is not None and "預防" in str(v):
            c_action = c
            break
    if c_action is None:
        return None, None
    reasons, actions = [], []
    for i in range(12):
        rr = r0 + 1 + i
        rv = df.iloc[rr, c_reason] if rr < len(df) else None
        av = df.iloc[rr, c_action] if rr < len(df) else None
        reasons.append(str(rv).strip() if (rv is not None and pd.notna(rv)) else "")
        actions.append(str(av).strip() if (av is not None and pd.notna(av)) else "")
    return reasons, actions

def _load_enb_sheet():
    """載入能源基線追蹤 Excel 中，名稱同時含「全廠」與「能源基線」字樣的分頁
    （例如「2025年能源績效指標與能源基線-全廠」），回傳 (df, error_msg)。
    若有多個年度的分頁同時存在，取名稱排序最後者（通常代表最新年度）。"""
    if not os.path.exists(ENB_EXCEL_FILE):
        return None, f"找不到能源基線追蹤 Excel 檔案（預期檔名內含「各項能源基線追蹤表」，目前查無此檔案）"
    try:
        xls = pd.ExcelFile(ENB_EXCEL_FILE)
        matches = [s for s in xls.sheet_names if ("全廠" in s and "能源基線" in s)]
        if not matches:
            return None, f"在「{ENB_EXCEL_FILE}」中找不到名稱同時含「全廠」與「能源基線」字樣的分頁"
        matches.sort(reverse=True)   # 年度數字較大（較新）排前面
        df = pd.read_excel(ENB_EXCEL_FILE, sheet_name=matches[0], header=None)
        return df, None
    except Exception as e:
        return None, f"讀取「{ENB_EXCEL_FILE}」時發生錯誤：{e}"

def read_enb_monthly_unit_from_excel():
    """從 Excel 讀取『每月單位產量耗能』能源基線追蹤表，回傳 (data_dict, error_msg)"""
    df, err = _load_enb_sheet()
    if err:
        return None, err

    # 「全廠」分頁裡『單位產量耗能』與『整廠用電量』兩張表疊在一起，
    # 以「整廠用電量」標題所在列為界，只在它上面的區塊裡找單位產量耗能的資料，
    # 避免兩張表都有的「標準基線」「調整時機」等關鍵字互相搶到錯誤的列。
    split_pos = _find_cell(df, "整廠用電量")
    unit_df = df.iloc[:split_pos[0]].reset_index(drop=True) if split_pos else df

    kwh_rows  = _find_label_rows(unit_df, "用電量", exclude=["指標", "績效"])
    prod_rows = _find_label_rows(unit_df, "產量", exclude=["指標", "績效"])
    std_rows  = _find_label_rows(unit_df, "標準基線", exclude=["指標", "績效"])
    adj_rows  = _find_label_rows(unit_df, "調整時機", exclude=["指標", "績效"])
    note_rows = _find_label_rows(unit_df, "備註", exclude=["指標", "績效"])

    if not (kwh_rows and prod_rows and std_rows and adj_rows):
        return None, ("找不到「每月單位產量耗能」表的必要資料列（用電量／產量／標準基線／調整時機），"
                       "請確認分頁格式是否與範本一致。")

    adj_upper_row = adj_rows[0]
    adj_lower_row = adj_upper_row + 1  # 下限通常緊接在上限列下方、沒有文字標籤

    data = {
        "kwh":        _read_month_row(unit_df, kwh_rows[0]),
        "production": _read_month_row(unit_df, prod_rows[0]),
        "std":        _read_month_row(unit_df, std_rows[0]),
        "adj_upper":  _read_month_row(unit_df, adj_upper_row),
        "adj_lower":  _read_month_row(unit_df, adj_lower_row),
        "note": (
            [str(v).strip() if (v is not None and pd.notna(v)) else ""
             for v in [unit_df.iloc[note_rows[0], 2 + i] if (2 + i) < unit_df.shape[1] else None for i in range(12)]]
            if note_rows else [""] * 12
        ),
    }
    reasons, actions = _read_diff_table(unit_df)
    data["reason"] = reasons if reasons else [""] * 12
    data["action"] = actions if actions else [""] * 12
    return data, None

def read_enb_plant_from_excel():
    """從 Excel 讀取『整廠用電量』能源基線追蹤表，回傳 (data_dict, error_msg)"""
    df, err = _load_enb_sheet()
    if err:
        return None, err

    split_pos = _find_cell(df, "整廠用電量")
    if not split_pos:
        return None, "在能源基線追蹤分頁中找不到「整廠用電量」子表標題"
    plant_df = df.iloc[split_pos[0]:].reset_index(drop=True)

    actual_rows = _find_label_rows(plant_df, "整廠電量", exclude=["標準基線", "調整", "指標", "績效"])
    base_rows   = _find_label_rows(plant_df, "標準基線", exclude=["指標", "績效"])
    adj_rows    = _find_label_rows(plant_df, "調整時機", exclude=["指標", "績效"])

    if not (actual_rows and base_rows and adj_rows):
        return None, ("找不到「整廠用電量」表的必要資料列（整廠電量／標準基線／調整時機），"
                       "請確認分頁格式是否與範本一致（年度字樣如「114年」可自行更動，"
                       "只要保留「整廠電量」「標準基線」「調整時機」等關鍵字即可）。")

    adj_upper_row = adj_rows[0]
    adj_lower_row = adj_upper_row + 1  # 下限通常緊接在上限列下方、沒有文字標籤

    data = {
        "actual":        _read_month_row(plant_df, actual_rows[0]),
        "baseline_prev": _read_month_row(plant_df, base_rows[0]),
        "adj_upper":     _read_month_row(plant_df, adj_upper_row),
        "adj_lower":     _read_month_row(plant_df, adj_lower_row),
    }
    reasons, actions = _read_diff_table(plant_df)
    data["reason"] = reasons if reasons else [""] * 12
    data["action"] = actions if actions else [""] * 12
    return data, None

# ─────────────────────────────────────────────────────────────────────────────
# 3-2. 能源基線追蹤（EnB）－重大能源使用設備（個別設備，如研磨機、大型攪拌機）
#      這些子表是「動態偵測」的：只要 Excel 分頁裡有一個標題列符合
#      「20XX年能源績效指標-OOO」的格式，程式就會自動當成一組新的追蹤子表來讀取，
#      不需要在程式碼裡逐一寫死設備名稱；日後在 Excel 裡新增第三、第四台設備的
#      追蹤表，只要照相同版面（標題／月份／分子／分母／標準基線／調整時機）填寫，
#      系統就會自動抓到。
# ─────────────────────────────────────────────────────────────────────────────
def _find_block_title_rows(df, keyword="能源績效指標"):
    """尋找子表標題列（例如：2025年能源績效指標-大型攪拌機系統單位產量耗能），
    回傳 [(列索引, 完整標題文字, 擷取出的設備／子表名稱), ...]"""
    blocks = []
    for i in range(len(df)):
        v = df.iloc[i, 1] if df.shape[1] > 1 else None
        if v is None or not pd.notna(v):
            continue
        s = str(v).strip()
        if keyword in s:
            m = re.search(r"能源績效指標-(.+)", s)
            name = m.group(1).strip() if m else s
            blocks.append((i, s, name))
    return blocks

def _extract_month_header(df, row_idx, col_start=2):
    """從『月份』標頭列讀出實際出現的月份數字（如 [4,5,6,7,8,9,10,11,12]），
    遇到非『N月』格式或空白儲存格就停止"""
    months = []
    c = col_start
    while c < df.shape[1]:
        v = df.iloc[row_idx, c]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            break
        m = re.match(r"^(\d{1,2})月$", str(v).strip())
        if not m:
            break
        months.append(int(m.group(1)))
        c += 1
    return months

def _read_values_by_count(df, row_idx, n, col_start=2):
    """讀出從 col_start 起連續 n 個儲存格的數值"""
    vals = []
    for i in range(n):
        c = col_start + i
        v = df.iloc[row_idx, c] if (row_idx is not None and c < df.shape[1]) else None
        vals.append(_sf(v))
    return vals

def _read_equipment_diff_table(df):
    """讀取子表自己的差異分析附表，回傳 {月份數字: {"reason":.., "action":..}}"""
    pos = _find_cell(df, "原因")
    if not pos:
        return {}
    r0, c_reason = pos
    c_month = c_action = None
    for c in range(df.shape[1]):
        v = df.iloc[r0, c]
        if v is None:
            continue
        if c_month is None and "月份" in str(v):
            c_month = c
        if c_action is None and "預防" in str(v):
            c_action = c
    if c_month is None or c_action is None:
        return {}
    result = {}
    r = r0 + 1
    while r < len(df):
        mv = df.iloc[r, c_month]
        if mv is None or (isinstance(mv, float) and pd.isna(mv)):
            break
        m = re.match(r"^(\d{1,2})月$", str(mv).strip())
        if not m:
            break
        rv, av = df.iloc[r, c_reason], df.iloc[r, c_action]
        result[int(m.group(1))] = {
            "reason": str(rv).strip() if (rv is not None and pd.notna(rv)) else "",
            "action": str(av).strip() if (av is not None and pd.notna(av)) else "",
        }
        r += 1
    return result

def read_enb_equipment_from_excel():
    """從能源基線追蹤 Excel 讀取『重大能源使用設備』分頁裡所有動態偵測到的子表，
    回傳 (equipment_list, error_msg)"""
    if not os.path.exists(ENB_EXCEL_FILE):
        return None, f"找不到能源基線追蹤 Excel 檔案（預期檔名內含「各項能源基線追蹤表」，目前查無此檔案）"
    try:
        xls = pd.ExcelFile(ENB_EXCEL_FILE)
        matches = [s for s in xls.sheet_names if "重大能源使用設備" in s]
        if not matches:
            return None, f"在「{ENB_EXCEL_FILE}」中找不到名稱含「重大能源使用設備」字樣的分頁"
        matches.sort(reverse=True)
        df = pd.read_excel(ENB_EXCEL_FILE, sheet_name=matches[0], header=None)
    except Exception as e:
        return None, f"讀取「{ENB_EXCEL_FILE}」時發生錯誤：{e}"

    blocks = _find_block_title_rows(df)
    if not blocks:
        return None, f"在「{matches[0]}」分頁中找不到任何子表標題（預期格式如「20XX年能源績效指標-OOO」）"

    equipment_list = []
    for i, (row_idx, full_title, name) in enumerate(blocks):
        end_row = blocks[i + 1][0] if i + 1 < len(blocks) else len(df)
        block_df = df.iloc[row_idx:end_row].reset_index(drop=True)

        month_rows = _find_label_rows(block_df, "月份")
        if not month_rows:
            continue
        months = _extract_month_header(block_df, month_rows[0])
        if not months:
            continue
        n = len(months)

        numer_row, denom_row = month_rows[0] + 1, month_rows[0] + 2
        numer_label = (str(block_df.iloc[numer_row, 1]).strip()
                       if numer_row < len(block_df) and pd.notna(block_df.iloc[numer_row, 1]) else "數值1")
        denom_label = (str(block_df.iloc[denom_row, 1]).strip()
                       if denom_row < len(block_df) and pd.notna(block_df.iloc[denom_row, 1]) else "數值2")

        std_rows = _find_label_rows(block_df, "標準基線")
        adj_rows = _find_label_rows(block_df, "調整時機")

        equipment_list.append({
            "title":            name,
            "numerator_label":  numer_label,
            "denominator_label": denom_label,
            "months":           months,
            "numerator":        _read_values_by_count(block_df, numer_row, n),
            "denominator":      _read_values_by_count(block_df, denom_row, n),
            "std":              _read_values_by_count(block_df, std_rows[0], n) if std_rows else [None] * n,
            "adj_upper":        _read_values_by_count(block_df, adj_rows[0], n) if adj_rows else [None] * n,
            "adj_lower":        _read_values_by_count(block_df, adj_rows[0] + 1, n) if adj_rows else [None] * n,
            "diff":             _read_equipment_diff_table(block_df),
        })

    if not equipment_list:
        return None, f"在「{matches[0]}」分頁中找到子表標題，但都無法解析出月份／數值資料，請確認版面格式。"
    return equipment_list, None

# ─────────────────────────────────────────────────────────────────────────────
# 3-2. Supabase 雲端同步（取代/輔助本地 JSON 檔案，解決 Streamlit Cloud
#      重新部署後資料被清空的問題）
#      設計原則：
#      - 完全「選用」：沒有在 Streamlit Secrets 設定 SUPABASE_URL／
#        SUPABASE_SERVICE_KEY 的話，以下所有函式都會安靜地回傳失敗，
#        程式會自動退回原本的本地 JSON 檔案，不會讓 app 壞掉。
#      - 用 service_role 金鑰連線（在伺服器端執行，不會外流到瀏覽器），
#        所以資料表的 Row Level Security 直接關閉，權限控管交給
#        app.py 自己的管理員密碼機制。
#      - 照片不存進資料庫欄位（會讓資料庫肥大、逼近 500MB 上限），
#        而是上傳到 Supabase Storage 的 equipment-photos bucket，
#        資料庫欄位只存檔案路徑。
# ─────────────────────────────────────────────────────────────────────────────
_SUPABASE_CLIENT = None
_SUPABASE_TRIED = False

def get_supabase_client():
    """回傳 Supabase client；沒有設定 Secrets 或連線失敗就回傳 None
    （呼叫端要自己處理 None 的情況，退回本地檔案）。同一次執行只嘗試建立一次連線。"""
    global _SUPABASE_CLIENT, _SUPABASE_TRIED
    if _SUPABASE_TRIED:
        return _SUPABASE_CLIENT
    _SUPABASE_TRIED = True
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_KEY"]
        from supabase import create_client
        _SUPABASE_CLIENT = create_client(url, key)
    except Exception:
        _SUPABASE_CLIENT = None
    return _SUPABASE_CLIENT

SUPABASE_PHOTO_BUCKET = "equipment-photos"

def _sb_upload_photo(sb, b64_str, path):
    """把 base64 照片上傳到 Storage，回傳存進資料庫欄位用的路徑字串；沒有照片就回傳 None"""
    if not b64_str:
        return None
    try:
        raw = base64.b64decode(b64_str)
        sb.storage.from_(SUPABASE_PHOTO_BUCKET).upload(
            path, raw, {"content-type": "image/jpeg", "upsert": "true"}
        )
        return path
    except Exception:
        return None

def _sb_download_photo(sb, path):
    """從 Storage 下載照片，回傳 base64 字串；找不到就回傳 None"""
    if not path:
        return None
    try:
        raw = sb.storage.from_(SUPABASE_PHOTO_BUCKET).download(path)
        return base64.b64encode(raw).decode()
    except Exception:
        return None

# ── 設備盤查資料（equipment 資料表） ────────────────────────────────────────
def push_equipment_to_supabase(records):
    """把目前的設備清單（含照片）整批上傳到 Supabase，回傳 (成功與否, 訊息)"""
    sb = get_supabase_client()
    if not sb:
        return False, "尚未設定 Supabase 連線資訊（SUPABASE_URL／SUPABASE_SERVICE_KEY）"
    try:
        rows = []
        for i, rec in enumerate(records):
            code = str(rec.get("設備編號") or f"NOID_{i}").strip() or f"NOID_{i}"
            appearance_path = _sb_upload_photo(sb, rec.get("外觀照片"), f"{code}_appearance.jpg")
            nameplate_path  = _sb_upload_photo(sb, rec.get("銘牌照片"),  f"{code}_nameplate.jpg")
            rows.append({
                "system_name": rec.get("系統別"), "equipment_name": rec.get("設備名稱"),
                "equipment_code": code, "equipment_type": rec.get("設備型式"),
                "department": rec.get("設備部門"), "building": rec.get("所在棟別"),
                "floor": rec.get("所在樓層"), "power_kw": _sf(rec.get("消耗功率(kW)")),
                "quantity": _sf(rec.get("設備數量")), "load_rate": _sf(rec.get("負載率")),
                "operating_hours": _sf(rec.get("運轉時數(hr/年)")),
                "install_year": _si(rec.get("設備年份")), "age_years": _si(rec.get("使用年數")),
                "criticality": _sf(rec.get("自評重大性")), "manager": rec.get("設備管理者"),
                "contractor": rec.get("外包商承攬商"), "related_vars": rec.get("相關變數"),
                "appearance_photo_path": appearance_path, "nameplate_photo_path": nameplate_path,
            })
        # 用「整批清空重寫」而非逐列比對更新，邏輯最單純可靠，335 筆規模也很快
        sb.table("equipment").delete().neq("id", -1).execute()
        batch = 100
        for i in range(0, len(rows), batch):
            sb.table("equipment").insert(rows[i:i+batch]).execute()
        return True, f"已上傳 {len(rows)} 筆設備資料到 Supabase"
    except Exception as e:
        return False, f"上傳失敗：{e}"

def pull_equipment_from_supabase():
    """從 Supabase 下載設備清單（含照片），回傳 (records 或 None, 錯誤訊息)"""
    sb = get_supabase_client()
    if not sb:
        return None, "尚未設定 Supabase 連線資訊"
    try:
        resp = sb.table("equipment").select("*").execute()
        rows = resp.data or []
        if not rows:
            return None, "Supabase 的 equipment 資料表目前是空的"
        records = []
        for row in rows:
            records.append({
                "系統別": row.get("system_name"), "設備名稱": row.get("equipment_name"),
                "設備編號": row.get("equipment_code"), "設備型式": row.get("equipment_type"),
                "設備部門": row.get("department"), "所在棟別": row.get("building"),
                "所在樓層": row.get("floor"), "消耗功率(kW)": row.get("power_kw"),
                "設備數量": row.get("quantity"), "負載率": row.get("load_rate"),
                "運轉時數(hr/年)": row.get("operating_hours"), "設備年份": row.get("install_year"),
                "使用年數": row.get("age_years"), "自評重大性": row.get("criticality"),
                "設備管理者": row.get("manager"), "外包商承攬商": row.get("contractor"),
                "相關變數": row.get("related_vars"),
                "外觀照片": _sb_download_photo(sb, row.get("appearance_photo_path")),
                "銘牌照片": _sb_download_photo(sb, row.get("nameplate_photo_path")),
            })
        return records, None
    except Exception as e:
        return None, f"下載失敗：{e}"

# ── 能源基線追蹤－每月單位產量耗能 ──────────────────────────────────────────
def push_enb_monthly_unit_to_supabase(data, year=None):
    sb = get_supabase_client()
    if not sb:
        return False, "尚未設定 Supabase 連線資訊"
    year = year or datetime.now().year
    try:
        rows = []
        for i, m in enumerate(range(1, 13)):
            rows.append({
                "year": year, "month": m,
                "kwh": data["kwh"][i], "production": data["production"][i],
                "std_baseline": data["std"][i], "adj_upper": data["adj_upper"][i],
                "adj_lower": data["adj_lower"][i], "note": data["note"][i],
                "reason": data["reason"][i], "action": data["action"][i],
            })
        sb.table("enb_monthly_unit").upsert(rows, on_conflict="year,month").execute()
        return True, "已上傳「單位產量耗能」到 Supabase"
    except Exception as e:
        return False, f"上傳失敗：{e}"

def pull_enb_monthly_unit_from_supabase(year=None):
    sb = get_supabase_client()
    if not sb:
        return None, "尚未設定 Supabase 連線資訊"
    year = year or datetime.now().year
    try:
        resp = sb.table("enb_monthly_unit").select("*").eq("year", year).order("month").execute()
        rows = resp.data or []
        if not rows:
            return None, f"Supabase 裡找不到 {year} 年的單位產量耗能資料"
        by_month = {r["month"]: r for r in rows}
        data = {"kwh": [], "production": [], "std": [], "adj_upper": [], "adj_lower": [],
                "note": [], "reason": [], "action": []}
        for m in range(1, 13):
            r = by_month.get(m, {})
            data["kwh"].append(r.get("kwh")); data["production"].append(r.get("production"))
            data["std"].append(r.get("std_baseline")); data["adj_upper"].append(r.get("adj_upper"))
            data["adj_lower"].append(r.get("adj_lower")); data["note"].append(r.get("note") or "")
            data["reason"].append(r.get("reason") or ""); data["action"].append(r.get("action") or "")
        return data, None
    except Exception as e:
        return None, f"下載失敗：{e}"

# ── 能源基線追蹤－整廠用電量 ────────────────────────────────────────────────
def push_enb_plant_to_supabase(data, year=None):
    sb = get_supabase_client()
    if not sb:
        return False, "尚未設定 Supabase 連線資訊"
    year = year or datetime.now().year
    try:
        rows = []
        for i, m in enumerate(range(1, 13)):
            rows.append({
                "year": year, "month": m,
                "actual_kwh": data["actual"][i], "baseline_prev_kwh": data["baseline_prev"][i],
                "adj_upper": data["adj_upper"][i], "adj_lower": data["adj_lower"][i],
                "reason": data["reason"][i], "action": data["action"][i],
            })
        sb.table("enb_plant").upsert(rows, on_conflict="year,month").execute()
        return True, "已上傳「整廠用電量」到 Supabase"
    except Exception as e:
        return False, f"上傳失敗：{e}"

def pull_enb_plant_from_supabase(year=None):
    sb = get_supabase_client()
    if not sb:
        return None, "尚未設定 Supabase 連線資訊"
    year = year or datetime.now().year
    try:
        resp = sb.table("enb_plant").select("*").eq("year", year).order("month").execute()
        rows = resp.data or []
        if not rows:
            return None, f"Supabase 裡找不到 {year} 年的整廠用電量資料"
        by_month = {r["month"]: r for r in rows}
        data = {"actual": [], "baseline_prev": [], "adj_upper": [], "adj_lower": [],
                "reason": [], "action": []}
        for m in range(1, 13):
            r = by_month.get(m, {})
            data["actual"].append(r.get("actual_kwh")); data["baseline_prev"].append(r.get("baseline_prev_kwh"))
            data["adj_upper"].append(r.get("adj_upper")); data["adj_lower"].append(r.get("adj_lower"))
            data["reason"].append(r.get("reason") or ""); data["action"].append(r.get("action") or "")
        return data, None
    except Exception as e:
        return None, f"下載失敗：{e}"

# ── 能源基線追蹤－重大能源使用設備（動態子表） ──────────────────────────────
def push_enb_equipment_to_supabase(equipment_list):
    sb = get_supabase_client()
    if not sb:
        return False, "尚未設定 Supabase 連線資訊"
    try:
        rows = []
        for eq in equipment_list:
            diff = eq.get("diff", {})
            for i, m in enumerate(eq["months"]):
                d = diff.get(m, {}) if isinstance(diff, dict) else diff.get(str(m), {})
                rows.append({
                    "title": eq["title"], "numerator_label": eq["numerator_label"],
                    "denominator_label": eq["denominator_label"], "month": m,
                    "numerator": eq["numerator"][i], "denominator": eq["denominator"][i],
                    "std_baseline": eq["std"][i], "adj_upper": eq["adj_upper"][i],
                    "adj_lower": eq["adj_lower"][i],
                    "reason": d.get("reason", ""), "action": d.get("action", ""),
                })
        if not rows:
            return False, "目前沒有重大設備子表資料可以上傳"
        sb.table("enb_equipment").delete().neq("id", -1).execute()
        batch = 200
        for i in range(0, len(rows), batch):
            sb.table("enb_equipment").insert(rows[i:i+batch]).execute()
        return True, f"已上傳 {len(equipment_list)} 組子表到 Supabase"
    except Exception as e:
        return False, f"上傳失敗：{e}"

def pull_enb_equipment_from_supabase():
    sb = get_supabase_client()
    if not sb:
        return None, "尚未設定 Supabase 連線資訊"
    try:
        resp = sb.table("enb_equipment").select("*").order("title").order("month").execute()
        rows = resp.data or []
        if not rows:
            return None, "Supabase 的 enb_equipment 資料表目前是空的"
        grouped = {}
        for r in rows:
            grouped.setdefault(r["title"], []).append(r)
        equipment_list = []
        for title, rlist in grouped.items():
            rlist.sort(key=lambda x: x["month"])
            equipment_list.append({
                "title": title,
                "numerator_label": rlist[0].get("numerator_label"),
                "denominator_label": rlist[0].get("denominator_label"),
                "months": [r["month"] for r in rlist],
                "numerator": [r.get("numerator") for r in rlist],
                "denominator": [r.get("denominator") for r in rlist],
                "std": [r.get("std_baseline") for r in rlist],
                "adj_upper": [r.get("adj_upper") for r in rlist],
                "adj_lower": [r.get("adj_lower") for r in rlist],
                "diff": {r["month"]: {"reason": r.get("reason", ""), "action": r.get("action", "")} for r in rlist},
            })
        return equipment_list, None
    except Exception as e:
        return None, f"下載失敗：{e}"

# ── 版面格式設定（單一列） ──────────────────────────────────────────────────
def push_layout_to_supabase(settings):
    sb = get_supabase_client()
    if not sb:
        return False
    try:
        sb.table("layout_settings").upsert({"id": 1, "settings": settings}, on_conflict="id").execute()
        return True
    except Exception:
        return False

def pull_layout_from_supabase():
    sb = get_supabase_client()
    if not sb:
        return None
    try:
        resp = sb.table("layout_settings").select("*").eq("id", 1).execute()
        if resp.data:
            return resp.data[0].get("settings")
    except Exception:
        pass
    return None

# ── 操作日誌 ────────────────────────────────────────────────────────────────
def push_activity_log_entry_to_supabase(action, detail):
    sb = get_supabase_client()
    if not sb:
        return
    try:
        sb.table("activity_log").insert({"action": action, "detail": detail}).execute()
    except Exception:
        pass

def pull_activity_log_from_supabase(limit=500):
    sb = get_supabase_client()
    if not sb:
        return None
    try:
        resp = (sb.table("activity_log").select("*")
                .order("logged_at", desc=True).limit(limit).execute())
        logs = [{"time": r.get("logged_at"), "action": r.get("action"), "detail": r.get("detail")}
                for r in (resp.data or [])]
        return logs[::-1]
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# 4. 持久化
# ─────────────────────────────────────────────────────────────────────────────
def load_json():
    if os.path.exists(DB_JSON):
        try:
            with open(DB_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return None

def save_json(data):
    with open(DB_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)

# ─────────────────────────────────────────────────────────────────────────────
# 3-0. 版面格式設定與操作日誌持久化
# ─────────────────────────────────────────────────────────────────────────────
def load_layout():
    if os.path.exists(LAYOUT_JSON):
        try:
            with open(LAYOUT_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return None

def save_layout(data):
    try:
        with open(LAYOUT_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
    push_layout_to_supabase(data)   # 有設定 Supabase 就順便同步；沒設定會安靜地跳過

def log_activity(action, detail=""):
    """記錄一筆操作日誌（誰／何時／做了什麼），存到本地 JSON，並在有設定 Supabase 時
    也順便寫一份過去，這樣重新部署後操作歷史也不會不見。
    因為系統目前是共用管理員密碼、沒有個人帳號，"操作者"暫時只能記錄為「管理員」，
    未來若改成個人帳密登入，可以在這裡換成實際使用者名稱。"""
    try:
        logs = []
        if os.path.exists(ACTIVITY_LOG_JSON):
            with open(ACTIVITY_LOG_JSON, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "detail": detail,
        })
        logs = logs[-500:]   # 只保留最近 500 筆，避免檔案無限成長
        with open(ACTIVITY_LOG_JSON, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False)
    except Exception:
        pass
    push_activity_log_entry_to_supabase(action, detail)   # 有設定 Supabase 就順便同步

def load_activity_log():
    if os.path.exists(ACTIVITY_LOG_JSON):
        try:
            with open(ACTIVITY_LOG_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    from_supabase = pull_activity_log_from_supabase()
    return from_supabase if from_supabase else []

# ─────────────────────────────────────────────────────────────────────────────
# 3-1. 能源基線追蹤（EnB）持久化
# ─────────────────────────────────────────────────────────────────────────────
def load_enb():
    if os.path.exists(ENB_JSON):
        try:
            with open(ENB_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return None

def save_enb(data):
    with open(ENB_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    # 跟 save_layout()／log_activity() 一樣，有設定 Supabase 就順便同步一份。
    # 這裡原本漏掉這一步：使用者在網頁表單手動編輯 EnB 資料按「儲存」後，
    # 只會存進本機的 enb_data.json，並不會自動同步到 Supabase；
    # 如果之後重新部署，這些手動編輯的內容就會遺失（要等下次手動點同步按鈕才找得回來，
    # 但那個按鈕是從 Excel 讀取，不是讀回你剛剛手動編輯的內容）。加上這幾行之後，
    # 只要手動編輯後按儲存，就會跟等版面設定一樣自動備份到 Supabase，不用另外記得點同步。
    try:
        if data.get("monthly_unit"):
            push_enb_monthly_unit_to_supabase(data["monthly_unit"])
        if data.get("plant"):
            push_enb_plant_to_supabase(data["plant"])
        if data.get("equipment"):
            push_enb_equipment_to_supabase(data["equipment"])
    except Exception:
        pass

def get_default_enb():
    """兩張能源基線追蹤表的預設示範資料（可於修改模式覆蓋）"""
    return {
        # ── 每月單位產量耗能 ──────────────────────────────────
        "monthly_unit": {
            "kwh":        [97700, 95000, 110900, 119835, 142949, 143479,
                            153573, 148013, 146538, 137559, 87300, None],
            "production": [65.76, 107.78, 100.16, 104.39, 104.24, 103.73,
                            106.26, 80.68, 91.30, 116.40, 115.27, None],
            "std":        [1100, 1100, 1300, 1300, 1300, 1300,
                           1300, 1300, 1300, 1300, 1300, 1300],
            "adj_upper":  [1250, 1250, 1450, 1450, 1450, 1450,
                           1450, 1450, 1450, 1450, 1450, 1450],
            "adj_lower":  [950, 950, 1150, 1150, 1150, 1150,
                           1150, 1150, 1150, 1150, 1150, 1150],
            "note": ["", "", "調整能源基線，調整為1300", "4/29 一二廠太陽能開始自發自用",
                     "", "", "", "", "", "", "", ""],
            "reason": [
                "超出能源基線調整時機原因，1月有春節假期，工作天數減少，導致kWh/噸的數據上升。",
                "本月產能提升，冬季不會使用冷氣，整廠耗電量下降。",
                "本月整廠用電提升，冷氣開始在運轉",
                "本月整廠用電提升，冷氣開始在運轉",
                "", "", "",
                "訂單量減少，導致產量下降，加上夏季吹冷氣提高整廠用電",
                "訂單量減少，導致產量下降，加上夏季吹冷氣提高整廠用電",
                "",
                "冬季冷氣使用頻率下降，整廠耗電量下降",
                "",
            ],
            "action": [
                "kWh/噸超出能源基線是春節假期造成，無預防及處置。",
                "持續追蹤製程用電與產能比例變化",
                "調整能源基線，調整為1300",
                "持續追蹤製程用電與產能比例變化",
                "", "", "",
                "逐步汰換二廠定頻中央空調設備，改變頻一級能效分離式冷氣",
                "逐步汰換二廠定頻中央空調設備，改變頻一級能效分離式冷氣",
                "",
                "持續追蹤製程用電與產能比例變化",
                "",
            ],
        },
        # ── 整廠用電量 ────────────────────────────────────────
        "plant": {
            "actual":       [120789, 114250, 133786, 146089, 173437, 178333,
                              190733, 187579, 184269, 180551, None, None],
            "baseline_prev":[131702, 119864, 152710, 154370, 163450, 175676,
                              193083, 195537, 181038, 174478, 142999, 126734],
            "adj_upper":    [158042.4, 143836.8, 183252, 185244, 196140, 210811.2,
                              231699.6, 234644.4, 217245.6, 209373.6, 171598.8, 152080.8],
            "adj_lower":    [105361.6, 95891.2, 122168, 123496, 130760, 140540.8,
                              154466.4, 156429.6, 144830.4, 139582.4, 114399.2, 101387.2],
            "reason": [""] * 12,
            "action": [""] * 12,
        },
        # ── 重大能源使用設備（個別設備，動態偵測，預設空清單，請用 Excel 同步）──
        "equipment": [],
    }

def get_builtin_data():
    """全廠設備資料（含照片，打包時間：2026/08/17 11:55）
    共 335 台設備
    """
def get_builtin_data():
    """全廠設備資料（含照片）。實際資料存放在同目錄的 BUILTIN_DATA_JSON 檔，
    這裡只負責載入，避免把 335 筆設備、含照片的資料內嵌在原始碼裡造成檔案過於龐大、難以維護。
    找不到該檔案時回傳空清單，讓上層邏輯改用 Excel 或提示使用者上傳。"""
    if not os.path.exists(BUILTIN_DATA_JSON):
        return []
    try:
        with open(BUILTIN_DATA_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def init_from_excel():
    if os.path.exists(EXCEL_FILE):
        # 本機環境：讀取真實 Excel
        recs = []
        for s, sh in SYSTEM_SHEETS.items():
            recs.extend(read_system(sh, s))
        return recs if recs else get_builtin_data()
    else:
        # 雲端環境：使用內建示範資料
        return get_builtin_data()

# ─────────────────────────────────────────────────────────────────────────────
# 5. Session 初始化
# ─────────────────────────────────────────────────────────────────────────────
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "edit_mode" not in st.session_state:
    st.session_state["edit_mode"] = False
if "font_size" not in st.session_state:
    st.session_state["font_size"] = 14
if "table_align" not in st.session_state:
    st.session_state["table_align"] = "center"
# 各頁面格式設定
if "fmt" not in st.session_state:
    _saved_layout = load_layout() or pull_layout_from_supabase()
    st.session_state["fmt"] = _saved_layout if _saved_layout else {
        # 儀表板：標題(KPI數字/提示框數字/圖表標題) / 圖片字體(圖例/座標軸/數據標籤) / 表格
        "dash_title_size":  28,
        "dash_chart_font_size": 13,
        "dash_table_size":  14,
        "dash_table_align": "center",
        # 設備盤查：標題(展開卡標題/摘要卡片數字/設備明細數字) / 表格（無圖表）
        "equip_title_size": 14,
        "equip_table_size":  14,
        "equip_table_align":      "center",
        # 評分標準：標題(章節標題) / 表格（無圖表）
        "score_title_size": 18,
        "score_table_size": 14,
        "score_table_align":"center",
        # 能源換算：標題(KPI數字/圖表標題) / 圖片字體 / 表格
        "energy_title_size": 16,
        "energy_chart_font_size": 13,
        "energy_table_size": 14,
        "energy_table_align":"center",
        # 負載分析：標題(KPI數字/圖表標題) / 圖片字體 / 表格
        "load_title_size": 16,
        "load_chart_font_size": 13,
        "load_table_size":  14,
        "load_table_align": "center",
        # 能源基線追蹤（單位產量耗能／整廠用電量／重大設備）：標題(KPI數字/圖表標題) / 圖片字體 / 表格
        "enb_title_size": 16,
        "enb_chart_font_size": 13,
        "enb_table_size": 14,
        "enb_table_align": "center",
        # 全域字型
        "font_family": "Noto Sans TC",
    }
if "db" not in st.session_state:
    saved = load_json()
    if saved:
        st.session_state["db"] = saved
    else:
        # 本地檔案不存在（例如剛重新部署過）：先試著從 Supabase 抓回上次的資料，
        # 這是解決「重新部署後照片消失」問題的關鍵一步。
        from_supabase, _err = pull_equipment_from_supabase()
        if from_supabase:
            st.session_state["db"] = from_supabase
            save_json(from_supabase)   # 順便寫回本地，當作這次 session 的快取
        else:
            # 再退回 Excel 或內建示範資料
            builtin = get_builtin_data()
            st.session_state["db"] = builtin if builtin else init_from_excel()

if "enb" not in st.session_state:
    saved_enb = load_enb()
    if saved_enb:
        st.session_state["enb"] = saved_enb
    else:
        # 同樣先試著從 Supabase 抓回上次的資料
        default_enb = get_default_enb()
        mu, _ = pull_enb_monthly_unit_from_supabase()
        pl, _ = pull_enb_plant_from_supabase()
        eq, _ = pull_enb_equipment_from_supabase()
        default_enb["monthly_unit"] = mu if mu else default_enb["monthly_unit"]
        default_enb["plant"] = pl if pl else default_enb["plant"]
        default_enb["equipment"] = eq if eq else default_enb["equipment"]
        st.session_state["enb"] = default_enb
        if mu or pl or eq:
            save_enb(default_enb)
st.session_state["enb"].setdefault("equipment", [])   # 舊存檔相容：沒有這個欄位就補空清單

def all_calc():
    rows = []
    seen_keys = set()
    for rec in st.session_state["db"]:
        r = dict(rec)
        # 去除完全相同的重複記錄（相同系統+名稱+編號+棟別+樓層+部門+負載率+時數）
        dedup_key = (
            str(r.get("系統別","")), str(r.get("設備名稱","")),
            str(r.get("設備編號","")), str(r.get("所在棟別","")),
            str(r.get("所在樓層","")), str(r.get("設備部門","")),
            str(r.get("負載率","")), str(r.get("運轉時數(hr/年)","")),
            str(r.get("消耗功率(kW)",""))
        )
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        kwh, sc, seu = calc_row(r)
        r.update({"_kwh": kwh, "_sc": sc, "_seu": seu})
        rows.append(r)
    return rows

# ─────────────────────────────────────────────────────────────────────────────
# 6. Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:14px 0 10px'>
      <div style='font-size:34px'>⚡</div>
      <div style='font-size:16px;font-weight:800;color:#fff'>永寬化學</div>
      <div style='font-size:11px;color:#94a3b8;margin-top:2px'>ISO 50001 能源控制台</div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    st.markdown("**🔐 系統權限**")
    if not st.session_state["logged_in"]:
        st.caption("預設唯讀。輸入密碼解鎖修改功能。")
        pwd = st.text_input("管理員密碼", type="password", key="pwd")
        if st.button("🔓 驗證並解鎖", use_container_width=True):
            if pwd == ADMIN_PASSWORD:
                st.session_state["logged_in"] = True
                log_activity("登入", "管理員登入成功")
                st.rerun()
            else:
                log_activity("登入失敗", "密碼錯誤")
                st.error("密碼錯誤！")
    else:
        st.success("✅ 高級系統管理員")
        if st.button("🔒 登出並鎖定", use_container_width=True):
            st.session_state.update(logged_in=False, edit_mode=False)
            st.rerun()

    st.divider()

    if st.session_state["logged_in"]:
        mode = st.radio(
            "操作模式",
            ["觀看模式（唯讀）", "修改模式（開放編輯）"],
            index=0
        )
        st.session_state["edit_mode"] = "修改" in mode
    else:
        st.info("👁️ 唯讀保護中")
        st.session_state["edit_mode"] = False

    st.divider()

    st.markdown("**📋 功能選單**")
    base_menu = [
        "全廠能耗儀表板",
        "設備盤查與照片管理",
        "評分標準說明",
        "能源換算與排放數據",
        "每日負載分析",
        "能源基線追蹤-單位產量耗能",
        "能源基線追蹤-整廠用電量",
        "能源基線追蹤-重大設備",
    ]
    if st.session_state["logged_in"]:
        base_menu.append("從Excel重新載入")
        base_menu.append("版面格式設定")
    menu = st.radio("", base_menu, label_visibility="collapsed")

    st.divider()
    db_count = len(st.session_state["db"])
    st.caption(f"資料庫：{db_count} 台設備")
    st.caption(f"更新：{datetime.now().strftime('%Y/%m/%d %H:%M')}")

# ─────────────────────────────────────────────────────────────────────────────
# 7. 頂部標題 + 模式橫幅
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<div style='background:#f8fafc;padding:16px 22px;border-radius:10px;
            border-left:5px solid #1a3a5c;margin-bottom:18px'>
  <h1 style='margin:0;color:#1a3a5c;font-size:22px'>永寬化學股份有限公司</h1>
  <h3 style='margin:5px 0 0;color:#475569;font-size:14px;font-weight:normal'>
    重大能源使用設備 (SEUs) 網頁版管理系統
  </h3>
  <p style='margin:6px 0 0;color:#64748b;font-size:11px;line-height:1.6'>
    系統邊界：雲林縣斗六市榴南里　｜　樓地板面積：12,378.27 ㎡　｜　ISO 50001:2018
  </p>
</div>
""", unsafe_allow_html=True)

if st.session_state["edit_mode"]:
    st.markdown('<div class="mode-edit">✏️ <b>修改模式已啟用</b>：可新增設備、編輯數據、上傳照片。</div>',
                unsafe_allow_html=True)
else:
    st.markdown('<div class="mode-view">👁️ <b>唯讀觀看模式</b>：所有編輯功能已鎖定。管理員由左側輸入密碼解鎖。</div>',
                unsafe_allow_html=True)
st.markdown("")

# ─────────────────────────────────────────────────────────────────────────────
# Excel 檔案存在檢查
# ─────────────────────────────────────────────────────────────────────────────
if not os.path.exists(EXCEL_FILE) and len(st.session_state["db"]) == 0:
    st.warning("⚠️ 未偵測到 Excel 檔案，已載入內建示範資料。如需載入完整資料請將 Excel 放於同一資料夾。")

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面一：全廠能耗儀表板
# ─────────────────────────────────────────────────────────────────────────────
def _render_equipment_detail(r, db_idx, loop_idx):
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("消耗功率",   f"{r.get('消耗功率(kW)','')} kW")
    d2.metric("年運轉時數", f"{float(r.get('運轉時數(hr/年)') or 0):,.0f} hr")
    d3.metric("使用年數",   f"{int(r.get('使用年數') or 0)} 年")
    d4.metric("重大性評分", r["_sc"])
    col_info, col_photo = st.columns([1, 2.5])
    with col_info:
        load_pct = f"{float(r.get('負載率',0))*100:.0f}%" if r.get('負載率') else "—"
        info_df = pd.DataFrame({
            "欄位": ["部門", "棟別/樓層", "型式說明", "數量", "負載率", "設備年份",
                    "管理者", "外包商", "年耗電量", "SEU 鑑別"],
            "資料": [
                r.get('設備部門','—'),
                f"{r.get('所在棟別','—')} / {r.get('所在樓層','—')}",
                r.get('設備型式','—'),
                f"{r.get('設備數量','—')} 台",
                load_pct,
                r.get('設備年份','—'),
                r.get('設備管理者','—'),
                r.get('外包商承攬商','—'),
                f"{r['_kwh']:,.0f} kWh",
                '⭐ A 級' if r["_seu"]=='A' else '一般設備',
            ],
        })
        centered_table(info_df, context="equip")
    with col_photo:
        st.markdown("**📷 設備影像**")

        rot_key1 = f"rot_p1_{loop_idx}_{db_idx if db_idx is not None else 0}"
        rot_key2 = f"rot_p2_{loop_idx}_{db_idx if db_idx is not None else 0}"
        if rot_key1 not in st.session_state: st.session_state[rot_key1] = 0
        if rot_key2 not in st.session_state: st.session_state[rot_key2] = 0

        def _save_rotated(photo_key, rot_key, d_idx):
            """從資料庫讀原圖 → 旋轉 → 壓縮 → 存回"""
            try:
                raw_b64 = st.session_state["db"][d_idx].get(photo_key)
                if not raw_b64:
                    st.error("找不到照片資料")
                    return
                raw_bytes = base64.b64decode(raw_b64)
                img_src = Image.open(BytesIO(raw_bytes)).convert("RGB")
                angle = st.session_state[rot_key]
                img_out = img_src.rotate(-angle, expand=True)
                buf = BytesIO()
                img_out.save(buf, format="JPEG", quality=92)
                rotated_bytes = compress_photo_bytes(buf.getvalue())
                st.session_state["db"][d_idx][photo_key] = base64.b64encode(rotated_bytes).decode()
                save_json(st.session_state["db"])
                st.session_state[rot_key] = 0
                st.success(f"✅ {photo_key}已儲存！")
                st.rerun()
            except Exception as e:
                st.error(f"儲存失敗：{e}")

        ph1, ph2 = st.columns([1, 2])

        with ph1:
            st.caption("📷 外觀照片（直立）")
            photo1_data = r.get("外觀照片") or (
                st.session_state["db"][db_idx].get("外觀照片") if db_idx is not None else None)
            if photo1_data:
                try:
                    img1 = Image.open(BytesIO(base64.b64decode(photo1_data))).convert("RGB")
                    if st.session_state[rot_key1] != 0:
                        img1 = img1.rotate(-st.session_state[rot_key1], expand=True)
                    st.image(img1, use_container_width=True)
                except Exception as e:
                    st.warning(f"顯示失敗：{e}")
                    img1 = None
                if st.session_state.get("edit_mode"):
                    rc1a, rc1b = st.columns(2)
                    with rc1a:
                        if st.button("↺ 逆時針", key=f"ccw1_{loop_idx}_{db_idx}", use_container_width=True):
                            st.session_state[rot_key1] = (st.session_state[rot_key1] - 90) % 360
                            st.rerun()
                    with rc1b:
                        if st.button("↻ 順時針", key=f"cw1_{loop_idx}_{db_idx}", use_container_width=True):
                            st.session_state[rot_key1] = (st.session_state[rot_key1] + 90) % 360
                            st.rerun()
                    if st.session_state[rot_key1] != 0 and db_idx is not None:
                        if st.button("💾 儲存旋轉", key=f"sav1_{loop_idx}_{db_idx}", use_container_width=True):
                            _save_rotated("外觀照片", rot_key1, db_idx)
            else:
                st.markdown("""
<div style='background:#f1f5f9;border:2px dashed #cbd5e1;border-radius:10px;
            padding:40px 20px;text-align:center;color:#94a3b8;min-height:200px;
            display:flex;flex-direction:column;justify-content:center'>
  <div style='font-size:36px'>📷</div>
  <div style='margin-top:8px;font-size:13px'>尚未上傳外觀照片</div>
</div>""", unsafe_allow_html=True)

        with ph2:
            st.caption("🏷️ 銘牌照片（橫式）")
            photo2_data = r.get("銘牌照片") or (
                st.session_state["db"][db_idx].get("銘牌照片") if db_idx is not None else None)
            if photo2_data:
                try:
                    img2 = Image.open(BytesIO(base64.b64decode(photo2_data))).convert("RGB")
                    if st.session_state[rot_key2] != 0:
                        img2 = img2.rotate(-st.session_state[rot_key2], expand=True)
                    st.image(img2, use_container_width=True)
                except Exception as e:
                    st.warning(f"顯示失敗：{e}")
                    img2 = None
                if st.session_state.get("edit_mode"):
                    rc2a, rc2b = st.columns(2)
                    with rc2a:
                        if st.button("↺ 逆時針", key=f"ccw2_{loop_idx}_{db_idx}", use_container_width=True):
                            st.session_state[rot_key2] = (st.session_state[rot_key2] - 90) % 360
                            st.rerun()
                    with rc2b:
                        if st.button("↻ 順時針", key=f"cw2_{loop_idx}_{db_idx}", use_container_width=True):
                            st.session_state[rot_key2] = (st.session_state[rot_key2] + 90) % 360
                            st.rerun()
                    if st.session_state[rot_key2] != 0 and db_idx is not None:
                        if st.button("💾 儲存旋轉", key=f"sav2_{loop_idx}_{db_idx}", use_container_width=True):
                            _save_rotated("銘牌照片", rot_key2, db_idx)
            else:
                st.markdown("""
<div style='background:#f1f5f9;border:2px dashed #cbd5e1;border-radius:10px;
            padding:60px 20px;text-align:center;color:#94a3b8;min-height:160px;
            display:flex;flex-direction:column;justify-content:center'>
  <div style='font-size:36px'>🏷️</div>
  <div style='margin-top:8px;font-size:13px'>尚未上傳銘牌照片</div>
</div>""", unsafe_allow_html=True)
    if st.session_state["edit_mode"] and db_idx is not None:
        st.markdown("---")
        cur = st.session_state["db"][db_idx]
        with st.form(f"ef_{loop_idx}_{db_idx}"):
            e1, e2, e3 = st.columns(3)
            with e1:
                e_name = st.text_input("設備名稱", value=str(cur.get("設備名稱","") or ""))
                e_id   = st.text_input("設備編號", value=str(cur.get("設備編號","") or ""))
                e_dept = st.text_input("部門",     value=str(cur.get("設備部門","") or ""))
            with e2:
                e_kw   = st.number_input("消耗功率(kW)", value=float(cur.get("消耗功率(kW)") or 0), min_value=0.0, step=0.1)
                e_qty  = st.number_input("設備數量", value=int(cur.get("設備數量") or 1), min_value=1)
                e_load = st.slider("負載率", 0.1, 1.0, float(cur.get("負載率") or 0.8), 0.05)
            with e3:
                e_hrs  = st.number_input("年運轉時數", value=float(cur.get("運轉時數(hr/年)") or 0), min_value=0.0)
                e_crit = st.slider("自評重大性", 1, 5, int(cur.get("自評重大性") or 3))
                e_mgr  = st.text_input("設備管理者", value=str(cur.get("設備管理者","") or ""))
            up1 = st.file_uploader("更新外觀照片", type=["jpg","jpeg","png"], key=f"u1_{loop_idx}_{db_idx}")
            up2 = st.file_uploader("更新銘牌照片", type=["jpg","jpeg","png"], key=f"u2_{loop_idx}_{db_idx}")
            sv, dl = st.columns([3,1])
            with sv: save_ok = st.form_submit_button("💾 儲存變更", use_container_width=True)
            with dl: del_ok  = st.form_submit_button("🗑️ 刪除", use_container_width=True)
            if save_ok:
                st.session_state["db"][db_idx].update({
                    "設備名稱":e_name,"設備編號":e_id,"設備部門":e_dept,
                    "消耗功率(kW)":e_kw,"設備數量":e_qty,"負載率":e_load,
                    "運轉時數(hr/年)":e_hrs,"自評重大性":e_crit,"設備管理者":e_mgr,
                })
                photo_msgs = []
                if up1:
                    b64, ok, ck = compress_photo_to_b64(up1)
                    st.session_state["db"][db_idx]["外觀照片"] = b64
                    photo_msgs.append(f"外觀照片 {ok:,.0f}KB → {ck:,.0f}KB")
                if up2:
                    b64, ok, ck = compress_photo_to_b64(up2)
                    st.session_state["db"][db_idx]["銘牌照片"] = b64
                    photo_msgs.append(f"銘牌照片 {ok:,.0f}KB → {ck:,.0f}KB")
                save_json(st.session_state["db"])
                log_activity("編輯設備", f"{e_name}（{e_id}）" + (f"，已壓縮：{'；'.join(photo_msgs)}" if photo_msgs else ""))
                st.success("✅ 已儲存！" + ("　📷 " + "、".join(photo_msgs) if photo_msgs else ""))
                st.rerun()
            if del_ok:
                st.session_state[f"confirm_del_{loop_idx}_{db_idx}"] = True
                st.rerun()

        if st.session_state.get(f"confirm_del_{loop_idx}_{db_idx}"):
            st.error(f"⚠️ 確定要刪除「{cur.get('設備名稱','') }」（{cur.get('設備編號','')}）嗎？此操作無法復原。")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("✅ 確定刪除", key=f"confirm_yes_{loop_idx}_{db_idx}", use_container_width=True, type="primary"):
                    log_activity("刪除設備", f"{cur.get('設備名稱','')}（{cur.get('設備編號','')}）")
                    st.session_state["db"].pop(db_idx)
                    save_json(st.session_state["db"])
                    del st.session_state[f"confirm_del_{loop_idx}_{db_idx}"]
                    st.warning("已刪除。")
                    st.rerun()
            with cc2:
                if st.button("取消", key=f"confirm_no_{loop_idx}_{db_idx}", use_container_width=True):
                    del st.session_state[f"confirm_del_{loop_idx}_{db_idx}"]
                    st.rerun()


if "儀表板" in menu:
    rows = all_calc()

    if len(rows) == 0:
        st.warning("⚠️ 資料庫是空的，請前往「🔄 從Excel重新載入」重新載入資料。")
        st.stop()

    tot_kwh = sum(r["_kwh"] for r in rows)
    tot_a   = sum(1 for r in rows if r["_seu"] == "A")
    cov     = round(tot_kwh / TOTAL_KWH * 100, 1) if TOTAL_KWH else 0
    eui     = round(TOTAL_KWH / FLOOR_AREA, 2)

    # KPI 卡片 第一列
    k1, k2, k3 = st.columns(3)
    for col, val, lbl, color in [
        (k1, f"{TOTAL_KWH:,}", "全廠年實際總用電 (kWh)", "#1a3a5c"),
        (k2, str(eui),          "EUI 能源強度 (度/㎡·年)", "#2563a8"),
        (k3, str(len(rows)),    "已盤查設備總數（台）",    "#00c896"),
    ]:
        _fs = st.session_state.get("fmt",{}).get("dash_title_size", 28)
        _ff = st.session_state.get("fmt",{}).get("font_family","Noto Sans TC")
        col.markdown(
            f'<div class="kpi" style="text-align:center;font-family:{_ff}">' +
            f'<div class="kpi-v" style="color:{color};font-size:{_fs}px">{val}</div>' +
            f'<div class="kpi-l" style="font-size:{max(10,int(_fs*0.4))}px">{lbl}</div></div>',
            unsafe_allow_html=True
        )
    st.markdown("<div style='margin:8px 0'></div>", unsafe_allow_html=True)
    # KPI 卡片 第二列
    k4, k5, k6 = st.columns(3)
    for col, val, lbl, color in [
        (k4, str(tot_a),  "A 級重大耗能設備（台）", "#f59e0b"),
        (k5, f"{cov}%",   "盤查耗電覆蓋率（估算）", "#8b5cf6"),
        (k6, f"{FLOOR_AREA:,.0f} ㎡", "廠區樓地板面積",    "#64748b"),
    ]:
        _fs = st.session_state.get("fmt",{}).get("dash_title_size", 28)
        _ff = st.session_state.get("fmt",{}).get("font_family","Noto Sans TC")
        col.markdown(
            f'<div class="kpi" style="text-align:center;font-family:{_ff}">' +
            f'<div class="kpi-v" style="color:{color};font-size:{_fs}px">{val}</div>' +
            f'<div class="kpi-l" style="font-size:{max(10,int(_fs*0.4))}px">{lbl}</div></div>',
            unsafe_allow_html=True
        )
    st.markdown("<br>", unsafe_allow_html=True)

    # 系統彙總
    sys_agg = {}
    for r in rows:
        s = r.get("系統別", "其他")
        sys_agg.setdefault(s, {"kwh": 0, "a": 0, "n": 0})
        sys_agg[s]["kwh"] += r["_kwh"]
        sys_agg[s]["a"]   += 1 if r["_seu"] == "A" else 0
        sys_agg[s]["n"]   += 1

    df_sys = pd.DataFrame([
        {
            "系統別":       s,
            "耗電量(kWh/年)": round(v["kwh"], 0),
            "佔比(%)":      round(v["kwh"] / tot_kwh * 100, 1) if tot_kwh > 0 else 0,
            "設備數":       v["n"],
            "A級設備":      v["a"],
        }
        for s, v in sys_agg.items()
        if v["kwh"] > 0   # ← 只顯示有耗電量的系統
    ])

    ch1, ch2 = st.columns([1, 1])
    with ch1:
        if len(df_sys) > 0:
            fig_pie = px.pie(
                df_sys,
                values="耗電量(kWh/年)",
                names="系統別",
                hole=0.40,
                color_discrete_sequence=["#1a3a5c","#2563a8","#00c896","#f59e0b","#8b5cf6"]
            )
            fig_pie.update_traces(
                textinfo="percent",
                textposition="inside",
                textfont=dict(size=st.session_state.get('fmt',{}).get('dash_chart_font_size', 13)),
                hovertemplate="<b>%{label}</b><br>%{value:,.0f} kWh<br>%{percent}<extra></extra>"
            )
            fig_pie.update_layout(
                legend=dict(
                    orientation="h",
                    yanchor="top", y=-0.05,
                    xanchor="center", x=0.5,
                    font=dict(size=st.session_state.get('fmt',{}).get('dash_chart_font_size', 13))
                ),
                margin=dict(t=20, b=60, l=20, r=20),
                height=400,
            )
            st.plotly_chart(fig_pie, use_container_width=True)
            st.markdown(f"<p style='text-align:center;font-size:{st.session_state.get('fmt',{}).get('dash_title_size', 28)}px;font-weight:700;color:#1a3a5c;'>全廠區用電數據</p>", unsafe_allow_html=True)
        else:
            st.info("無耗電量資料，無法顯示圓餅圖。")

    with ch2:
        if len(df_sys) > 0:
            _dcf = st.session_state.get('fmt',{}).get('dash_chart_font_size', 13)
            fig_bar = go.Figure(go.Bar(
                x=df_sys["系統別"],
                y=df_sys["耗電量(kWh/年)"],
                text=df_sys["耗電量(kWh/年)"].apply(lambda v: f"{v:,.0f}"),
                textposition="outside",
                textfont=dict(size=_dcf),
                marker_color=["#1a3a5c","#2563a8","#00c896","#f59e0b","#8b5cf6"],
                width=0.5,
            ))
            fig_bar.update_layout(
                title=dict(text="各系統年耗電量 (kWh)", x=0.5,
                           font=dict(size=st.session_state.get("fmt",{}).get("dash_title_size", 28))),
                height=380,
                margin=dict(t=50, b=60, l=60, r=20),
                plot_bgcolor="white", paper_bgcolor="white",
                yaxis_tickformat=",",
                xaxis=dict(tickfont=dict(size=_dcf)),
                yaxis=dict(tickfont=dict(size=_dcf)),
                bargap=0.3,
            )
            st.plotly_chart(fig_bar, use_container_width=True)

    st.divider()
    st.subheader("📋 各系統耗能摘要")
    if len(df_sys) > 0:
        df_show = df_sys.copy()
        df_show["耗電量(kWh/年)"] = df_show["耗電量(kWh/年)"].apply(lambda v: f"{v:,.0f}")
        df_show["佔比(%)"]        = df_show["佔比(%)"].apply(lambda v: f"{v:.1f}%")
        centered_table(df_show, context="dash")

    # A 級設備清單
    a_rows = sorted([r for r in rows if r["_seu"] == "A"],
                    key=lambda r: r["_kwh"], reverse=True)
    if a_rows:
        st.divider()
        st.subheader("⭐ A 級重大耗能設備（依耗電量排序）")
        centered_table(pd.DataFrame([{
            "系統":         r.get("系統別", ""),
            "設備名稱":     r.get("設備名稱", ""),
            "編號":         r.get("設備編號", ""),
            "部門":         r.get("設備部門", ""),
            "功率(kW)":    r.get("消耗功率(kW)", ""),
            "年耗電(kWh)": f"{r['_kwh']:,.0f}",
            "重大性評分":  r["_sc"],
            "管理者":       r.get("設備管理者", ""),
        } for r in a_rows]), context="dash")

    # ── 各項能源耗能占比
    st.divider()
    st.subheader("🔥 各項能源耗能占比")

    # 能源數據（來自 Excel 表2 各項能源耗能占比）
    ENERGY_DATA = {
        "汽油":   {"kwh_per_m2": 9339.49023,  "pct": 0.48},
        "柴油":   {"kwh_per_m2": 20090.1181,  "pct": 1.04},
        "外購電力": {"kwh_per_m2": 1911641,    "pct": 98.48},
    }
    TOTAL_ENERGY_KWH = 1941070.608  # 全廠實際耗能量 kWh/公秉

    df_energy = pd.DataFrame([
        {
            "能源來源":           name,
            "能耗評估(kWh/公秉)": v["kwh_per_m2"],
            "耗能占比(%)":        v["pct"],
        }
        for name, v in ENERGY_DATA.items()
    ])

    e_col1, e_col2 = st.columns([1, 1])

    with e_col1:
        fig_energy = go.Figure(go.Pie(
            labels=list(ENERGY_DATA.keys()),
            values=[v["pct"] for v in ENERGY_DATA.values()],
            hole=0.40,
            marker_colors=["#3b82f6", "#ef4444", "#22c55e"],
            textinfo="percent",
            textposition="inside",
            insidetextorientation="radial",
            textfont=dict(size=st.session_state.get('fmt',{}).get('dash_chart_font_size', 13)),
            hovertemplate="<b>%{label}</b><br>占比：%{value:.2f}%<extra></extra>",
        ))
        fig_energy.update_layout(
            legend=dict(
                orientation="h",
                yanchor="top", y=-0.05,
                xanchor="center", x=0.5,
                font=dict(size=st.session_state.get('fmt',{}).get('dash_chart_font_size', 13))
            ),
            margin=dict(t=20, b=60, l=20, r=20),
            height=400,
        )
        st.plotly_chart(fig_energy, use_container_width=True)
        st.markdown(f"<p style='text-align:center;font-size:{st.session_state.get('fmt',{}).get('dash_title_size', 28)}px;font-weight:700;color:#1a3a5c;'>各項能源耗能占比</p>", unsafe_allow_html=True)

    with e_col2:
        st.markdown("##### 各項能源耗能數據")
        df_energy_show = df_energy.copy()
        df_energy_show["能耗評估(kWh/公秉)"] = df_energy_show["能耗評估(kWh/公秉)"].apply(lambda v: f"{v:,.2f}")
        df_energy_show["耗能占比(%)"] = df_energy_show["耗能占比(%)"].apply(lambda v: f"{v:.2f}%")
        centered_table(df_energy_show, context="dash")
        _dhsz = st.session_state.get("fmt", {}).get("dash_title_size", 28)
        _dhlsz = max(10, int(_dhsz * 0.4))
        st.markdown(f"""
        <div style='background:#f0fdf4;border-left:4px solid #22c55e;
                    padding:14px 16px;border-radius:6px;margin-top:12px'>
          <div style='font-size:{_dhlsz}px;color:#64748b;margin-bottom:4px'>全廠實際總耗能量</div>
          <div style='font-size:{_dhsz}px;font-weight:800;color:#166534'>
            1,941,070.608 <span style='font-size:{_dhlsz}px;font-weight:400'>kWh/公秉</span>
          </div>
          <div style='font-size:{_dhlsz}px;color:#64748b;margin-top:8px'>
            外購電力占全廠能源 <strong style='color:#166534'>98.48%</strong>，
            為最主要能源來源
          </div>
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面二：設備盤查與照片管理
# ─────────────────────────────────────────────────────────────────────────────
elif "設備盤查" in menu:

    # 「標題」設定同時控制：展開卡標題文字、摘要卡片數字、設備明細的 4 個數字方塊（st.metric）。
    # Streamlit 沒有提供官方參數可以直接調整 st.expander() 或 st.metric() 的字體大小，
    # 這裡用 CSS 選擇 data-testid（比內部 class 名稱穩定，不會因為 Streamlit 版本更新就失效）
    # 局部覆蓋，只在這個頁面生效。
    _etsz = st.session_state.get("fmt", {}).get("equip_title_size", 14)
    st.markdown(f"""
    <style>
    div[data-testid="stExpander"] summary p {{ font-size: {_etsz}px; }}
    div[data-testid="stMetricValue"] {{ font-size: {_etsz}px; }}
    </style>
    """, unsafe_allow_html=True)

    # 新增設備表單（修改模式）
    if st.session_state["edit_mode"]:
        with st.expander("➕ 新增設備（展開填寫）", expanded=False):
            with st.form("add_form", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                with c1:
                    in_sys  = st.selectbox("所屬系統", list(SYSTEM_SHEETS.keys()))
                    in_name = st.text_input("設備名稱 *")
                    in_id   = st.text_input("設備編號 *")
                    in_type = st.text_input("型式/說明")
                with c2:
                    in_dept = st.text_input("設備部門")
                    in_bldg = st.text_input("所在棟別")
                    in_kw   = st.number_input("消耗功率 (kW)", min_value=0.0, value=5.0, step=0.1)
                    in_qty  = st.number_input("設備數量 (台)", min_value=1, value=1)
                with c3:
                    in_load = st.slider("負載率", 0.1, 1.0, 0.85, 0.05)
                    in_hrs  = st.number_input("年運轉時數 (hr)", min_value=0.0, value=2000.0)
                    in_yr   = st.number_input("設備年份", 1990, 2030, 2015)
                    in_crit = st.slider("自評重大性 (1~5)", 1, 5, 3)
                pic1 = st.file_uploader("📷 設備外觀照片", type=["jpg","jpeg","png"])
                pic2 = st.file_uploader("🏷️ 銘牌照片",     type=["jpg","jpeg","png"])

                if st.form_submit_button("💾 提交寫入資料庫", use_container_width=True):
                    if not in_name or not in_id:
                        st.error("設備名稱與編號為必填！")
                    else:
                        photo1_b64, photo2_b64 = None, None
                        photo_msgs = []
                        if pic1:
                            photo1_b64, ok, ck = compress_photo_to_b64(pic1)
                            photo_msgs.append(f"外觀照片 {ok:,.0f}KB → {ck:,.0f}KB")
                        if pic2:
                            photo2_b64, ok, ck = compress_photo_to_b64(pic2)
                            photo_msgs.append(f"銘牌照片 {ok:,.0f}KB → {ck:,.0f}KB")
                        new = {
                            "系統別": in_sys, "設備名稱": in_name, "設備編號": in_id,
                            "設備型式": in_type, "設備部門": in_dept, "所在棟別": in_bldg,
                            "消耗功率(kW)": in_kw, "設備數量": in_qty, "負載率": in_load,
                            "運轉時數(hr/年)": in_hrs, "設備年份": in_yr,
                            "使用年數": datetime.now().year - int(in_yr),
                            "自評重大性": in_crit,
                            "外觀照片": photo1_b64,
                            "銘牌照片": photo2_b64,
                        }
                        st.session_state["db"].append(new)
                        save_json(st.session_state["db"])
                        log_activity("新增設備", f"{in_name}（{in_id}）" + (f"，已壓縮：{'；'.join(photo_msgs)}" if photo_msgs else ""))
                        st.success(f"✅ 設備【{in_name}】已寫入！" + ("　📷 " + "、".join(photo_msgs) if photo_msgs else ""))
                        st.rerun()

    # ── 搜尋 + 重大性篩選
    kw_f  = st.text_input("🔍 搜尋設備名稱 / 編號 / 部門（跨系統搜尋）")
    seu_f = st.selectbox("重大性篩選", ["全部", "A 級重大設備", "一般設備"])
    rows  = all_calc()

    # 建立一次性的索引表（O(1) 查找）。原本 get_db_idx() 每叫一次就把整個資料庫
    # （335 筆）掃過一遍，335 台設備等於做了超過 10 萬次比對，是頁面很慢的主因之一。
    db_index = {}
    for i, d in enumerate(st.session_state["db"]):
        key = (str(d.get("系統別","")), str(d.get("設備名稱","")), str(d.get("設備編號","")))
        db_index[key] = i

    def get_db_idx(r):
        key = (str(r.get("系統別","")), str(r.get("設備名稱","")), str(r.get("設備編號","")))
        return db_index.get(key)

    PAGE_SIZE = 20  # 每頁最多顯示幾台設備，避免一次把上百個展開卡片（含照片）全部畫出來

    if kw_f:
        # ── 搜尋模式
        filtered = [r for r in rows
                    if (seu_f=="全部" or (seu_f=="A 級重大設備" and r["_seu"]=="A") or (seu_f=="一般設備" and r["_seu"]!="-"))
                    and kw_f.lower() in f"{r.get('設備名稱','')} {r.get('設備編號','')} {r.get('設備部門','')}".lower()]
        st.caption(f"搜尋結果：**{len(filtered)}** 筆")

        total_pages = max(1, math.ceil(len(filtered) / PAGE_SIZE))
        page = st.number_input("頁數", min_value=1, max_value=total_pages, value=1, step=1,
                                key="search_page") if total_pages > 1 else 1
        start = (page - 1) * PAGE_SIZE
        for li, r in enumerate(filtered[start:start + PAGE_SIZE]):
            icon  = SYSTEM_ICONS.get(r.get("系統別",""), "🔧")
            a_tag = " ⭐A級" if r["_seu"]=="A" else ""
            title = f"{icon}[{r.get('系統別','')}] {r.get('設備名稱','')} ({r.get('設備編號','')})  ｜  {r['_kwh']:,.0f} kWh  評分{r['_sc']}{a_tag}"
            with st.expander(title, expanded=False):
                _render_equipment_detail(r, get_db_idx(r), start + li)
        if total_pages > 1:
            st.caption(f"第 {page} / {total_pages} 頁")
    else:
        # ── 系統分頁模式
        sys_rows = {}
        for r in rows:
            if seu_f=="A 級重大設備" and r["_seu"]!="A": continue
            if seu_f=="一般設備" and r["_seu"]=="A": continue
            s = r.get("系統別","其他")
            sys_rows.setdefault(s,[]).append(r)

        # 摘要卡片：直接點卡片切換系統，取代原本卡片下方另外一排的 radio 選單。
        # 原理：st.button 的外觀用 CSS 改造成卡片樣式（圓角、陰影、頂部色條），
        # 目前選中的系統用 type="primary" 讓 Streamlit 原生畫出高亮外框做區分。
        # 效能考量與原本 radio 版本相同：只有被選到的那個系統才會真的被計算與渲染，
        # 其餘系統的設備明細完全不會執行，避免 335 台設備一次全部算完、全部畫出來。
        _ecsz = st.session_state.get("fmt", {}).get("equip_title_size", 14)
        if st.session_state.get("equip_sys_selected") not in sys_rows:
            st.session_state["equip_sys_selected"] = next(iter(sys_rows))

        st.markdown(f"""
        <style>
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] button {{
            height: auto; min-height: 132px; padding: 18px 10px; border-radius: 12px;
            border: 1px solid rgba(0,0,0,.06); border-top: 4px solid #2563a8;
            box-shadow: 0 1px 6px rgba(0,0,0,.10); background:#fff;
            white-space: pre-line; line-height: 1.5; font-size: {_ecsz}px;
        }}
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] button[kind="primary"] {{
            border: 2px solid #2563a8; border-top: 4px solid #2563a8; background:#eef4fb;
        }}
        </style>
        """, unsafe_allow_html=True)

        card_cols = st.columns(len(sys_rows))
        for i,(sn_i,sl_i) in enumerate(sys_rows.items()):
            a_cnt = sum(1 for r in sl_i if r["_seu"]=="A")
            icon = SYSTEM_ICONS.get(sn_i,"🔧")
            label = (f"{icon} **{sn_i}**\n"
                     f"{len(sl_i)} 台設備\n"
                     f"A級：{a_cnt} 台\n"
                     f"{sum(r['_kwh'] for r in sl_i):,.0f} kWh/年")
            is_sel = st.session_state["equip_sys_selected"] == sn_i
            if card_cols[i].button(label, key=f"sys_card_{sn_i}", use_container_width=True,
                                    type="primary" if is_sel else "secondary"):
                st.session_state["equip_sys_selected"] = sn_i
                st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        sn = st.session_state["equip_sys_selected"]
        sl = sys_rows[sn]

        a_list = [r for r in sl if r["_seu"]=="A"]
        total_kwh = sum(r['_kwh'] for r in sl)
        st.caption(f"共 **{len(sl)}** 台 ｜ A級 **{len(a_list)}** 台 ｜ 年耗電 **{total_kwh:,.0f}** kWh")
        # A級優先顯示在最上方，其餘設備依序排列
        sorted_sl = sorted(sl, key=lambda r: (0 if r["_seu"]=="A" else 1, -r["_kwh"]))

        total_pages = max(1, math.ceil(len(sorted_sl) / PAGE_SIZE))
        page = st.number_input(f"{sn} 頁數", min_value=1, max_value=total_pages, value=1, step=1,
                                key=f"page_{sn}") if total_pages > 1 else 1
        start = (page - 1) * PAGE_SIZE
        for li,r in enumerate(sorted_sl[start:start + PAGE_SIZE]):
            a_tag = " ⭐A級" if r["_seu"]=="A" else ""
            title = f"{r.get('設備名稱','')} ({r.get('設備編號','')})  ｜  {r['_kwh']:,.0f} kWh/年  評分{r['_sc']}{a_tag}"
            with st.expander(title, expanded=False):
                _render_equipment_detail(r, get_db_idx(r), start + li)
        if total_pages > 1:
            st.caption(f"第 {page} / {total_pages} 頁（共 {len(sorted_sl)} 台）")

        st.divider()
        csv_data = pd.DataFrame([{k:v for k,v in r.items() if k not in ("外觀照片","銘牌照片","_kwh","_sc","_seu")} for r in sl]).to_csv(index=False).encode("utf-8-sig")
        st.download_button(f"⬇️ 匯出 {sn} CSV", csv_data,
            f"SEU_{sn}_{datetime.now().strftime('%Y%m%d')}.csv","text/csv",key=f"dl_{sn}")








# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：評分標準說明
# ─────────────────────────────────────────────────────────────────────────────
elif "評分標準" in menu:
    st.subheader("📐 ISO 50001 重大能源使用設備評分標準")

    # 「標題」設定控制這個頁面裡的 #### / ##### 章節標題文字大小（Streamlit markdown
    # 標題本身沒有字體大小參數，用 CSS 覆蓋內建的 h4/h5 標籤，只在這個頁面生效）。
    _stsz = st.session_state.get("fmt", {}).get("score_title_size", 18)
    st.markdown(f"""
    <style>
    div[data-testid="stMarkdownContainer"] h4 {{ font-size: {_stsz}px; }}
    div[data-testid="stMarkdownContainer"] h5 {{ font-size: {_stsz * 0.85:.0f}px; }}
    </style>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["重大能源使用鑑別（A級）", "優先改善項目鑑別（I級）"])

    with tab1:
        st.warning(
            "📝 **v2.2 更新：** 這裡的權重與公式已改為和系統實際計算邏輯（`calc_row()`）一致，"
            "新增了原本沒有顯示的「設備功率」評分。"
        )
        st.markdown("#### 鑑別因子與權重")
        df_w1 = pd.DataFrame({
            "鑑別因子": ["設備耗能估比", "設備功率", "工廠自評重大性（設備管控評估）", "總計"],
            "估比":     ["30%", "40%", "30%", "100%"],
        })
        centered_table(df_w1, context="score")

        st.markdown("<br>", unsafe_allow_html=True)
        col1, col2, col2b = st.columns(3)

        with col1:
            st.markdown("##### 設備耗能估比評分")
            df_s1 = pd.DataFrame({
                "年耗電量(kWh)": ["— ～ 2,499", "2,500 ～ 5,499", "5,500 ～ 7,499", "7,500 ～ 9,999", "10,000 ～"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_s1, context="score")

        with col2:
            st.markdown("##### 設備功率評分")
            df_s1b = pd.DataFrame({
                "消耗功率(kW)": ["— ～ 2.49", "2.5 ～ 4.99", "5.0 ～ 7.49", "7.5 ～ 8.99", "9.0 ～"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_s1b, context="score")

        with col2b:
            st.markdown("##### 工廠自評重大性評分")
            df_s2 = pd.DataFrame({
                "評估等級": ["— ～ 1", "2 ～ 2", "3 ～ 3", "4 ～ 4", "5 ～ 5"],
                "說明": ["非重要管控項目", "", "需再評估", "", "既有或應該列入管控"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_s2, context="score")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("##### 重大能源使用鑑別級距")
        df_seu = pd.DataFrame({
            "評分範圍": ["0 ～ 1.9 分", "2 ～ 2.9 分", "3 ～ 3.9 分", "4 ～ 5.0 分"],
            "級別": ["-", "-", "-", "A"],
            "說明": ["一般設備", "一般設備", "一般設備", "重大能源使用設備（SEU）"],
        })
        centered_table(df_seu, context="score")

        st.info(
            "**計算公式：** 重大性評分 = 設備耗能估比分數 × 30% ＋ 設備功率分數 × 40% ＋ 工廠自評重大性分數 × 30%\n\n"
            "評分 ≥ 4.0 分 → 鑑別為 **A 級重大能源使用設備（SEU）**，需研提能源管理行動計畫並制訂操作規範。\n\n"
            "> 📝 備註：管控可為 SOP 或即時監控"
        )

    with tab2:
        st.markdown("#### 鑑別因子與權重")
        df_w2 = pd.DataFrame({
            "鑑別因子":     ["設備耗能估比", "設備老舊度", "設備運轉度", "能效改善頻率", "改善執行難易度", "總計"],
            "估比":         ["15%", "30%", "5%", "20%", "30%", "100%"],
        })
        centered_table(df_w2, context="score")

        st.markdown("<br>", unsafe_allow_html=True)
        col3, col4 = st.columns(2)

        with col3:
            st.markdown("##### 設備耗能估比評分")
            df_p1 = pd.DataFrame({
                "耗能估比範圍": ["0% ～ 0.1%", "0.1% ～ 0.1%", "0.1% ～ 0.4%", "0.5% ～ 1.0%", "1.0% ～"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_p1, context="score")

            st.markdown("##### 設備老舊度評分")
            df_p2 = pd.DataFrame({
                "使用年數": ["0 ～ 4 年", "5 ～ 9 年", "10 ～ 14 年", "15 ～ 19 年", "20 年以上"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_p2, context="score")

            st.markdown("##### 設備運轉度評分")
            df_p3 = pd.DataFrame({
                "年運轉時數": ["0 ～ 1,460 小時", "1,461 ～ 2,920 小時", "2,921 ～ 4,380 小時", "4,381 ～ 5,840 小時", "5,841 ～ 8,760 小時"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_p3, context="score")

        with col4:
            st.markdown("##### 能效改善頻率評分")
            df_p4 = pd.DataFrame({
                "改善頻率": ["# ～ 1（5年內新機）", "2 ～ 2", "3 ～ 3（10年以上能效改善1次）", "4 ～ 4", "5 ～ 5（10年以上從未改善）"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_p4, context="score")

            st.markdown("##### 改善執行難易度評分")
            df_p5 = pd.DataFrame({
                "難易度": ["# ～ 1（不會改善）", "2 ～ 2", "3 ～ 3（需再評估）", "4 ～ 4", "5 ～ 5（可立即改善）"],
                "分數": [1, 2, 3, 4, 5],
            })
            centered_table(df_p5, context="score")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("##### 優先改善項目鑑別級距")
        df_pri = pd.DataFrame({
            "評分範圍": ["0 ～ 1.9 分", "2 ～ 2.9 分", "3 ～ 3.9 分", "4 ～ 5.0 分"],
            "級別": ["-", "-", "-", "I"],
            "說明": ["一般設備", "一般設備", "一般設備", "優先改善項目"],
        })
        centered_table(df_pri, context="score")

        st.info("**計算公式：** 優先改善評分 = 耗能估比分數 × 15% ＋ 老舊度分數 × 30% ＋ 運轉度分數 × 5% ＋ 改善頻率分數 × 20% ＋ 改善難易度分數 × 30%\n\n評分 ≥ 4.0 分 → 鑑別為 **I 級優先改善項目**，需優先執行能效改善作業。")


# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：能源換算與排放數據
# ─────────────────────────────────────────────────────────────────────────────
elif "能源換算" in menu:
    st.subheader("⚡ 能源換算與溫室氣體排放數據")

    # 「標題」設定同時控制：圖表標題文字、頁面裡數字方塊（st.metric）的字體大小。
    _entsz = st.session_state.get("fmt", {}).get("energy_title_size", 16)
    st.markdown(f"""
    <style>
    div[data-testid="stMetricValue"] {{ font-size: {_entsz}px; }}
    </style>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs([
        "熱值表", "換算表（能源使用）", "油當量表", "溫室氣體排放數據"
    ])

    # ── Tab 1：熱值表
    with tab1:
        st.markdown("#### 各能源熱值表")
        st.caption("資料來源：113年能源統計手冊－能源產品單位熱值表")
        df_heat = pd.DataFrame([
            {"能源種類": "燃料油",        "熱值": 9320,  "單位": "kcal/L"},
            {"能源種類": "車用汽油",       "熱值": 7520,  "單位": "kcal/L"},
            {"能源種類": "柴油",          "熱值": 8629,  "單位": "kcal/L"},
            {"能源種類": "液化石油氣(LPG)","熱值": 5958,  "單位": "kcal/L"},
            {"能源種類": "天然氣(NG)",    "熱值": 5925,  "單位": "kcal/m³"},
            {"能源種類": "外購電力",       "熱值": 860,   "單位": "kcal/kWh"},
            {"能源種類": "燃料煤",        "熱值": 5890,  "單位": "kcal/kg"},
        ])
        centered_table(df_heat, context="energy")

        st.markdown("#### 換算說明")
        st.info("""
- 液化石油氣：1 公斤＝1.818 公升（一般）
- 液化天然氣：1 公斤（液態）≒1.320 立方公尺（氣態）≒2.207 公升（液態）
- 丙烷混合氣：1 公斤＝1.095 立方公尺＝1.786 公升
- 1 公斤油當量 = 10,000 千卡
- 1 kWh = 3,600 kJ（千焦耳）
- 1 kcal = 4.184 kJ
- 1 kWh = 3,600 kJ = 3,600/4.184 kcal = 860.4 kcal
- 1 Mcal = 1,000 kcal = 860.4 kcal/kWh = 1.1628 kWh
        """)

    # ── Tab 2：換算表（能源使用）
    with tab2:
        st.markdown("#### 各項能源使用換算表")
        df_conv = pd.DataFrame([
            {
                "能源種類": "燃料油", "使用量": "-", "單位": "公秉",
                "用電量(kWh/公秉)": "-", "熱值(Mcal/L)": "-",
                "油當量值(kLOE/公秉)": "-", "溫室氣體排放量(公噸CO₂e/公秉)": "-"
            },
            {
                "能源種類": "汽油", "使用量": "1.0681", "單位": "公秉",
                "用電量(kWh/公秉)": "9,339.490", "熱值(Mcal/L)": "8,031.962",
                "油當量值(kLOE/公秉)": "0.80", "溫室氣體排放量(公噸CO₂e/公秉)": "2.4650"
            },
            {
                "能源種類": "柴油", "使用量": "2.0023", "單位": "公秉",
                "用電量(kWh/公秉)": "20,090.118", "熱值(Mcal/L)": "17,277.502",
                "油當量值(kLOE/公秉)": "1.73", "溫室氣體排放量(公噸CO₂e/公秉)": "6.2700"
            },
            {
                "能源種類": "液化石油氣(LPG)", "使用量": "-", "單位": "公秉",
                "用電量(kWh/公秉)": "-", "熱值(Mcal/L)": "-",
                "油當量值(kLOE/公秉)": "-", "溫室氣體排放量(公噸CO₂e/公秉)": "0.0000"
            },
            {
                "能源種類": "天然氣(NG)", "使用量": "-", "單位": "千立方公尺",
                "用電量(kWh/公秉)": "-", "熱值(Mcal/L)": "-",
                "油當量值(kLOE/公秉)": "-", "溫室氣體排放量(公噸CO₂e/公秉)": "0.0000"
            },
            {
                "能源種類": "外購電力", "使用量": "1,911.6410", "單位": "千度",
                "用電量(kWh/公秉)": "1,911,641.00", "熱值(Mcal/L)": "1,644,011.26",
                "油當量值(kLOE/公秉)": "164.40", "溫室氣體排放量(公噸CO₂e/公秉)": "906.1178"
            },
            {
                "能源種類": "燃料煤", "使用量": "-", "單位": "公噸",
                "用電量(kWh/公秉)": "-", "熱值(Mcal/L)": "-",
                "油當量值(kLOE/公秉)": "-", "溫室氣體排放量(公噸CO₂e/公秉)": "0.0000"
            },
        ])
        centered_table(df_conv, context="energy")

        # 摘要 KPI
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("外購電力用電量", "1,911,641 kWh", "千度")
        c2.metric("總油當量", "164.40 kLOE", "外購電力")
        c3.metric("總CO₂排放量", "906.12 公噸CO₂e", "外購電力貢獻")

    # ── Tab 3：油當量表
    with tab3:
        st.markdown("#### 各能源油當量換算表")
        st.caption("資料來源：113年能源統計手冊－能源產品單位熱值表")
        df_oe = pd.DataFrame([
            {"能源種類": "燃料油",         "油當量值": 0.9320, "單位": "kLOE/公秉"},
            {"能源種類": "汽油",           "油當量值": 0.7520, "單位": "kLOE/公秉"},
            {"能源種類": "柴油",           "油當量值": 0.8629, "單位": "kLOE/公秉"},
            {"能源種類": "液化石油氣(LPG)", "油當量值": 0.5958, "單位": "kLOE/公秉"},
            {"能源種類": "天然氣(NG)",     "油當量值": 0.5925, "單位": "kLOE/立方公尺"},
            {"能源種類": "外購電力",        "油當量值": 0.0860, "單位": "kLOE/千度"},
            {"能源種類": "燃料煤",         "油當量值": 0.5890, "單位": "kLOE/公噸"},
        ])
        centered_table(df_oe, context="energy")

    # ── Tab 4：溫室氣體排放數據
    with tab4:
        st.markdown("#### 溫室氣體排放數據（類別1＋類別2）")

        # 摘要卡片
        g1, g2, g3, g4 = st.columns(4)
        g1.metric("汽油排放",   "2.4650 公噸CO₂e", "類別1－移動源")
        g2.metric("柴油排放",   "6.2700 公噸CO₂e", "類別1－移動源")
        g3.metric("外購電力排放", "906.1178 公噸CO₂e", "類別2")
        g4.metric("總排放量",   "909.11 公噸CO₂e", "合計")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 排放明細")
        df_ghg = pd.DataFrame([
            {
                "項次": 1, "設備名稱": "資車・公務車", "類別": "類別1",
                "子類別": "1.2移動燃燒直接排放", "原燃料": "汽油",
                "活動數量": 1.0681, "活動數量單位": "公秉",
                "CO₂排放量(公噸/年)": 2.3580, "GWP": 1.0,
                "排放量(公噸CO₂e/年)": 2.3580
            },
            {
                "項次": 2, "設備名稱": "資車", "類別": "類別1",
                "子類別": "1.2移動燃燒直接排放", "原燃料": "柴油",
                "活動數量": 2.0023, "活動數量單位": "公秉",
                "CO₂排放量(公噸/年)": 3.0478, "GWP": 1.0,
                "排放量(公噸CO₂e/年)": 3.0478
            },
            {
                "項次": 3, "設備名稱": "堆高機", "類別": "類別1",
                "子類別": "1.2移動燃燒直接排放", "原燃料": "柴油",
                "活動數量": 2.0023, "活動數量單位": "公秉",
                "CO₂排放量(公噸/年)": 3.1244, "GWP": 1.0,
                "排放量(公噸CO₂e/年)": 3.1244
            },
            {
                "項次": 4, "設備名稱": "未量測設定", "類別": "類別2",
                "子類別": "2.1輸入電力的間接排放", "原燃料": "外購電力",
                "活動數量": 1911.6410, "活動數量單位": "千度",
                "CO₂排放量(公噸/年)": 906.1178, "GWP": 1.0,
                "排放量(公噸CO₂e/年)": 906.1178
            },
        ])
        centered_table(df_ghg, context="energy")

        st.markdown("<br>", unsafe_allow_html=True)

        # 排放量圓餅圖
        fig_ghg = go.Figure(go.Pie(
            labels=["汽油（公務車）", "柴油（資車）", "柴油（堆高機）", "外購電力"],
            values=[2.4650, 3.0478, 3.1244, 906.1178],
            hole=0.40,
            marker_colors=["#3b82f6", "#f59e0b", "#ef4444", "#22c55e"],
            textinfo="percent",
            textposition="inside",
            insidetextorientation="radial",
            textfont=dict(size=st.session_state.get('fmt',{}).get('energy_chart_font_size', 13)),
            hovertemplate="<b>%{label}</b><br>%{value:.4f} 公噸CO₂e<br>%{percent}<extra></extra>",
        ))
        fig_ghg.update_layout(
            legend=dict(
                orientation="h",
                yanchor="top", y=-0.05,
                xanchor="center", x=0.5,
                font=dict(size=st.session_state.get('fmt',{}).get('energy_chart_font_size', 13))
            ),
            margin=dict(t=20, b=60, l=20, r=20),
            height=400,
        )
        st.plotly_chart(fig_ghg, use_container_width=True)
        st.markdown(f"<p style='text-align:center;font-size:{st.session_state.get('fmt',{}).get('energy_title_size', 16)}px;font-weight:700;color:#1a3a5c;'>溫室氣體排放來源分布</p>", unsafe_allow_html=True)

        st.info("""
**排放係數來源：**
- 溫室氣體排放係數管理表6.0.4版
- 國家溫室氣體排放係數：汽油(移動) / 柴油(固定)
- 來源：(5)國家排放係數
        """)

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面三：每日負載分析
# ─────────────────────────────────────────────────────────────────────────────
elif "負載" in menu:
    st.subheader("📈 全廠 24 小時電力負載曲線")
    st.info("統計區間：2024 年 6/11 – 10/18（共 95 天尖峰期）")

    # 「標題」設定同時控制：圖表標題文字、下方數字方塊（st.metric）的字體大小。
    _ldtsz = st.session_state.get("fmt", {}).get("load_title_size", 16)
    st.markdown(f"""
    <style>
    div[data-testid="stMetricValue"] {{ font-size: {_ldtsz}px; }}
    </style>
    """, unsafe_allow_html=True)

    df_load = pd.DataFrame({
        "時間(時)": list(range(1, 25)),
        "最高用電(kW)": [135,130,127,126,125,128,194,278,362,384,388,399,
                         377,386,389,381,372,302,226,158,142,141,136,135],
        "最低用電(kW)": [105,101,97,97,96,98,183,245,309,333,337,343,
                         319,329,333,325,316,257,196,120,110,108,104,103],
    })
    df_load["平均用電(kW)"] = (
        (df_load["最高用電(kW)"] + df_load["最低用電(kW)"]) / 2
    ).round(1)

    _ldcf = st.session_state.get("fmt", {}).get("load_chart_font_size", 13)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_load["時間(時)"], y=df_load["最高用電(kW)"],
        name="最高", mode="lines+markers",
        line=dict(color="#ef4444", width=2)
    ))
    fig.add_trace(go.Scatter(
        x=df_load["時間(時)"], y=df_load["平均用電(kW)"],
        name="平均", mode="lines+markers",
        line=dict(color="#2563a8", width=2, dash="dash")
    ))
    fig.add_trace(go.Scatter(
        x=df_load["時間(時)"], y=df_load["最低用電(kW)"],
        name="最低", mode="lines+markers",
        line=dict(color="#22c55e", width=2),
        fill="tonexty", fillcolor="rgba(37,99,168,.06)"
    ))
    fig.update_layout(
        title=dict(text="廠區 24 小時尖離峰電力負載分佈",
                   font=dict(size=_ldtsz)),
        xaxis=dict(title="時間（時）", tickmode="linear", tick0=1, dtick=1,
                   tickfont=dict(size=_ldcf), title_font=dict(size=_ldcf)),
        yaxis=dict(title="電力負載 (kW)", tickfont=dict(size=_ldcf), title_font=dict(size=_ldcf)),
        legend=dict(font=dict(size=_ldcf)),
        height=420,
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified"
    )
    st.plotly_chart(fig, use_container_width=True)

    pk = df_load.loc[df_load["最高用電(kW)"].idxmax()]
    of = df_load.loc[df_load["最低用電(kW)"].idxmin()]
    pa, pb, pc = st.columns(3)
    pa.metric("🔺 尖峰時段", f"{int(pk['時間(時)'])}:00", f"{pk['最高用電(kW)']} kW")
    pb.metric("🔻 離峰時段", f"{int(of['時間(時)'])}:00", f"{of['最低用電(kW)']} kW")
    pc.metric("📊 負載差異",
              f"{pk['最高用電(kW)'] - of['最低用電(kW)']} kW",
              f"比值 {round(pk['最高用電(kW)']/of['最低用電(kW)'],2):.2f}x")

    st.divider()
    centered_table(df_load, context="load")
    st.download_button(
        "⬇️ 下載負載曲線 CSV",
        df_load.to_csv(index=False).encode("utf-8-sig"),
        "YuanKuan_24H_Load.csv", "text/csv"
    )

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：能源基線追蹤－每月單位產量耗能
# ─────────────────────────────────────────────────────────────────────────────
elif "單位產量耗能" in menu:
    st.subheader("⚡ 能源基線追蹤－每月單位產量耗能")
    st.caption("依 ISO 50001 能源基線（EnB）與能源績效指標（EnPI）追蹤生產能耗強度")
    st.markdown(f"""
    <style>
    div[data-testid="stMetricValue"] {{ font-size: {st.session_state.get("fmt", {}).get("enb_title_size", 16)}px; }}
    </style>
    """, unsafe_allow_html=True)

    MONTHS = list(range(1, 13))
    MONTH_LABELS = [f"{m}月" for m in MONTHS]
    enb_u = st.session_state["enb"]["monthly_unit"]

    # ── 修改模式：逐月填報表單 ──────────────────────────────
    if st.session_state["edit_mode"]:
        with st.expander("✏️ 編輯每月用電量／產量／基線設定", expanded=False):
            with st.form("enb_unit_form"):
                cols = st.columns(4)
                new_kwh, new_prod, new_std, new_up, new_low, new_note = [], [], [], [], [], []
                for i, m in enumerate(MONTHS):
                    with cols[i % 4]:
                        st.markdown(f"**{m} 月**")
                        new_kwh.append(st.number_input(
                            "用電量(kWh)", min_value=0.0,
                            value=float(enb_u["kwh"][i] or 0), key=f"eu_kwh_{m}"))
                        new_prod.append(st.number_input(
                            "產量(噸)", min_value=0.0,
                            value=float(enb_u["production"][i] or 0), key=f"eu_prod_{m}"))
                        new_std.append(st.number_input(
                            "能源標準基線(kWh/噸)", min_value=0.0,
                            value=float(enb_u["std"][i] or 0), key=f"eu_std_{m}"))
                        new_up.append(st.number_input(
                            "基線調整上限(kWh/噸)", min_value=0.0,
                            value=float(enb_u["adj_upper"][i] or 0), key=f"eu_up_{m}"))
                        new_low.append(st.number_input(
                            "基線調整下限(kWh/噸)", min_value=0.0,
                            value=float(enb_u["adj_lower"][i] or 0), key=f"eu_low_{m}"))
                        new_note.append(st.text_input(
                            "備註", value=enb_u["note"][i], key=f"eu_note_{m}"))
                        st.divider()
                if st.form_submit_button("💾 儲存單位產量耗能設定", use_container_width=True):
                    enb_u["kwh"], enb_u["production"] = new_kwh, new_prod
                    enb_u["std"], enb_u["adj_upper"], enb_u["adj_lower"] = new_std, new_up, new_low
                    enb_u["note"] = new_note
                    st.session_state["enb"]["monthly_unit"] = enb_u
                    save_enb(st.session_state["enb"])
                    st.success("✅ 已儲存！")
                    st.rerun()

    # ── 計算單位能耗與超標判斷 ──────────────────────────────
    intensity = []
    for i in range(12):
        k, p = enb_u["kwh"][i], enb_u["production"][i]
        intensity.append(round(k / p, 2) if (k and p) else None)

    valid_vals = [v for v in intensity if v is not None]
    avg_intensity = round(sum(valid_vals) / len(valid_vals), 2) if valid_vals else 0

    exceed_flags = []
    for i in range(12):
        v, up, low = intensity[i], enb_u["adj_upper"][i], enb_u["adj_lower"][i]
        exceed_flags.append(
            v is not None and up is not None and low is not None and (v > up or v < low)
        )

    k1, k2, k3 = st.columns(3)
    k1.metric("年平均單位能耗", f"{avg_intensity:,.2f} kWh/噸")
    k2.metric("⚠️ 超出基線月份數", f"{sum(exceed_flags)} 個月")
    k3.metric("已填報月份數", f"{len(valid_vals)} / 12")

    # ── 折線圖 ──────────────────────────────────────────────
    fig_u = go.Figure()
    _enbcf = st.session_state.get("fmt", {}).get("enb_chart_font_size", 13)
    fig_u.add_trace(go.Scatter(x=MONTH_LABELS, y=intensity, name="單位能耗(kWh/噸)",
                                mode="lines+markers", line=dict(color="#22c55e", width=3)))
    fig_u.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_u["std"], name="能源標準基線",
                                mode="lines", line=dict(color="#fbbf24", width=2, dash="dot")))
    fig_u.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_u["adj_upper"], name="基線調整上限",
                                mode="lines", line=dict(color="#ef4444", width=2)))
    fig_u.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_u["adj_lower"], name="基線調整下限",
                                mode="lines", line=dict(color="#ef4444", width=2)))
    fig_u.update_layout(title=dict(text="每月單位產量耗能", x=0.5,
                         font=dict(size=st.session_state.get("fmt",{}).get("enb_title_size", 16))),
                         xaxis=dict(tickfont=dict(size=_enbcf)), yaxis=dict(tickfont=dict(size=_enbcf)),
                         legend=dict(font=dict(size=_enbcf)),
                         height=420, plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified")
    st.plotly_chart(fig_u, use_container_width=True)

    # ── 資料表 ──────────────────────────────────────────────
    st.divider()
    df_u = pd.DataFrame({
        "月份": MONTH_LABELS,
        "用電量(kWh)": [f"{v:,.0f}" if v else "—" for v in enb_u["kwh"]],
        "產量(噸)": [f"{v:,.2f}" if v else "—" for v in enb_u["production"]],
        "單位能耗(kWh/噸)": [f"{v:,.2f}" if v is not None else "—" for v in intensity],
        "能源標準基線": [f"{v:,.1f}" if v is not None else "—" for v in enb_u["std"]],
        "基線調整上限": [f"{v:,.1f}" if v is not None else "—" for v in enb_u["adj_upper"]],
        "基線調整下限": [f"{v:,.1f}" if v is not None else "—" for v in enb_u["adj_lower"]],
        "備註": enb_u["note"],
    })
    centered_table(df_u, context="enb")

    # ── 差異分析（逐月） ────────────────────────────────────
    st.divider()
    st.subheader("📋 差異分析")
    for i, m in enumerate(MONTHS):
        exceeded = exceed_flags[i]
        warn = "　⚠️ 超出基線範圍，請填寫原因" if (exceeded and not enb_u["reason"][i]) else ""
        icon = "🔴" if exceeded else "⚪"
        with st.expander(f"{icon} {m}月{warn}", expanded=False):
            if st.session_state["edit_mode"]:
                r = st.text_area("原因", value=enb_u["reason"][i], key=f"eu_reason_{m}")
                a = st.text_area("預防／處置", value=enb_u["action"][i], key=f"eu_action_{m}")
                if st.button(f"💾 儲存 {m} 月差異分析", key=f"eu_save_diff_{m}"):
                    enb_u["reason"][i], enb_u["action"][i] = r, a
                    st.session_state["enb"]["monthly_unit"] = enb_u
                    save_enb(st.session_state["enb"])
                    st.success("✅ 已儲存！")
                    st.rerun()
            else:
                st.markdown(f"**原因：** {enb_u['reason'][i] or '—'}")
                st.markdown(f"**預防／處置：** {enb_u['action'][i] or '—'}")

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：能源基線追蹤－整廠用電量
# ─────────────────────────────────────────────────────────────────────────────
elif "整廠用電量" in menu:
    st.subheader("⚡ 能源基線追蹤－整廠用電量")
    st.caption("比較本年度整廠用電量與上年度同期基線及基線調整範圍")
    st.markdown(f"""
    <style>
    div[data-testid="stMetricValue"] {{ font-size: {st.session_state.get("fmt", {}).get("enb_title_size", 16)}px; }}
    </style>
    """, unsafe_allow_html=True)

    MONTHS = list(range(1, 13))
    MONTH_LABELS = [f"{m}月" for m in MONTHS]
    enb_p = st.session_state["enb"]["plant"]
    enb_p.setdefault("reason", [""] * 12)
    enb_p.setdefault("action", [""] * 12)

    # ── 修改模式：逐月填報表單 ──────────────────────────────
    if st.session_state["edit_mode"]:
        with st.expander("✏️ 編輯整廠用電量與基線設定", expanded=False):
            with st.form("enb_plant_form"):
                cols = st.columns(4)
                new_actual, new_base, new_up, new_low = [], [], [], []
                for i, m in enumerate(MONTHS):
                    with cols[i % 4]:
                        st.markdown(f"**{m} 月**")
                        new_actual.append(st.number_input(
                            "本年度整廠電量(kWh)", min_value=0.0,
                            value=float(enb_p["actual"][i] or 0), key=f"pl_act_{m}"))
                        new_base.append(st.number_input(
                            "上年度基線(kWh)", min_value=0.0,
                            value=float(enb_p["baseline_prev"][i] or 0), key=f"pl_base_{m}"))
                        new_up.append(st.number_input(
                            "基線調整上限(kWh)", min_value=0.0,
                            value=float(enb_p["adj_upper"][i] or 0), key=f"pl_up_{m}"))
                        new_low.append(st.number_input(
                            "基線調整下限(kWh)", min_value=0.0,
                            value=float(enb_p["adj_lower"][i] or 0), key=f"pl_low_{m}"))
                        st.divider()
                if st.form_submit_button("💾 儲存整廠用電量設定", use_container_width=True):
                    enb_p["actual"], enb_p["baseline_prev"] = new_actual, new_base
                    enb_p["adj_upper"], enb_p["adj_lower"] = new_up, new_low
                    st.session_state["enb"]["plant"] = enb_p
                    save_enb(st.session_state["enb"])
                    st.success("✅ 已儲存！")
                    st.rerun()

    # ── 計算差值與超標判斷 ──────────────────────────────────
    diff, exceed_flags = [], []
    for i in range(12):
        a, up, low = enb_p["actual"][i], enb_p["adj_upper"][i], enb_p["adj_lower"][i]
        if a is None or up is None or low is None:
            diff.append(None); exceed_flags.append(False)
        else:
            diff.append(round(a - up, 1))
            exceed_flags.append(a > up or a < low)

    valid_actual = [v for v in enb_p["actual"] if v is not None]
    avg_actual = round(sum(valid_actual) / len(valid_actual), 0) if valid_actual else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("年平均整廠用電量", f"{avg_actual:,.0f} kWh")
    k2.metric("⚠️ 超出基線月份數", f"{sum(exceed_flags)} 個月")
    k3.metric("已填報月份數", f"{len(valid_actual)} / 12")

    # ── 折線圖 ──────────────────────────────────────────────
    fig_p = go.Figure()
    _enbcf2 = st.session_state.get("fmt", {}).get("enb_chart_font_size", 13)
    fig_p.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_p["actual"], name="本年度整廠電量(kWh)",
                                mode="lines+markers", line=dict(color="#22c55e", width=3)))
    fig_p.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_p["baseline_prev"], name="能源標準基線-上年度",
                                mode="lines", line=dict(color="#fbbf24", width=2, dash="dot")))
    fig_p.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_p["adj_upper"], name="基線調整上限",
                                mode="lines", line=dict(color="#ef4444", width=2)))
    fig_p.add_trace(go.Scatter(x=MONTH_LABELS, y=enb_p["adj_lower"], name="基線調整下限",
                                mode="lines", line=dict(color="#ef4444", width=2)))
    fig_p.update_layout(title=dict(text="整廠用電量", x=0.5,
                         font=dict(size=st.session_state.get("fmt",{}).get("enb_title_size", 16))),
                         xaxis=dict(tickfont=dict(size=_enbcf2)), yaxis=dict(tickfont=dict(size=_enbcf2)),
                         legend=dict(font=dict(size=_enbcf2)),
                         height=420, plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified")
    st.plotly_chart(fig_p, use_container_width=True)

    # ── 資料表 ──────────────────────────────────────────────
    st.divider()
    df_p = pd.DataFrame({
        "月份": MONTH_LABELS,
        "本年度整廠電量(kWh)": [f"{v:,.0f}" if v is not None else "—" for v in enb_p["actual"]],
        "能源標準基線-上年度(kWh)": [f"{v:,.0f}" if v is not None else "—" for v in enb_p["baseline_prev"]],
        "基線調整上限(kWh)": [f"{v:,.1f}" if v is not None else "—" for v in enb_p["adj_upper"]],
        "基線調整下限(kWh)": [f"{v:,.1f}" if v is not None else "—" for v in enb_p["adj_lower"]],
        "與調整上限差值(kWh)": [f"{v:,.1f}" if v is not None else "—" for v in diff],
    })
    centered_table(df_p, context="enb")

    # ── 差異分析（逐月） ────────────────────────────────────
    st.divider()
    st.subheader("📋 差異分析")
    for i, m in enumerate(MONTHS):
        exceeded = exceed_flags[i]
        warn = "　⚠️ 超出基線範圍，請填寫原因" if (exceeded and not enb_p["reason"][i]) else ""
        icon = "🔴" if exceeded else "⚪"
        with st.expander(f"{icon} {m}月{warn}", expanded=False):
            if st.session_state["edit_mode"]:
                r = st.text_area("原因", value=enb_p["reason"][i], key=f"pl_reason_{m}")
                a = st.text_area("預防／處置", value=enb_p["action"][i], key=f"pl_action_{m}")
                if st.button(f"💾 儲存 {m} 月差異分析", key=f"pl_save_diff_{m}"):
                    enb_p["reason"][i], enb_p["action"][i] = r, a
                    st.session_state["enb"]["plant"] = enb_p
                    save_enb(st.session_state["enb"])
                    st.success("✅ 已儲存！")
                    st.rerun()
            else:
                st.markdown(f"**原因：** {enb_p['reason'][i] or '—'}")
                st.markdown(f"**預防／處置：** {enb_p['action'][i] or '—'}")

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：能源基線追蹤－重大能源使用設備（個別設備，動態偵測）
# ─────────────────────────────────────────────────────────────────────────────
elif "重大設備" in menu:
    st.subheader("⚡ 能源基線追蹤－重大能源使用設備")
    st.caption(
        "個別重大耗能設備（如研磨機、大型攪拌機）各自的能源基線追蹤。"
        "此資料只能透過 Excel 同步，不提供頁面手動編輯——"
        "請切換到「✏️ 修改模式」，至「從Excel重新載入」頁面點擊「同步『重大設備』表」。"
    )
    st.markdown(f"""
    <style>
    div[data-testid="stMetricValue"] {{ font-size: {st.session_state.get("fmt", {}).get("enb_title_size", 16)}px; }}
    </style>
    """, unsafe_allow_html=True)

    equipment_list = st.session_state["enb"].get("equipment", [])

    if not equipment_list:
        st.info("尚未同步任何設備資料，目前清單是空的。請先至「從Excel重新載入」頁面同步一次。")
    else:
        for eq in equipment_list:
            months = eq["months"]
            month_labels = [f"{m}月" for m in months]
            numerator, denominator = eq["numerator"], eq["denominator"]

            ratio = []
            for k in range(len(months)):
                num, den = numerator[k], denominator[k]
                ratio.append(round(num / den, 2) if (num and den) else None)

            valid_ratio = [v for v in ratio if v is not None]
            avg_ratio = round(sum(valid_ratio) / len(valid_ratio), 2) if valid_ratio else 0

            exceed_flags = []
            for k in range(len(months)):
                v, up, low = ratio[k], eq["adj_upper"][k], eq["adj_lower"][k]
                exceed_flags.append(v is not None and up is not None and low is not None
                                     and (v > up or v < low))

            with st.expander(f"⚙️ {eq['title']}", expanded=True):
                k1, k2, k3 = st.columns(3)
                k1.metric("平均值", f"{avg_ratio:,.2f}")
                k2.metric("⚠️ 超出基線月份數", f"{sum(exceed_flags)} 個月")
                k3.metric("已填報月份數", f"{len(valid_ratio)} / {len(months)}")

                fig_e = go.Figure()
                _enbcf3 = st.session_state.get("fmt", {}).get("enb_chart_font_size", 13)
                fig_e.add_trace(go.Scatter(
                    x=month_labels, y=ratio,
                    name=f"{eq['numerator_label']} / {eq['denominator_label']}",
                    mode="lines+markers", line=dict(color="#22c55e", width=3)))
                fig_e.add_trace(go.Scatter(x=month_labels, y=eq["std"], name="能源標準基線",
                                            mode="lines", line=dict(color="#fbbf24", width=2, dash="dot")))
                fig_e.add_trace(go.Scatter(x=month_labels, y=eq["adj_upper"], name="基線調整上限",
                                            mode="lines", line=dict(color="#ef4444", width=2)))
                fig_e.add_trace(go.Scatter(x=month_labels, y=eq["adj_lower"], name="基線調整下限",
                                            mode="lines", line=dict(color="#ef4444", width=2)))
                fig_e.update_layout(title=dict(text=eq["title"], x=0.5,
                                     font=dict(size=st.session_state.get("fmt",{}).get("enb_title_size", 16))),
                                     xaxis=dict(tickfont=dict(size=_enbcf3)), yaxis=dict(tickfont=dict(size=_enbcf3)),
                                     legend=dict(font=dict(size=_enbcf3)),
                                     height=380, plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified")
                st.plotly_chart(fig_e, use_container_width=True)

                df_e = pd.DataFrame({
                    "月份": month_labels,
                    eq["numerator_label"]:   [f"{v:,.2f}" if v is not None else "—" for v in numerator],
                    eq["denominator_label"]: [f"{v:,.2f}" if v is not None else "—" for v in denominator],
                    "比值":       [f"{v:,.2f}" if v is not None else "—" for v in ratio],
                    "能源標準基線": [f"{v:,.2f}" if v is not None else "—" for v in eq["std"]],
                    "基線調整上限": [f"{v:,.2f}" if v is not None else "—" for v in eq["adj_upper"]],
                    "基線調整下限": [f"{v:,.2f}" if v is not None else "—" for v in eq["adj_lower"]],
                })
                centered_table(df_e, context="enb")

                diff = eq.get("diff", {})
                if diff:
                    st.markdown("**📋 差異分析**")
                    for month_num in sorted(diff.keys()):
                        d = diff[month_num]
                        icon = "🔴" if (d.get("reason") or d.get("action")) else "⚪"
                        st.markdown(
                            f"{icon} **{month_num}月** — 原因：{d.get('reason') or '—'}　"
                            f"／　預防處置：{d.get('action') or '—'}"
                        )

# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面四：從 Excel 重新載入
# ─────────────────────────────────────────────────────────────────────────────
elif "Excel" in menu:
    st.subheader("🔄 從 Excel 重新載入資料")
    st.warning("⚠️ 此操作將覆蓋所有已修改的設備資料與照片，請謹慎！")

    if not os.path.exists(EXCEL_FILE):
        st.warning(f"⚠️ 未偵測到 Excel 檔案（雲端環境），目前使用內建示範資料。")
        if st.session_state["edit_mode"]:
            if st.button("🔄 載入內建示範資料", type="primary"):
                st.session_state["db"] = get_builtin_data()
                save_json(st.session_state["db"])
                log_activity("重新載入設備資料", f"載入內建示範資料，共 {len(st.session_state['db'])} 筆")
                st.success(f"✅ 已載入內建資料！共 {len(st.session_state['db'])} 筆。")
                st.rerun()
        else:
            st.info("請切換至「✏️ 修改模式」才能執行重新載入。")
    else:
        st.success(f"✅ 找到 Excel 檔案：{EXCEL_FILE}")
        if st.session_state["edit_mode"]:
            if st.button("🔄 確認重新載入 Excel", type="primary"):
                st.session_state["db"] = init_from_excel()
                save_json(st.session_state["db"])
                log_activity("重新載入設備資料", f"從 Excel 重新載入，共 {len(st.session_state['db'])} 筆")
                st.success(f"✅ 已重新載入！共 {len(st.session_state['db'])} 筆。")
                st.rerun()
        else:
            st.info("請切換至「✏️ 修改模式」才能執行重新載入。")

    st.divider()
    st.markdown(f"目前資料庫共 **{len(st.session_state['db'])}** 筆設備")
    st.markdown(f"本地存檔：`{DB_JSON}`（{'✅ 已存在' if os.path.exists(DB_JSON) else '⚠️ 尚未建立'}）")

    st.divider()
    st.subheader("🔋 能源基線追蹤表（EnB）同步")
    st.caption(
        f"這兩張表是從**另一份** Excel（{ENB_EXCEL_FILE}）讀取，"
        "取名稱同時含「全廠」與「能源基線」字樣的分頁（例如「2025年能源績效指標與能源基線-全廠」），"
        "『每月單位產量耗能』與『整廠用電量』兩張表會疊在同一個分頁裡，"
        "程式會以「整廠用電量」標題自動切開兩張表分別讀取，"
        "右側『差異分析』附表（月份／原因／預防處置）也會一併讀入。"
    )

    if not os.path.exists(ENB_EXCEL_FILE):
        st.info(f"未偵測到「{ENB_EXCEL_FILE}」（或檔名含「各項能源基線追蹤表」的檔案），"
                "能源基線追蹤表暫時只能透過頁面上的表單手動編輯。")
    elif not st.session_state["edit_mode"]:
        st.info("請切換至「✏️ 修改模式」才能執行同步。")
    else:
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("🔄 同步「單位產量耗能」表", use_container_width=True):
                data, err = read_enb_monthly_unit_from_excel()
                if err:
                    st.error(f"❌ {err}")
                else:
                    st.session_state["enb"]["monthly_unit"] = data
                    save_enb(st.session_state["enb"])
                    log_activity("同步EnB", "單位產量耗能")
                    st.success("✅ 已從 Excel 同步「每月單位產量耗能」！")
                    st.rerun()
        with b2:
            if st.button("🔄 同步「整廠用電量」表", use_container_width=True):
                data, err = read_enb_plant_from_excel()
                if err:
                    st.error(f"❌ {err}")
                else:
                    st.session_state["enb"]["plant"] = data
                    save_enb(st.session_state["enb"])
                    log_activity("同步EnB", "整廠用電量")
                    st.success("✅ 已從 Excel 同步「整廠用電量」！")
                    st.rerun()
        with b3:
            if st.button("🔄 同步「重大設備」表", use_container_width=True):
                data, err = read_enb_equipment_from_excel()
                if err:
                    st.error(f"❌ {err}")
                else:
                    st.session_state["enb"]["equipment"] = data
                    save_enb(st.session_state["enb"])
                    log_activity("同步EnB", f"重大設備，共 {len(data)} 組子表")
                    st.success(f"✅ 已從 Excel 同步「重大能源使用設備」！共 {len(data)} 組子表。")
                    st.rerun()
        st.caption(f"目前使用的能源基線 Excel 檔案：`{ENB_EXCEL_FILE}`")
        st.markdown(f"本地存檔：`{ENB_JSON}`（{'✅ 已存在' if os.path.exists(ENB_JSON) else '⚠️ 尚未建立'}）")
        n_eq = len(st.session_state["enb"].get("equipment", []))
        st.markdown(f"目前已同步「重大設備」子表數：**{n_eq}** 組")

    st.divider()
    st.subheader("☁️ Supabase 雲端同步")
    _sb_client = get_supabase_client()
    if not _sb_client:
        st.info(
            "尚未偵測到 Supabase 連線設定。請確認 Streamlit Secrets 裡已設定 "
            "`SUPABASE_URL` 與 `SUPABASE_SERVICE_KEY`（設定完通常幾秒內就會生效，"
            "不需要重新部署）。"
        )
    else:
        st.success("✅ 已連線到 Supabase。")
        st.caption(
            "「上傳」是把目前畫面上看到的資料（含照片）整批寫進 Supabase，覆蓋雲端原本的內容；"
            "「下載」是把 Supabase 裡的資料整批抓回來，覆蓋目前畫面上的內容。"
            "建議規則：**做完一批編輯後上傳一次**；**重新部署或懷疑本地資料被清空時下載一次**。"
            "系統重新啟動、找不到本地存檔時，也會自動嘗試從 Supabase 下載一次。"
        )
        if not st.session_state["edit_mode"]:
            st.info("請切換至「✏️ 修改模式」才能執行同步。")
        else:
            st.markdown("**設備盤查資料（含照片）**")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("⬆️ 上傳設備資料到 Supabase", use_container_width=True, key="sb_push_equip"):
                    with st.spinner("上傳中，含照片可能需要一點時間…"):
                        ok, msg = push_equipment_to_supabase(st.session_state["db"])
                    if ok:
                        log_activity("Supabase上傳", f"設備資料：{msg}")
                        st.success(f"✅ {msg}")
                    else:
                        st.error(f"❌ {msg}")
            with c2:
                if st.button("⬇️ 從 Supabase 下載設備資料", use_container_width=True, key="sb_pull_equip"):
                    with st.spinner("下載中，含照片可能需要一點時間…"):
                        data, err = pull_equipment_from_supabase()
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        st.session_state["db"] = data
                        save_json(data)
                        log_activity("Supabase下載", f"設備資料：共 {len(data)} 筆")
                        st.success(f"✅ 已下載 {len(data)} 筆設備資料！")
                        st.rerun()

            st.markdown("**能源基線追蹤（單位產量耗能／整廠用電量／重大設備）**")
            e1, e2, e3 = st.columns(3)
            with e1:
                if st.button("⬆️⬇️ 單位產量耗能", use_container_width=True, key="sb_unit_menu"):
                    st.session_state["_sb_unit_action"] = True
                if st.session_state.get("_sb_unit_action"):
                    uc1, uc2 = st.columns(2)
                    if uc1.button("上傳", key="sb_push_unit", use_container_width=True):
                        ok, msg = push_enb_monthly_unit_to_supabase(st.session_state["enb"]["monthly_unit"])
                        (st.success if ok else st.error)(msg)
                    if uc2.button("下載", key="sb_pull_unit", use_container_width=True):
                        data, err = pull_enb_monthly_unit_from_supabase()
                        if err:
                            st.error(err)
                        else:
                            st.session_state["enb"]["monthly_unit"] = data
                            save_enb(st.session_state["enb"])
                            st.success("✅ 已下載！")
                            st.rerun()
            with e2:
                if st.button("⬆️⬇️ 整廠用電量", use_container_width=True, key="sb_plant_menu"):
                    st.session_state["_sb_plant_action"] = True
                if st.session_state.get("_sb_plant_action"):
                    pc1, pc2 = st.columns(2)
                    if pc1.button("上傳", key="sb_push_plant", use_container_width=True):
                        ok, msg = push_enb_plant_to_supabase(st.session_state["enb"]["plant"])
                        (st.success if ok else st.error)(msg)
                    if pc2.button("下載", key="sb_pull_plant", use_container_width=True):
                        data, err = pull_enb_plant_from_supabase()
                        if err:
                            st.error(err)
                        else:
                            st.session_state["enb"]["plant"] = data
                            save_enb(st.session_state["enb"])
                            st.success("✅ 已下載！")
                            st.rerun()
            with e3:
                if st.button("⬆️⬇️ 重大設備", use_container_width=True, key="sb_eq_menu"):
                    st.session_state["_sb_eq_action"] = True
                if st.session_state.get("_sb_eq_action"):
                    ec1, ec2 = st.columns(2)
                    if ec1.button("上傳", key="sb_push_eq", use_container_width=True):
                        ok, msg = push_enb_equipment_to_supabase(st.session_state["enb"]["equipment"])
                        (st.success if ok else st.error)(msg)
                    if ec2.button("下載", key="sb_pull_eq", use_container_width=True):
                        data, err = pull_enb_equipment_from_supabase()
                        if err:
                            st.error(err)
                        else:
                            st.session_state["enb"]["equipment"] = data
                            save_enb(st.session_state["enb"])
                            st.success("✅ 已下載！")
                            st.rerun()


    st.error(
        "**重要：**equipment_db.json（含所有網頁上傳的照片）只存在於目前這台伺服器的本地硬碟，"
        "不在 GitHub repo 裡。**只要更新 app.py、重新部署，這個檔案就會被清空**，"
        "回到 Excel 或內建示範資料的狀態。任何時候要更新程式碼之前，請先在這裡「匯出備份」，"
        "部署完成後再回來「還原備份」，才不會遺失網頁上傳的照片與手動編輯的內容。"
    )
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        st.markdown("**匯出備份**")
        backup_bytes = json.dumps(st.session_state["db"], ensure_ascii=False, default=str).encode("utf-8")
        n_photo = sum(1 for r in st.session_state["db"] if r.get("外觀照片") or r.get("銘牌照片"))
        st.caption(f"目前共 {len(st.session_state['db'])} 筆設備，其中 {n_photo} 筆含照片。")
        st.download_button(
            "⬇️ 匯出設備資料庫備份（含照片，JSON）",
            backup_bytes,
            f"equipment_db_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            "application/json",
            use_container_width=True,
        )
    with bcol2:
        st.markdown("**還原備份**")
        if not st.session_state["edit_mode"]:
            st.info("請切換至「✏️ 修改模式」才能還原備份。")
        else:
            up_backup = st.file_uploader("選擇之前匯出的備份 JSON 檔", type=["json"], key="restore_backup_uploader")
            if up_backup is not None:
                try:
                    restored = json.loads(up_backup.read().decode("utf-8"))
                    if not isinstance(restored, list):
                        st.error("❌ 檔案格式不正確：預期是一份設備清單（JSON 陣列）。")
                    else:
                        r_photo = sum(1 for r in restored if isinstance(r, dict) and (r.get("外觀照片") or r.get("銘牌照片")))
                        st.warning(f"⚠️ 偵測到備份檔含 {len(restored)} 筆設備，其中 {r_photo} 筆含照片。"
                                   f"還原後將**覆蓋目前的 {len(st.session_state['db'])} 筆資料**，此操作無法復原。")
                        if st.button("✅ 確認還原（覆蓋目前資料）", type="primary", use_container_width=True):
                            st.session_state["db"] = restored
                            save_json(st.session_state["db"])
                            log_activity("還原設備資料庫備份", f"還原 {len(restored)} 筆設備（{r_photo} 筆含照片）")
                            st.success("✅ 已還原！")
                            st.rerun()
                except Exception as e:
                    st.error(f"❌ 無法解析備份檔：{e}")

    st.divider()
    st.subheader("🗜️ 批次壓縮既有照片")
    st.caption(
        f"新上傳的照片現在會自動壓縮（長邊限制 {PHOTO_MAX_DIM}px、JPEG 品質 {PHOTO_QUALITY}），"
        "但資料庫裡舊的、還沒套用這個規則的大尺寸照片不會自動變小。點下面的按鈕可以一次全部重新壓縮。"
    )
    if not st.session_state["edit_mode"]:
        st.info("請切換至「✏️ 修改模式」才能執行批次壓縮。")
    else:
        total_kb = sum(
            len(base64.b64decode(rec[k])) / 1024
            for rec in st.session_state["db"] for k in ("外觀照片", "銘牌照片") if rec.get(k)
        )
        n_photos = sum(1 for rec in st.session_state["db"] for k in ("外觀照片", "銘牌照片") if rec.get(k))
        st.markdown(f"目前資料庫共 **{n_photos}** 張照片，總大小約 **{total_kb/1024:,.1f} MB**。")
        if st.button("🗜️ 壓縮資料庫中所有照片", use_container_width=True):
            total_before = total_after = 0.0
            n_changed = 0
            with st.spinner("壓縮中，請稍候…"):
                for rec in st.session_state["db"]:
                    for key in ("外觀照片", "銘牌照片"):
                        b64 = rec.get(key)
                        if not b64:
                            continue
                        raw = base64.b64decode(b64)
                        before_kb = len(raw) / 1024
                        compressed = compress_photo_bytes(raw)
                        after_kb = len(compressed) / 1024
                        total_before += before_kb
                        total_after += after_kb
                        if after_kb < before_kb * 0.95:   # 已經很小的圖就不重複處理
                            rec[key] = base64.b64encode(compressed).decode()
                            n_changed += 1
                save_json(st.session_state["db"])
            log_activity("批次壓縮照片",
                         f"處理 {n_changed} 張，總大小 {total_before/1024:.1f}MB → {total_after/1024:.1f}MB")
            st.success(f"✅ 完成！共重新壓縮 {n_changed} 張照片，"
                       f"總大小從 {total_before/1024:,.1f}MB 降到 {total_after/1024:,.1f}MB。")
            st.rerun()

    st.divider()
    st.subheader("📜 操作日誌")
    st.caption(
        "記錄登入、設備新增／編輯／刪除、Excel 重新載入與 EnB 同步等關鍵操作，"
        "方便事後追查是誰在什麼時候改了什麼（目前系統共用一組管理員密碼，"
        "操作者暫時只會顯示「管理員」；日後若改成個人帳密登入，可再擴充成記錄實際使用者）。"
    )
    logs = load_activity_log()
    if not logs:
        st.info("目前沒有任何操作紀錄。")
    else:
        max_n = len(logs)
        if max_n <= 5:
            n_show = max_n
        else:
            n_show = st.slider("顯示最近幾筆", 5, min(200, max_n), min(20, max_n))
        df_log = pd.DataFrame(logs[-n_show:][::-1])
        df_log.columns = ["時間", "動作", "詳細內容"]
        centered_table(df_log, context="log")
        st.download_button(
            "⬇️ 下載完整操作日誌 CSV",
            pd.DataFrame(logs).to_csv(index=False).encode("utf-8-sig"),
            "activity_log.csv", "text/csv"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ── 頁面：版面格式設定（僅管理員可見）
# ─────────────────────────────────────────────────────────────────────────────
elif "版面格式" in menu:
    if not st.session_state["logged_in"]:
        st.warning("請先登入管理員帳號")
        st.stop()

    st.subheader("🎨 版面格式設定")
    st.info("調整後即時套用至全站，並自動儲存，重新整理或重新部署後仍會保留。")

    fmt = st.session_state["fmt"]

    FONTS = [
        "Noto Sans TC",
        "Microsoft JhengHei",
        "Arial",
        "Georgia",
        "Courier New",
        "Times New Roman",
    ]

    # ── 全域設定
    with st.expander("🌐 全域字型設定", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            ff = st.selectbox("全站字型", FONTS,
                              index=FONTS.index(fmt.get("font_family","Noto Sans TC"))
                              if fmt.get("font_family","Noto Sans TC") in FONTS else 0)
            fmt["font_family"] = ff
        with col2:
            st.markdown(f"""
<div style='font-family:{ff};font-size:16px;padding:12px;
            background:#f8fafc;border-radius:8px;margin-top:22px'>
  預覽：永寬化學 ISO 50001 管理系統<br>
  <span style='font-size:13px;color:#64748b'>ABCDE abcde 12345</span>
</div>""", unsafe_allow_html=True)

    # ── 各頁面設定 tabs
    t1, t2, t3, t4, t5, t6 = st.tabs([
        "📊 儀表板", "🗂️ 設備盤查", "📐 評分標準", "⚡ 能源換算", "📈 負載分析", "🔋 能源基線追蹤"
    ])

    with t1:
        st.markdown("#### 儀表板頁面")
        st.caption("標題：KPI數字／提示框數字／圖表標題　｜　圖片字體：圖例、座標軸、數據標籤　｜　表格：表格字體")
        c1, c2, c3 = st.columns(3)
        with c1:
            fmt["dash_title_size"] = st.slider(
                "標題", 10, 48, fmt.get("dash_title_size", 28), 1, key="dts_title")
        with c2:
            fmt["dash_chart_font_size"] = st.slider(
                "圖片字體", 8, 24, fmt.get("dash_chart_font_size", 13), 1, key="dts_chart")
        with c3:
            fmt["dash_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("dash_table_size", 14), 1, key="dts")
        fmt["dash_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"], index=0 if fmt.get("dash_table_align","center")=="center" else 1,
            horizontal=True, key="dta") == "置中" else "left"

        # Preview
        st.markdown("---")
        st.markdown("**預覽效果**")
        preview_df = pd.DataFrame({
            "系統別": ["製程系統","空調系統","照明系統"],
            "耗電量(kWh)": ["895,644","525,255","116,778"],
            "佔比(%)": ["52.0%","30.5%","6.8%"],
            "A級設備": ["16","2","0"],
        })
        centered_table(preview_df, context="dash")

    with t2:
        st.markdown("#### 設備盤查頁面")
        st.caption("標題：展開卡標題／摘要卡片數字／設備明細數字　｜　表格：設備資料表格字體（此頁無圖表）")
        c1, c2 = st.columns(2)
        with c1:
            fmt["equip_title_size"] = st.slider(
                "標題", 10, 36, fmt.get("equip_title_size", 14), 1, key="ets_title")
        with c2:
            fmt["equip_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("equip_table_size", 14), 1, key="ets")
        fmt["equip_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"],
            index=0 if fmt.get("equip_table_align","center")=="center" else 1,
            horizontal=True, key="ea") == "置中" else "left"

    with t3:
        st.markdown("#### 評分標準頁面")
        st.caption("標題：章節標題文字　｜　表格：評分表字體（此頁無圖表）")
        c1, c2 = st.columns(2)
        with c1:
            fmt["score_title_size"] = st.slider(
                "標題", 10, 36, fmt.get("score_title_size", 18), 1, key="sts_title")
        with c2:
            fmt["score_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("score_table_size", 14), 1, key="sts")
        fmt["score_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"],
            index=0 if fmt.get("score_table_align","center")=="center" else 1,
            horizontal=True, key="sta") == "置中" else "left"
        st.markdown("---")
        st.markdown("**預覽效果**")
        preview_score = pd.DataFrame({
            "耗能估比範圍": ["— ～ 0.24%","0.25% ～ 0.49%","0.50% ～ 0.74%","0.75% ～","1.00% ～"],
            "分數": [1,2,3,4,5],
        })
        centered_table(preview_score, context="score")

    with t4:
        st.markdown("#### 能源換算頁面")
        st.caption("標題：KPI數字／圖表標題　｜　圖片字體：圖例、數據標籤　｜　表格：換算表字體")
        c1, c2, c3 = st.columns(3)
        with c1:
            fmt["energy_title_size"] = st.slider(
                "標題", 10, 36, fmt.get("energy_title_size", 16), 1, key="ents_title")
        with c2:
            fmt["energy_chart_font_size"] = st.slider(
                "圖片字體", 8, 24, fmt.get("energy_chart_font_size", 13), 1, key="ents_chart")
        with c3:
            fmt["energy_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("energy_table_size", 14), 1, key="ents")
        fmt["energy_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"],
            index=0 if fmt.get("energy_table_align","center")=="center" else 1,
            horizontal=True, key="enta") == "置中" else "left"

    with t5:
        st.markdown("#### 每日負載分析頁面")
        st.caption("標題：KPI數字／圖表標題　｜　圖片字體：圖例、座標軸　｜　表格：負載曲線表字體")
        c1, c2, c3 = st.columns(3)
        with c1:
            fmt["load_title_size"] = st.slider(
                "標題", 10, 36, fmt.get("load_title_size", 16), 1, key="lts_title")
        with c2:
            fmt["load_chart_font_size"] = st.slider(
                "圖片字體", 8, 24, fmt.get("load_chart_font_size", 13), 1, key="lts_chart")
        with c3:
            fmt["load_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("load_table_size", 14), 1, key="lts")
        fmt["load_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"],
            index=0 if fmt.get("load_table_align","center")=="center" else 1,
            horizontal=True, key="lta") == "置中" else "left"

    with t6:
        st.markdown("#### 能源基線追蹤頁面")
        st.caption("套用到：能源基線追蹤-單位產量耗能／整廠用電量／重大設備 這三個頁面　｜　"
                    "標題：KPI數字／圖表標題　｜　圖片字體：圖例、座標軸　｜　表格：資料表字體")
        c1, c2, c3 = st.columns(3)
        with c1:
            fmt["enb_title_size"] = st.slider(
                "標題", 10, 36, fmt.get("enb_title_size", 16), 1, key="enbts_title")
        with c2:
            fmt["enb_chart_font_size"] = st.slider(
                "圖片字體", 8, 24, fmt.get("enb_chart_font_size", 13), 1, key="enbts_chart")
        with c3:
            fmt["enb_table_size"] = st.slider(
                "表格字體", 10, 36, fmt.get("enb_table_size", 14), 1, key="enbts")
        fmt["enb_table_align"] = "center" if st.radio(
            "表格對齊", ["置中","靠左"],
            index=0 if fmt.get("enb_table_align","center")=="center" else 1,
            horizontal=True, key="enbta") == "置中" else "left"

    st.session_state["fmt"] = fmt
    save_layout(fmt)

    # ── 儲存／重設按鈕
    st.divider()
    col_s, col_r, col_info = st.columns([1, 1, 3])
    with col_s:
        if st.button("💾 儲存設定", type="primary", use_container_width=True):
            save_layout(fmt)
            log_activity("儲存版面設定", "")
            st.success("✅ 已儲存！")
    with col_r:
        if st.button("🔄 恢復全部預設值", type="secondary", use_container_width=True):
            default_fmt = {
                "dash_title_size": 28, "dash_chart_font_size": 13,
                "dash_table_size": 14, "dash_table_align": "center",
                "equip_title_size": 14, "equip_table_size": 14, "equip_table_align": "center",
                "score_title_size": 18, "score_table_size": 14, "score_table_align": "center",
                "energy_title_size": 16, "energy_chart_font_size": 13,
                "energy_table_size": 14, "energy_table_align": "center",
                "load_title_size": 16, "load_chart_font_size": 13,
                "load_table_size": 14, "load_table_align": "center",
                "enb_title_size": 16, "enb_chart_font_size": 13,
                "enb_table_size": 14, "enb_table_align": "center",
                "font_family": "Noto Sans TC",
            }
            st.session_state["fmt"] = default_fmt
            save_layout(default_fmt)
            log_activity("恢復版面預設值", "")
            st.success("✅ 已恢復預設值！")
            st.rerun()
    with col_info:
        st.markdown("""
<div style='background:#f0fdf4;padding:10px 14px;border-radius:8px;
            border-left:4px solid #22c55e;font-size:13px;margin-top:4px'>
  💡 拖動滑桿當下就會自動存檔；「儲存設定」按鈕是額外提供明確的存檔確認，兩者效果相同。
</div>""", unsafe_allow_html=True)

    st.divider()
    with st.expander("🔍 除錯用：目前實際生效的設定值（如果調整滑桿沒有效果，把這裡展開後截圖給開發人員）"):
        st.caption(
            "這裡顯示的是系統目前實際記憶體中使用的完整設定值。"
            "如果你剛剛調整過某個滑桿、上面的數字卻沒有跟著變，代表設定沒有正確寫入，是真的問題；"
            "如果這裡的數字是對的，但畫面上看起來沒變，通常代表瀏覽器頁面需要重新整理，"
            "或者 GitHub 上傳的還不是最新版 app.py。"
        )
        st.json(st.session_state.get("fmt", {}))
        st.caption(f"版面設定存檔（layout_settings.json）是否存在：{os.path.exists(LAYOUT_JSON)}")
