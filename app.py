import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import re
from io import BytesIO

# ==========================================
# 1. AYARLAR & CSS
# ==========================================
st.set_page_config(page_title="RecoMatch", layout="wide", page_icon="🛡️")

st.markdown("""
<style>
    .main {background-color: #f8f9fa;}
    .stDataFrame {border: 1px solid #dee2e6; border-radius: 4px;}
    div[data-testid="stExpander"] {background: white; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);}
    
    .mini-table {
        width: 100%; border-collapse: collapse; font-size: 0.85rem; 
        background: white; border-radius: 8px; overflow: hidden; 
        box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 1rem;
    }
    .mini-table th {
        background: #1e3a8a; color: white; text-align: right; 
        padding: 10px 8px; font-weight: 600; white-space: nowrap;
    }
    .mini-table th:first-child { text-align: left; }
    .mini-table td {
        padding: 8px 8px; text-align: right; border-bottom: 1px solid #f3f4f6;
        color: #374151; font-family: 'Segoe UI Mono', monospace;
    }
    .mini-table td:first-child { text-align: left; font-family: sans-serif; font-weight: 600; color: #111827; }
    
    .pos-val { color: #059669; font-weight: 700; }
    .neg-val { color: #dc2626; font-weight: 700; }
    .neu-val { color: #9ca3af; }
    .border-left-thick { border-left: 2px solid #e5e7eb; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. TEMPLATE MANAGER
# ==========================================
TEMPLATE_FILE = "recomatch_memory.json"

class TemplateManager:
    @staticmethod
    def load():
        if os.path.exists(TEMPLATE_FILE):
            try:
                with open(TEMPLATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
            except: return {}
        return {}

    @staticmethod
    def update_template(filename, mapping):
        templates = TemplateManager.load()
        key = filename.split('_')[0].lower()
        if len(key) < 3: key = filename.lower()
        templates[key] = mapping
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)

    @staticmethod
    def find_best_match(filename):
        templates = TemplateManager.load()
        for key, val in templates.items():
            if key in filename.lower(): return val
        return {}

# ==========================================
# 3. YARDIMCI FONKSİYONLAR (GÜÇLENDİRİLMİŞ)
# ==========================================
def normalize_text(s):
    if pd.isna(s): return ""
    s = str(s).strip().upper()
    s = s.replace(" ", "").replace("O", "0")
    return s

def normalize_currency(val):
    """TRY, TRL, TL ayrımlarını birleştirir."""
    if pd.isna(val): return "TL"
    s = str(val).strip().upper().replace(" ", "").replace(".", "")
    
    # Eşanlamlılar Sözlüğü
    if s in ["TRY", "TRL", "TURKLIRASI", "TÜRKLIRASI", "TL"]: return "TL"
    if s in ["USD", "ABDDOLARI", "USDOLLAR", "DOLAR"]: return "USD"
    if s in ["EUR", "EURO", "AVRO"]: return "EUR"
    if s in ["GBP", "STERLIN"]: return "GBP"
    if s in ["CHF", "ISVICREFRANGI"]: return "CHF"
    return s

def get_invoice_key(raw_val):
    val_str = str(raw_val)
    if val_str.endswith('.0'): val_str = val_str[:-2]
    clean = re.sub(r'[^A-Z0-9]', '', normalize_text(val_str))
    return clean

def parse_amount(val):
    if pd.isna(val) or val == "": return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).strip()
    is_neg = s.startswith("-") or ("(" in s and ")" in s)
    s = re.sub(r"[^\d.,]", "", s)
    if not s: return 0.0
    try:
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."): s = s.replace(".", "").replace(",", ".")
            else: s = s.replace(",", "")
        elif "," in s: s = s.replace(",", ".")
        f = float(s)
        return -f if is_neg else f
    except: return 0.0

def read_and_merge(uploaded_files):
    if not uploaded_files: return pd.DataFrame()
    df_list = []
    for f in uploaded_files:
        try:
            # Dosya uzantısına göre oku
            if f.name.lower().endswith(".csv"):
                temp_df = pd.read_csv(f, dtype=str)
            else:
                temp_df = pd.read_excel(f, header=0, dtype=str)
                
            # Kolon temizliği
            temp_df.columns = temp_df.columns.astype(str).str.strip()
            temp_df["Satır_No"] = temp_df.index + 2
            
            # İçerik temizliği (Whitespace)
            for col in temp_df.columns:
                temp_df[col] = temp_df[col].astype(str).str.strip()
                # 'nan' stringlerini temizle
                temp_df[col] = temp_df[col].replace({'nan': '', 'None': ''})
                
            temp_df["Kaynak_Dosya"] = f.name
            df_list.append(temp_df)
        except Exception as e:
            st.error(f"Dosya okuma hatası ({f.name}): {e}")
    if not df_list: return pd.DataFrame()
    return pd.concat(df_list, ignore_index=True)

# ==========================================
# 4. HESAPLAMA MANTIĞI
# ==========================================
def calculate_smart_balance(row, role, 
                            mode_tl, c_tl_debt, c_tl_credit, c_tl_single, is_tl_signed,
                            mode_fx, c_fx_debt, c_fx_credit, c_fx_single, is_fx_signed,
                            doc_cat):
    
    # 1. Varsayılan İşaret
    calc_sign = 1
    if role == "Biz Alıcı":
        if doc_cat in ["FATURA", "IADE_ODEME"]: calc_sign = 1 
        else: calc_sign = -1 
    else: # Biz Satıcı
        if doc_cat in ["FATURA", "IADE_ODEME"]: calc_sign = -1 
        else: calc_sign = 1 

    # --- TL ---
    tl_debt_val = 0.0
    tl_credit_val = 0.0
    tl_net = 0.0
    
    if mode_tl == "separate":
        tl_debt_val = parse_amount(row.get(c_tl_debt, 0))
        tl_credit_val = parse_amount(row.get(c_tl_credit, 0))
        tl_net = tl_credit_val - tl_debt_val
    else:
        raw_tl = parse_amount(row.get(c_tl_single, 0))
        if is_tl_signed: tl_net = raw_tl
        else: tl_net = raw_tl * calc_sign

    # --- FX ---
    fx_net = 0.0
    if mode_fx == "separate":
        f_d = parse_amount(row.get(c_fx_debt, 0))
        f_c = parse_amount(row.get(c_fx_credit, 0))
        fx_net = f_c - f_d
    elif mode_fx == "single":
        raw_fx = parse_amount(row.get(c_fx_single, 0))
        if raw_fx != 0:
            if is_fx_signed:
                fx_net = raw_fx
            else:
                # Akıllı Yön Tespiti (TL'ye bak)
                if mode_tl == "separate":
                    if tl_debt_val > 0: fx_net = -abs(raw_fx)
                    elif tl_credit_val > 0: fx_net = abs(raw_fx)
                    else: fx_net = raw_fx * calc_sign
                elif mode_tl == "single" and is_tl_signed:
                    if tl_net < 0: fx_net = -abs(raw_fx)
                    elif tl_net > 0: fx_net = abs(raw_fx)
                    else: fx_net = raw_fx * calc_sign
                else:
                    fx_net = raw_fx * calc_sign

    return tl_net, fx_net

def get_doc_category(val, cfg):
    val = normalize_text(val)
    if val in [normalize_text(x) for x in cfg.get("FATURA", [])]: return "FATURA"
    elif val in [normalize_text(x) for x in cfg.get("ODEME", [])]: return "ODEME"
    elif val in [normalize_text(x) for x in cfg.get("IADE_FATURA", [])]: return "IADE_FATURA"
    elif val in [normalize_text(x) for x in cfg.get("IADE_ODEME", [])]: return "IADE_ODEME"
    return "DIGER"

def prepare_data(df, mapping, role):
    if df.empty: return df
    df = df.copy()
    c_date = mapping.get("date")
    if c_date and c_date in df.columns:
        df["std_date"] = pd.to_datetime(df[c_date], dayfirst=True, errors='coerce').dt.date
    else: df["std_date"] = None

    c_type = mapping.get("doc_type")
    type_cfg = mapping.get("type_vals", {})
    if c_type and c_type in df.columns:
        df["Doc_Category"] = df[c_type].apply(lambda x: get_doc_category(x, type_cfg))
    else: df["Doc_Category"] = "DIGER"

    def wrapper(r):
        return calculate_smart_balance(
            r, role,
            mapping.get("amount_mode", "single"), 
            mapping.get("col_debt"), mapping.get("col_credit"), mapping.get("col_amount"), mapping.get("is_tl_signed"),
            mapping.get("fx_amount_mode", "none"),
            mapping.get("col_fx_debt"), mapping.get("col_fx_credit"), mapping.get("col_fx_amount"), mapping.get("is_fx_signed"),
            r["Doc_Category"]
        )
    
    res = df.apply(wrapper, axis=1, result_type='expand')
    df["Signed_TL"] = res[0]
    df["Signed_FX"] = res[1]

    # Para Birimi Normalizasyonu
    c_curr = mapping.get("curr")
    if c_curr and c_curr in df.columns:
        df["PB_Norm"] = df[c_curr].apply(normalize_currency)
        df["PB_Norm"] = df["PB_Norm"].replace("", "TL").fillna("TL")
    else: df["PB_Norm"] = "TL"

    c_inv = mapping.get("inv_no")
    if c_inv and c_inv in df.columns:
        df["key_invoice_norm"] = df[c_inv].apply(get_invoice_key)
    else: df["key_invoice_norm"] = ""
    return df

# ==========================================
# 5. UI & MAPPING
# ==========================================
def render_mapping_ui(title, df, default_map, key_prefix):
    st.markdown(f"#### {title} Ayarları")
    cols = ["Seçiniz..."] + list(df.columns)
    def idx(c): return cols.index(c) if c in cols else 0

    st.caption("Yerel (TL) Tutar")
    amount_mode = st.radio(f"{title} TL Mod", ["Tek Kolon", "Ayrı (Borç/Alacak)"], 
                           index=0 if default_map.get("amount_mode") != "separate" else 1, horizontal=True, key=f"{key_prefix}_mode")
    mode_tl = "single" if amount_mode == "Tek Kolon" else "separate"
    
    c_tl_d, c_tl_c, c_tl_s, is_tl_sign = None, None, None, False
    if mode_tl == "separate":
        c1, c2 = st.columns(2)
        with c1: c_tl_d = st.selectbox("TL Borç", cols, index=idx(default_map.get("col_debt")), key=f"{key_prefix}_debt")
        with c2: c_tl_c = st.selectbox("TL Alacak", cols, index=idx(default_map.get("col_credit")), key=f"{key_prefix}_credit")
    else:
        c_tl_s = st.selectbox("TL Tutar", cols, index=idx(default_map.get("col_amount")), key=f"{key_prefix}_amt")
        is_tl_sign = st.checkbox("Tutarlar Excel'de zaten (+/-) işaretli", 
                                 value=default_map.get("is_tl_signed", False), key=f"{key_prefix}_tlsign")

    st.caption("Döviz (FX) Tutar")
    fx_opt = st.radio(f"{title} Döviz Mod", ["Yok", "Tek Kolon", "Ayrı (Borç/Alacak)"],
                      index=0 if default_map.get("fx_amount_mode", "none") == "none" else (1 if default_map.get("fx_amount_mode") == "single" else 2),
                      horizontal=True, key=f"{key_prefix}_fx_opt")
    mode_fx = "none"
    c_fx_d, c_fx_c, c_fx_s, is_fx_sign = None, None, None, False
    if fx_opt == "Tek Kolon":
        mode_fx = "single"
        c_fx_s = st.selectbox("Döviz Tutar", cols, index=idx(default_map.get("col_fx_amount")), key=f"{key_prefix}_fx_amt")
        is_fx_sign = st.checkbox("Döviz tutarı zaten (+/-) işaretli", 
                                 value=default_map.get("is_fx_signed", False), key=f"{key_prefix}_fxsign")
    elif fx_opt == "Ayrı (Borç/Alacak)":
        mode_fx = "separate"
        f1, f2 = st.columns(2)
        with f1: c_fx_d = st.selectbox("Döviz Borç", cols, index=idx(default_map.get("col_fx_debt")), key=f"{key_prefix}_fx_debt")
        with f2: c_fx_credit = st.selectbox("Döviz Alacak", cols, index=idx(default_map.get("col_fx_credit")), key=f"{key_prefix}_fx_credit")

    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1: c_inv = st.selectbox("Fatura No", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
    with c2: c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
    with c3: c_curr = st.selectbox("Para Birimi", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
    c_pay = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")

    st.markdown("---")
    c_type = st.selectbox("Belge Türü", cols, index=idx(default_map.get("doc_type")), key=f"{key_prefix}_type")
    sel_types = {"FATURA": [], "IADE_FATURA": [], "ODEME": [], "IADE_ODEME": []}
    if c_type != "Seçiniz...":
        vals = sorted([str(x) for x in df[c_type].unique() if pd.notna(x)])
        d_t = default_map.get("type_vals", {})
        with st.expander(f"📂 {title} - Tür Eşleştirme", expanded=False):
            c_f, c_o = st.columns(2)
            with c_f:
                sel_types["FATURA"] = st.multiselect("Faturalar", vals, default=d_t.get("FATURA", []), key=f"{key_prefix}_mf")
                sel_types["IADE_FATURA"] = st.multiselect("İade Faturalar", vals, default=d_t.get("IADE_FATURA", []), key=f"{key_prefix}_mif")
            with c_o:
                sel_types["ODEME"] = st.multiselect("Ödemeler", vals, default=d_t.get("ODEME", []), key=f"{key_prefix}_mo")
                sel_types["IADE_ODEME"] = st.multiselect("İade Ödemeler", vals, default=d_t.get("IADE_ODEME", []), key=f"{key_prefix}_mio")

    extra = st.multiselect("İlave Kolonlar", [c for c in cols if c != "Seçiniz..."], default=default_map.get("extra_cols", []), key=f"{key_prefix}_extra")

    def cln(v): return None if v == "Seçiniz..." else v
    return {
        "amount_mode": mode_tl, "col_debt": cln(c_tl_d), "col_credit": cln(c_tl_c), "col_amount": cln(c_tl_s), "is_tl_signed": is_tl_sign,
        "fx_amount_mode": mode_fx, "col_fx_debt": cln(c_fx_d), "col_fx_credit": cln(c_fx_c), "col_fx_amount": cln(c_fx_s), "is_fx_signed": is_fx_sign,
        "inv_no": cln(c_inv), "date": cln(c_date), "curr": cln(c_curr),
        "pay_no": cln(c_pay), "doc_type": cln(c_type), "type_vals": sel_types, "extra_cols": extra
    }

# ==========================================
# 6. GÖRÜNTÜ FORMATLAYICI (EKSİKSİZ VERİ)
# ==========================================
def format_clean_view(df, map_our, map_their, type="FATURA"):
    if df.empty: return df
    cols_our, rename_our = [], {}
    
    # Bizim Taraf
    if "Kaynak_Dosya_Biz" in df.columns: cols_our.append("Kaynak_Dosya_Biz"); rename_our["Kaynak_Dosya_Biz"] = "Kaynak (Biz)"
    if "Satır_No_Biz" in df.columns: cols_our.append("Satır_No_Biz"); rename_our["Satır_No_Biz"] = "Satır (Biz)"
    
    our_inv = map_our.get("inv_no")
    if our_inv and (our_inv + "_Biz") in df.columns:
        cols_our.append(our_inv + "_Biz")
        rename_our[our_inv + "_Biz"] = "Fatura No (Biz)" if type == "FATURA" else "İlgili Fatura (Biz)"

    our_pay = map_our.get("pay_no")
    if type != "FATURA" and our_pay and (our_pay + "_Biz") in df.columns:
        cols_our.append(our_pay + "_Biz")
        rename_our[our_pay + "_Biz"] = "Ödeme/Açık. (Biz)"

    cols_our.extend(["std_date_Biz", "Signed_TL_Biz", "Signed_FX_Biz"])
    rename_our.update({"std_date_Biz": "Tarih (Biz)", "Signed_TL_Biz": "Tutar TL (Biz)", "Signed_FX_Biz": "Tutar FX (Biz)"})
    
    if map_our.get("curr") and (map_our.get("curr")+"_Biz" in df.columns):
        cols_our.append(map_our.get("curr")+"_Biz"); rename_our[map_our.get("curr")+"_Biz"] = "PB (Biz)"
        
    for ec in map_our.get("extra_cols", []):
        if (ec+"_Biz") in df.columns:
            cols_our.append(ec+"_Biz"); rename_our[ec+"_Biz"] = f"{ec} (Biz)"

    # Karşı Taraf
    cols_their, rename_their = [], {}
    if "Kaynak_Dosya_Onlar" in df.columns: cols_their.append("Kaynak_Dosya_Onlar"); rename_their["Kaynak_Dosya_Onlar"] = "Kaynak (Onlar)"
    if "Satır_No_Onlar" in df.columns: cols_their.append("Satır_No_Onlar"); rename_their["Satır_No_Onlar"] = "Satır (Onlar)"

    their_inv = map_their.get("inv_no")
    if their_inv and (their_inv + "_Onlar") in df.columns:
        cols_their.append(their_inv + "_Onlar")
        rename_their[their_inv + "_Onlar"] = "Fatura No (Onlar)" if type == "FATURA" else "İlgili Fatura (Onlar)"

    their_pay = map_their.get("pay_no")
    if type != "FATURA" and their_pay and (their_pay + "_Onlar") in df.columns:
        cols_their.append(their_pay + "_Onlar")
        rename_their[their_pay + "_Onlar"] = "Ödeme/Açık. (Onlar)"

    cols_their.extend(["std_date_Onlar", "Signed_TL_Onlar", "Signed_FX_Onlar"])
    rename_their.update({"std_date_Onlar": "Tarih (Onlar)", "Signed_TL_Onlar": "Tutar TL (Onlar)", "Signed_FX_Onlar": "Tutar FX (Onlar)"})

    if map_their.get("curr") and (map_their.get("curr")+"_Onlar" in df.columns):
        cols_their.append(map_their.get("curr")+"_Onlar"); rename_their[map_their.get("curr")+"_Onlar"] = "PB (Onlar)"

    for ec in map_their.get("extra_cols", []):
        if (ec+"_Onlar") in df.columns:
            cols_their.append(ec+"_Onlar"); rename_their[ec+"_Onlar"] = f"{ec} (Onlar)"

    final_cols = cols_our + cols_their + ["Fark_TL", "Fark_FX"]
    final_rename = {**rename_our, **rename_their, "Fark_TL": "Fark (TL)", "Fark_FX": "Fark (FX)"}
    
    existing = [c for c in final_cols if c in df.columns]
    return df[existing].rename(columns=final_rename)

# ==========================================
# 7. MAIN FLOW
# ==========================================
def force_suffix(df, suffix, key_col):
    new_cols = {}
    for c in df.columns:
        if c == key_col: continue
        new_cols[c] = f"{c}{suffix}"
    return df.rename(columns=new_cols)

with st.sidebar:
    st.header("RecoMatch 🛡️")
    role = st.selectbox("Bizim Rolümüz", ["Biz Alıcı", "Biz Satıcı"])
    st.divider()
    files_our = st.file_uploader("Bizim Ekstreler", accept_multiple_files=True)
    files_their = st.file_uploader("Karşı Taraf Ekstreler", accept_multiple_files=True)
    st.divider()
    pay_scenario = st.radio("Ödeme Eşleşme", ["Tarih + Ödeme No + Tutar", "Tarih + Belge Türü + Tutar"])
    analyze_btn = st.button("Analizi Başlat", type="primary", use_container_width=True)

if files_our and files_their:
    df_our = read_and_merge(files_our)
    df_their = read_and_merge(files_their)
    saved_our = TemplateManager.find_best_match(files_our[0].name)
    saved_their = TemplateManager.find_best_match(files_their[0].name)
    
    c1, c2 = st.columns(2)
    with c1: map_our = render_mapping_ui("Bizim Taraf", df_our, saved_our, "our")
    with c2: map_their = render_mapping_ui("Karşı Taraf", df_their, saved_their, "their")

    if analyze_btn:
        try:
            if not map_our.get("inv_no") or not map_their.get("inv_no"):
                st.error("HATA: 'Fatura No' seçimi zorunludur!")
                st.stop()

            TemplateManager.update_template(files_our[0].name, map_our)
            TemplateManager.update_template(files_their[0].name, map_their)
            
            with st.spinner("Hesaplanıyor..."):
                prep_our = prepare_data(df_our, map_our, role)
                role_their = "Biz Satıcı" if role == "Biz Alıcı" else "Biz Alıcı"
                prep_their = prepare_data(df_their, map_their, role_their)

                ignored_our = prep_our[prep_our["Doc_Category"] == "DIGER"]
                ignored_their = prep_their[prep_their["Doc_Category"] == "DIGER"]

                # --- EŞLEŞTİRME ---
                inv_our = prep_our[prep_our["Doc_Category"].str.contains("FATURA")]
                inv_their = prep_their[prep_their["Doc_Category"].str.contains("FATURA")]
                
                def build_agg(mapping):
                    agg = {"Signed_TL": "sum", "Signed_FX": "sum", "std_date": "max", "Kaynak_Dosya": "first", "Satır_No": "first"}
                    if mapping.get("inv_no"): agg[mapping["inv_no"]] = "first"
                    if mapping.get("pay_no"): agg[mapping["pay_no"]] = "first"
                    if mapping.get("curr"): agg[mapping["curr"]] = "first" 
                    for ec in mapping.get("extra_cols", []): agg[ec] = "first"
                    return agg

                gk_our = ["key_invoice_norm"] + ([map_our["curr"]] if map_our["curr"] else [])
                gk_their = ["key_invoice_norm"] + ([map_their["curr"]] if map_their["curr"] else [])
                
                grp_our = inv_our.groupby(gk_our, as_index=False).agg(build_agg(map_our))
                grp_their = inv_their.groupby(gk_their, as_index=False).agg(build_agg(map_their))
                
                grp_our = force_suffix(grp_our, "_Biz", "key_invoice_norm")
                grp_their = force_suffix(grp_their, "_Onlar", "key_invoice_norm")
                
                merged_inv = pd.merge(grp_our, grp_their, on="key_invoice_norm", how="outer")
                merged_inv["Fark_TL"] = merged_inv["Signed_TL_Biz"].fillna(0) - merged_inv["Signed_TL_Onlar"].fillna(0)
                merged_inv["Fark_FX"] = merged_inv["Signed_FX_Biz"].fillna(0) - merged_inv["Signed_FX_Onlar"].fillna(0)

                # --- ÖDEME ---
                pay_our = prep_our[prep_our["Doc_Category"].str.contains("ODEME")].copy()
                pay_their = prep_their[prep_their["Doc_Category"].str.contains("ODEME")].copy()
                
                def create_pay_key(df, cfg, scenario):
                    d = df["std_date"].astype(str)
                    a = df["Signed_TL"].abs().round(2).astype(str)
                    if "Ödeme No" in scenario:
                        p = df[cfg["pay_no"]].astype(str) if cfg["pay_no"] else ""
                        base_key = d + "_" + p + "_" + a
                    else:
                        cat = df["Doc_Category"].astype(str)
                        base_key = d + "_" + cat + "_" + a
                    df["_temp_rank"] = df.groupby(base_key).cumcount()
                    return base_key + "_" + df["_temp_rank"].astype(str)

                pay_our["match_key"] = create_pay_key(pay_our, map_our, pay_scenario)
                pay_their["match_key"] = create_pay_key(pay_their, map_their, pay_scenario)
                
                pay_our = force_suffix(pay_our, "_Biz", "match_key")
                pay_their = force_suffix(pay_their, "_Onlar", "match_key")

                merged_pay = pd.merge(pay_our, pay_their, on="match_key", how="outer")
                merged_pay["Fark_TL"] = merged_pay["Signed_TL_Biz"].fillna(0) + merged_pay["Signed_TL_Onlar"].fillna(0)
                merged_pay["Fark_FX"] = merged_pay["Signed_FX_Biz"].fillna(0) + merged_pay["Signed_FX_Onlar"].fillna(0)

                # --- BAKİYE ---
                our_bal = prep_our.groupby("PB_Norm")[["Signed_TL", "Signed_FX"]].sum().reset_index()
                their_bal = prep_their.groupby("PB_Norm")[["Signed_TL", "Signed_FX"]].sum().reset_index()
                balance_summary = pd.merge(our_bal, their_bal, on="PB_Norm", how="outer", suffixes=("_Biz", "_Onlar")).fillna(0)
                balance_summary["Net_Fark_TL"] = balance_summary["Signed_TL_Biz"] + balance_summary["Signed_TL_Onlar"]
                balance_summary["Net_Fark_FX"] = balance_summary["Signed_FX_Biz"] + balance_summary["Signed_FX_Onlar"]

                st.session_state["res"] = {
                    "inv_match": format_clean_view(merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].notna()], map_our, map_their, "FATURA"),
                    "inv_bizde": format_clean_view(merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].isna()], map_our, map_their, "FATURA"),
                    "inv_onlar": format_clean_view(merged_inv[merged_inv["Signed_TL_Biz"].isna() & merged_inv["Signed_TL_Onlar"].notna()], map_our, map_their, "FATURA"),
                    "pay_match": format_clean_view(merged_pay, map_our, map_their, "ODEME"),
                    "ignored_our": ignored_our, "ignored_their": ignored_their, "balance_summary": balance_summary
                }
        except Exception as e:
            st.error(f"Bir hata oluştu: {str(e)}")

if "res" in st.session_state:
    res = st.session_state["res"]
    st.markdown("### 📊 Mutabakat Özeti")
    
    summary_df = res["balance_summary"]
    rows = ""
    for _, r in summary_df.iterrows():
        c_fx = "pos-val" if r['Net_Fark_FX'] >= 0 else "neg-val"
        c_tl = "pos-val" if r['Net_Fark_TL'] >= 0 else "neg-val"
        rows += f"""<tr>
            <td>{r['PB_Norm']}</td>
            <td>{r['Signed_TL_Biz']:,.2f}</td><td>{r['Signed_TL_Onlar']:,.2f}</td><td class="{c_tl}">{r['Net_Fark_TL']:,.2f}</td>
            <td class="border-left-thick">{r['Signed_FX_Biz']:,.2f}</td><td>{r['Signed_FX_Onlar']:,.2f}</td><td class="{c_fx}">{r['Net_Fark_FX']:,.2f}</td>
        </tr>"""

    st.markdown(f"""
    <table class="mini-table">
        <thead>
            <tr>
                <th>PB</th> <th>Bizim TL</th> <th>Karşı TL</th> <th>Fark TL</th> 
                <th class="border-left-thick">Bizim Döviz</th> <th>Karşı Döviz</th> <th>Fark Döviz</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["✅ Fatura Eşleşme", "⚠️ Bizde Var/Yok", "⚠️ Onlarda Var/Yok", "💳 Ödemeler", "🔍 Analiz Dışı", "📥 İndir"])
    with tab1: st.data_editor(res["inv_match"], use_container_width=True, disabled=True)
    with tab2: st.data_editor(res["inv_bizde"], use_container_width=True, disabled=True)
    with tab3: st.data_editor(res["inv_onlar"], use_container_width=True, disabled=True)
    with tab4: st.data_editor(res["pay_match"], use_container_width=True, disabled=True)
    with tab5: 
        c1,c2=st.columns(2)
        with c1: st.write("Bizim Kapsam Dışı"); st.dataframe(res["ignored_our"])
        with c2: st.write("Onların Kapsam Dışı"); st.dataframe(res["ignored_their"])
    with tab6:
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        res["balance_summary"].to_excel(writer, sheet_name='Ozet', index=False)
        res["inv_match"].to_excel(writer, sheet_name='Fatura_Eslesme', index=False)
        res["inv_bizde"].to_excel(writer, sheet_name='Bizde_Var_Onlarda_Yok', index=False)
        res["inv_onlar"].to_excel(writer, sheet_name='Onlarda_Var_Bizde_Yok', index=False)
        res["pay_match"].to_excel(writer, sheet_name='Odeme_Eslesme', index=False)
        writer.close()
        st.download_button("Excel İndir", output.getvalue(), "RecoMatch_Rapor.xlsx")
