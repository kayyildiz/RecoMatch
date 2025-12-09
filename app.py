import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import re
from io import BytesIO

# ==========================================
# 1. AYARLAR & CSS (MINIMAL)
# ==========================================
st.set_page_config(
    page_title="RecoMatch | Akıllı Mutabakat",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main {background-color: #f9fafb;}
    .stDataFrame {border: 1px solid #e5e7eb; border-radius: 5px;}
    div[data-testid="stExpander"] {background-color: white; border-radius: 8px; border: none; box-shadow: 0 1px 2px rgba(0,0,0,0.05);}
    
    /* MİNİMAL ÖZET TABLOSU CSS */
    .compact-table {
        width: 100%;
        border-collapse: collapse;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-size: 0.9rem;
        background-color: white;
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        margin-bottom: 1rem;
    }
    .compact-table th {
        background-color: #1E3A8A;
        color: white;
        text-align: right;
        padding: 8px 12px;
        font-weight: 600;
    }
    .compact-table th:first-child { text-align: left; }
    .compact-table td {
        padding: 6px 12px;
        border-bottom: 1px solid #eee;
        text-align: right;
        color: #374151;
    }
    .compact-table td:first-child { 
        text-align: left; 
        font-weight: 600; 
        color: #111827;
    }
    .compact-table tr:last-child td { border-bottom: none; }
    .val-pos { color: #10B981; font-weight: bold; }
    .val-neg { color: #EF4444; font-weight: bold; }
    .val-neutral { color: #6B7280; }
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
# 3. YARDIMCI FONKSİYONLAR
# ==========================================
def normalize_text(s):
    if pd.isna(s): return ""
    s = str(s).strip().upper()
    s = s.replace(" ", "").replace("O", "0")
    return s

def get_invoice_key(raw_val):
    clean = re.sub(r'[^A-Z0-9]', '', normalize_text(raw_val))
    return clean

def read_and_merge(uploaded_files):
    if not uploaded_files: return pd.DataFrame()
    df_list = []
    for f in uploaded_files:
        try:
            # Excel okurken binlik ayraç sorunlarını azaltmak için dtype=str okuyup sonra parse etmek daha güvenli olabilir
            # Ancak performans için direkt okuyoruz, parse_amount fonksiyonu düzeltecek.
            temp_df = pd.read_excel(f)
            temp_df["Satır_No"] = temp_df.index + 2 
            for col in temp_df.select_dtypes(include=['object']).columns:
                temp_df[col] = temp_df[col].astype(str).str.strip()
            temp_df["Kaynak_Dosya"] = f.name
            df_list.append(temp_df)
        except Exception as e:
            st.error(f"Dosya okuma hatası ({f.name}): {e}")
    return pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()

def parse_amount(val):
    """
    Türkçe Excel formatını (1.000,50) veya (1000.50) sayıya çevirir.
    Boş, '-' veya hatalı verileri 0.0 döner.
    """
    if pd.isna(val): return 0.0
    s = str(val).strip()
    if not s or s == "-": return 0.0
    
    # Olası formatlar:
    # 1.234,56 (TR) -> Noktaları sil, virgülü nokta yap
    # 1,234.56 (US) -> Virgülleri sil
    
    # Basit sezgisel yaklaşım:
    # Eğer hem nokta hem virgül varsa: sondaki ondalıktır.
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."): # 1.234,56 formatı
            s = s.replace(".", "").replace(",", ".")
        else: # 1,234.56 formatı
            s = s.replace(",", "")
    elif "," in s: # Sadece virgül var (12,50 veya 1,000)
        # Genelde TR formatında ondalıktır ama emin olamayız.
        # Standart olarak virgülü nokta yapalım (Python float uyumu için)
        s = s.replace(",", ".")
    
    # Temizlik sonrası
    try:
        return float(s)
    except:
        return 0.0

# ==========================================
# 4. HESAPLAMA MANTIĞI (DÜZELTİLMİŞ)
# ==========================================

def calculate_smart_balance(row, role, 
                            mode_tl, c_tl_debt, c_tl_credit, c_tl_single,
                            mode_fx, c_fx_debt, c_fx_credit, c_fx_single,
                            doc_cat):
    """
    Hem TL hem FX için akıllı bakiye hesaplar.
    Döviz tek kolonsa, TL'nin Borç/Alacak durumuna bakarak yön tayin eder.
    """
    
    # --- 1. TL HESABI ---
    tl_net = 0.0
    tl_debt_val = 0.0
    tl_credit_val = 0.0
    
    if mode_tl == "separate":
        tl_debt_val = parse_amount(row.get(c_tl_debt, 0))
        tl_credit_val = parse_amount(row.get(c_tl_credit, 0))
        tl_net = tl_credit_val - tl_debt_val
    else:
        # Tek kolon TL
        raw_tl = parse_amount(row.get(c_tl_single, 0))
        # İşaret mantığı (Tek kolonsa role göre)
        sign = 1
        if role == "Biz Alıcı":
            if doc_cat in ["FATURA", "IADE_ODEME"]: sign = 1 # Alacak
            else: sign = -1 # Borç
        else: # Biz Satıcı
            if doc_cat in ["FATURA", "IADE_ODEME"]: sign = -1 # Borç
            else: sign = 1 # Alacak
        tl_net = raw_tl * sign

    # --- 2. FX HESABI ---
    fx_net = 0.0
    
    if mode_fx == "separate":
        # Ayrı Döviz Borç/Alacak
        f_d = parse_amount(row.get(c_fx_debt, 0))
        f_c = parse_amount(row.get(c_fx_credit, 0))
        fx_net = f_c - f_d
        
    elif mode_fx == "single":
        # TEK KOLON DÖVİZ (KRİTİK KISIM)
        raw_fx = parse_amount(row.get(c_fx_single, 0))
        
        if raw_fx != 0:
            # Yönü Nereden Bileceğiz?
            # 1. Yöntem: TL ayrık ise TL kolonlarına bak
            if mode_tl == "separate":
                if tl_debt_val > 0 and tl_credit_val == 0:
                    # TL Borç ise, Döviz de Borçtur -> Eksi
                    fx_net = -abs(raw_fx)
                elif tl_credit_val > 0 and tl_debt_val == 0:
                    # TL Alacak ise, Döviz de Alacaktır -> Artı
                    fx_net = abs(raw_fx)
                else:
                    # İkisi de dolu veya boşsa belge türüne dön
                    # (Yukarıdaki tek kolon TL mantığı)
                    sign = 1
                    if role == "Biz Alıcı":
                        if doc_cat in ["FATURA", "IADE_ODEME"]: sign = 1
                        else: sign = -1
                    else:
                        if doc_cat in ["FATURA", "IADE_ODEME"]: sign = -1
                        else: sign = 1
                    fx_net = raw_fx * sign
            else:
                # TL de tek kolonsa mecburen belge türüne bak
                sign = 1
                if role == "Biz Alıcı":
                    if doc_cat in ["FATURA", "IADE_ODEME"]: sign = 1
                    else: sign = -1
                else:
                    if doc_cat in ["FATURA", "IADE_ODEME"]: sign = -1
                    else: sign = 1
                fx_net = raw_fx * sign

    return tl_net, fx_net

def get_doc_category(row_type_val, type_config):
    val = normalize_text(row_type_val)
    if val in [normalize_text(x) for x in type_config.get("FATURA", [])]: return "FATURA"
    elif val in [normalize_text(x) for x in type_config.get("ODEME", [])]: return "ODEME"
    elif val in [normalize_text(x) for x in type_config.get("IADE_FATURA", [])]: return "IADE_FATURA"
    elif val in [normalize_text(x) for x in type_config.get("IADE_ODEME", [])]: return "IADE_ODEME"
    return "DIGER"

def prepare_data(df, mapping, role):
    df = df.copy()
    
    # Tarih
    c_date = mapping.get("date")
    if c_date and c_date in df.columns:
        df["std_date"] = pd.to_datetime(df[c_date], dayfirst=True, errors='coerce').dt.date
    else:
        df["std_date"] = None

    # Kategori
    c_type = mapping.get("doc_type")
    type_cfg = mapping.get("type_vals", {})
    if c_type and c_type in df.columns:
        df["Doc_Category"] = df[c_type].apply(lambda x: get_doc_category(x, type_cfg))
    else:
        df["Doc_Category"] = "DIGER"

    # --- HESAPLAMA (Row-wise) ---
    # Apply fonksiyonu ile satır satır hesaplayıp iki sütunu (TL, FX) aynı anda döndüreceğiz
    
    def calc_row(row):
        return calculate_smart_balance(
            row, role,
            mapping.get("amount_mode", "single"), 
            mapping.get("col_debt"), mapping.get("col_credit"), mapping.get("col_amount"),
            mapping.get("fx_amount_mode", "none"),
            mapping.get("col_fx_debt"), mapping.get("col_fx_credit"), mapping.get("col_fx_amount"),
            row["Doc_Category"]
        )

    # Apply result_type='expand' ile DataFrame döner
    res_df = df.apply(calc_row, axis=1, result_type='expand')
    df["Signed_TL"] = res_df[0]
    df["Signed_FX"] = res_df[1]

    # Para Birimi
    c_curr = mapping.get("curr")
    if c_curr and c_curr in df.columns:
        df["PB_Norm"] = df[c_curr].apply(normalize_text)
        df["PB_Norm"] = df["PB_Norm"].replace("", "TL").fillna("TL")
    else:
        df["PB_Norm"] = "TL"

    # Fatura Key
    c_inv = mapping.get("inv_no")
    if c_inv and c_inv in df.columns:
        df["key_invoice_norm"] = df[c_inv].apply(get_invoice_key)
    else:
        df["key_invoice_norm"] = ""
        
    return df

# ==========================================
# 5. UI & MAPPING
# ==========================================
def render_mapping_ui(title, df, default_map, key_prefix):
    st.markdown(f"#### {title} Ayarları")
    cols = ["Seçiniz..."] + list(df.columns)
    def idx(c): return cols.index(c) if c in cols else 0

    # --- TL ---
    st.caption("Yerel Para Birimi (TL)")
    amount_mode = st.radio(f"{title} TL Modu", ["Tek Kolon", "Ayrı (Borç/Alacak)"], 
                           index=0 if default_map.get("amount_mode") != "separate" else 1,
                           horizontal=True, key=f"{key_prefix}_mode")
    mode_val = "single" if amount_mode == "Tek Kolon" else "separate"
    
    c_debt, c_credit, c_amt = None, None, None
    if mode_val == "separate":
        c1, c2 = st.columns(2)
        with c1: c_debt = st.selectbox("TL Borç", cols, index=idx(default_map.get("col_debt")), key=f"{key_prefix}_debt")
        with c2: c_credit = st.selectbox("TL Alacak", cols, index=idx(default_map.get("col_credit")), key=f"{key_prefix}_credit")
    else:
        c_amt = st.selectbox("TL Tutar", cols, index=idx(default_map.get("col_amount")), key=f"{key_prefix}_amt")

    # --- DÖVİZ (FX) ---
    st.caption("Döviz (FX) Tutarı")
    fx_option = st.radio(f"{title} Döviz Modu", ["Yok", "Tek Kolon", "Ayrı (Borç/Alacak)"],
                         index=0 if default_map.get("fx_amount_mode", "none") == "none" else (1 if default_map.get("fx_amount_mode") == "single" else 2),
                         horizontal=True, key=f"{key_prefix}_fx_opt")
    
    fx_mode_val = "none"
    c_fx_debt, c_fx_credit, c_fx_amt = None, None, None
    
    if fx_option == "Tek Kolon":
        fx_mode_val = "single"
        c_fx_amt = st.selectbox("Döviz Tutar", cols, index=idx(default_map.get("col_fx_amount")), key=f"{key_prefix}_fx_amt")
    elif fx_option == "Ayrı (Borç/Alacak)":
        fx_mode_val = "separate"
        f1, f2 = st.columns(2)
        with f1: c_fx_debt = st.selectbox("Döviz Borç", cols, index=idx(default_map.get("col_fx_debt")), key=f"{key_prefix}_fx_debt")
        with f2: c_fx_credit = st.selectbox("Döviz Alacak", cols, index=idx(default_map.get("col_fx_credit")), key=f"{key_prefix}_fx_credit")

    st.divider()

    # --- DİĞER KOLONLAR ---
    c1, c2, c3 = st.columns(3)
    with c1: c_inv = st.selectbox("Fatura No", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
    with c2: c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
    with c3: c_curr = st.selectbox("Para Birimi (PB)", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
    
    c_pay_no = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")

    # --- BELGE TÜRÜ ---
    st.markdown("---")
    c_type = st.selectbox("Belge Türü Kolonu", cols, index=idx(default_map.get("doc_type")), key=f"{key_prefix}_type")
    selected_types = {"FATURA": [], "IADE_FATURA": [], "ODEME": [], "IADE_ODEME": []}
    
    if c_type != "Seçiniz...":
        unique_vals = sorted([str(x) for x in df[c_type].unique() if pd.notna(x)])
        d_types = default_map.get("type_vals", {})
        
        with st.expander(f"📂 {title} - Belge Türü Eşleştirme", expanded=False):
            c_f, c_o = st.columns(2)
            with c_f:
                selected_types["FATURA"] = st.multiselect("Faturalar", unique_vals, default=[x for x in d_types.get("FATURA", []) if x in unique_vals], key=f"{key_prefix}_mf")
                selected_types["IADE_FATURA"] = st.multiselect("İade Faturalar", unique_vals, default=[x for x in d_types.get("IADE_FATURA", []) if x in unique_vals], key=f"{key_prefix}_mif")
            with c_o:
                selected_types["ODEME"] = st.multiselect("Ödemeler", unique_vals, default=[x for x in d_types.get("ODEME", []) if x in unique_vals], key=f"{key_prefix}_mo")
                selected_types["IADE_ODEME"] = st.multiselect("İade Ödemeler", unique_vals, default=[x for x in d_types.get("IADE_ODEME", []) if x in unique_vals], key=f"{key_prefix}_mio")

    extra_cols = st.multiselect("İlave Kolonlar", [c for c in cols if c != "Seçiniz..."], default=[x for x in default_map.get("extra_cols", []) if x in cols], key=f"{key_prefix}_extra")

    def clean(v): return None if v == "Seçiniz..." else v
    return {
        "amount_mode": mode_val,
        "col_debt": clean(c_debt), "col_credit": clean(c_credit), "col_amount": clean(c_amt),
        
        "fx_amount_mode": fx_mode_val,
        "col_fx_debt": clean(c_fx_debt), "col_fx_credit": clean(c_fx_credit), "col_fx_amount": clean(c_fx_amt),

        "inv_no": clean(c_inv), "date": clean(c_date), "curr": clean(c_curr),
        "pay_no": clean(c_pay_no), "doc_type": clean(c_type),
        "type_vals": selected_types,
        "extra_cols": extra_cols
    }

# ==========================================
# 6. GÖRÜNTÜ FORMATLAYICI
# ==========================================
def format_clean_view(df, map_our, map_their, type="FATURA"):
    cols_our, rename_our = [], {}
    
    # Bizim Kolonlar
    if "Kaynak_Dosya_Biz" in df.columns: cols_our.append("Kaynak_Dosya_Biz"); rename_our["Kaynak_Dosya_Biz"] = "Kaynak (Biz)"
    if "Satır_No_Biz" in df.columns: cols_our.append("Satır_No_Biz"); rename_our["Satır_No_Biz"] = "Satır (Biz)"
    
    if type == "FATURA":
        orig = map_our.get("inv_no")
        if orig and (orig+"_Biz" in df.columns): cols_our.append(orig+"_Biz"); rename_our[orig+"_Biz"] = "Fatura No (Biz)"
    else:
        orig = map_our.get("pay_no")
        if orig and (orig+"_Biz" in df.columns): cols_our.append(orig+"_Biz"); rename_our[orig+"_Biz"] = "Ödeme/Belge (Biz)"
            
    cols_our.append("std_date_Biz"); rename_our["std_date_Biz"] = "Tarih (Biz)"
    cols_our.append("Signed_TL_Biz"); rename_our["Signed_TL_Biz"] = "Tutar TL (Biz)"
    cols_our.append("Signed_FX_Biz"); rename_our["Signed_FX_Biz"] = "Tutar FX (Biz)"
    
    if map_our.get("curr") and (map_our.get("curr")+"_Biz" in df.columns):
        cols_our.append(map_our.get("curr")+"_Biz"); rename_our[map_our.get("curr")+"_Biz"] = "PB (Biz)"
        
    for ec in map_our.get("extra_cols", []):
        if (ec+"_Biz") in df.columns: cols_our.append(ec+"_Biz"); rename_our[ec+"_Biz"] = f"{ec} (Biz)"

    # Onların Kolonlar
    cols_their, rename_their = [], {}
    if "Kaynak_Dosya_Onlar" in df.columns: cols_their.append("Kaynak_Dosya_Onlar"); rename_their["Kaynak_Dosya_Onlar"] = "Kaynak (Onlar)"
    if "Satır_No_Onlar" in df.columns: cols_their.append("Satır_No_Onlar"); rename_their["Satır_No_Onlar"] = "Satır (Onlar)"

    if type == "FATURA":
        orig = map_their.get("inv_no")
        if orig and (orig+"_Onlar" in df.columns): cols_their.append(orig+"_Onlar"); rename_their[orig+"_Onlar"] = "Fatura No (Onlar)"
    else:
        orig = map_their.get("pay_no")
        if orig and (orig+"_Onlar" in df.columns): cols_their.append(orig+"_Onlar"); rename_their[orig+"_Onlar"] = "Ödeme/Belge (Onlar)"
            
    cols_their.append("std_date_Onlar"); rename_their["std_date_Onlar"] = "Tarih (Onlar)"
    cols_their.append("Signed_TL_Onlar"); rename_their["Signed_TL_Onlar"] = "Tutar TL (Onlar)"
    cols_their.append("Signed_FX_Onlar"); rename_their["Signed_FX_Onlar"] = "Tutar FX (Onlar)"

    if map_their.get("curr") and (map_their.get("curr")+"_Onlar" in df.columns):
        cols_their.append(map_their.get("curr")+"_Onlar"); rename_their[map_their.get("curr")+"_Onlar"] = "PB (Onlar)"

    for ec in map_their.get("extra_cols", []):
        if (ec+"_Onlar") in df.columns: cols_their.append(ec+"_Onlar"); rename_their[ec+"_Onlar"] = f"{ec} (Onlar)"

    final_cols = cols_our + cols_their + ["Fark_TL", "Fark_FX"]
    final_rename = {**rename_our, **rename_their, "Fark_TL": "Fark (TL)", "Fark_FX": "Fark (FX)"}
    
    existing = [c for c in final_cols if c in df.columns]
    return df[existing].rename(columns=final_rename)

# ==========================================
# 7. MAIN FLOW
# ==========================================
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
        if not map_our.get("inv_no") or not map_their.get("inv_no"):
            st.error("HATA: Lütfen her iki taraf için de 'Fatura No' kolonunu seçiniz!")
            st.stop()

        TemplateManager.update_template(files_our[0].name, map_our)
        TemplateManager.update_template(files_their[0].name, map_their)
        
        with st.spinner("Analiz yapılıyor..."):
            prep_our = prepare_data(df_our, map_our, role)
            role_their = "Biz Satıcı" if role == "Biz Alıcı" else "Biz Alıcı"
            prep_their = prepare_data(df_their, map_their, role_their)

            ignored_our = prep_our[prep_our["Doc_Category"] == "DIGER"]
            ignored_their = prep_their[prep_their["Doc_Category"] == "DIGER"]

            # --- A) FATURA ---
            inv_our = prep_our[prep_our["Doc_Category"].str.contains("FATURA")]
            inv_their = prep_their[prep_their["Doc_Category"].str.contains("FATURA")]
            
            def build_agg(mapping):
                agg = {
                    "Signed_TL": "sum", "Signed_FX": "sum", "std_date": "max",
                    "Kaynak_Dosya": "first", "Satır_No": "first"
                }
                if mapping.get("inv_no"): agg[mapping["inv_no"]] = "first"
                if mapping.get("curr"): agg[mapping["curr"]] = "first" 
                for ec in mapping.get("extra_cols", []): agg[ec] = "first"
                return agg

            gk_our = ["key_invoice_norm"] + ([map_our["curr"]] if map_our["curr"] else [])
            gk_their = ["key_invoice_norm"] + ([map_their["curr"]] if map_their["curr"] else [])
            
            grp_our = inv_our.groupby(gk_our, as_index=False).agg(build_agg(map_our))
            grp_their = inv_their.groupby(gk_their, as_index=False).agg(build_agg(map_their))
            
            merged_inv = pd.merge(grp_our, grp_their, on="key_invoice_norm", how="outer", suffixes=("_Biz", "_Onlar"))
            merged_inv["Fark_TL"] = merged_inv["Signed_TL_Biz"].fillna(0) - merged_inv["Signed_TL_Onlar"].fillna(0)
            merged_inv["Fark_FX"] = merged_inv["Signed_FX_Biz"].fillna(0) - merged_inv["Signed_FX_Onlar"].fillna(0)

            # --- B) ÖDEME ---
            pay_our = prep_our[prep_our["Doc_Category"].str.contains("ODEME")].copy()
            pay_their = prep_their[prep_their["Doc_Category"].str.contains("ODEME")].copy()
            
            def create_pay_key_with_rank(df, cfg, scenario):
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

            pay_our["match_key"] = create_pay_key_with_rank(pay_our, map_our, pay_scenario)
            pay_their["match_key"] = create_pay_key_with_rank(pay_their, map_their, pay_scenario)
            
            merged_pay = pd.merge(pay_our, pay_their, on="match_key", how="outer", suffixes=("_Biz", "_Onlar"))
            merged_pay["Fark_TL"] = merged_pay["Signed_TL_Biz"].fillna(0) + merged_pay["Signed_TL_Onlar"].fillna(0)
            merged_pay["Fark_FX"] = merged_pay["Signed_FX_Biz"].fillna(0) + merged_pay["Signed_FX_Onlar"].fillna(0)

            # --- C) BAKİYE ÖZETİ ---
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
                "ignored_our": ignored_our,
                "ignored_their": ignored_their,
                "balance_summary": balance_summary
            }

if "res" in st.session_state:
    res = st.session_state["res"]
    
    st.markdown("### 📊 Cari Bakiye & Mutabakat Özeti")
    
    # HTML TABLO OLUŞTURMA (MİNİMAL)
    summary_df = res["balance_summary"].copy()
    
    # Header
    table_html = """
    <table class="compact-table">
        <thead>
            <tr>
                <th>Para Birimi</th>
                <th>Bizim Bakiye</th>
                <th>Karşı Bakiye</th>
                <th>Fark (Döviz)</th>
                <th>Fark (TL Karşılığı)</th>
            </tr>
        </thead>
        <tbody>
    """
    
    for idx, row in summary_df.iterrows():
        pb = row["PB_Norm"]
        biz = row['Signed_FX_Biz']
        onlar = row['Signed_FX_Onlar']
        fark_fx = row['Net_Fark_FX']
        fark_tl = row['Net_Fark_TL']
        
        fark_class = "val-pos" if fark_fx >= 0 else "val-neg"
        if abs(fark_fx) < 0.01: fark_class = "val-neutral"
        
        table_html += f"""
            <tr>
                <td>{pb}</td>
                <td>{biz:,.2f}</td>
                <td>{onlar:,.2f}</td>
                <td class="{fark_class}">{fark_fx:,.2f}</td>
                <td class="{fark_class}">{fark_tl:,.2f}</td>
            </tr>
        """
    
    table_html += "</tbody></table>"
    st.markdown(table_html, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "✅ Fatura Eşleşme", "⚠️ Bizde Var / Yok", "⚠️ Onlarda Var / Yok", 
        "💳 Ödemeler", "🔍 Analiz Dışı", "📥 İndir"
    ])
    
    with tab1: st.data_editor(res["inv_match"], use_container_width=True, disabled=True)
    with tab2: st.data_editor(res["inv_bizde"], use_container_width=True, disabled=True)
    with tab3: st.data_editor(res["inv_onlar"], use_container_width=True, disabled=True)
    with tab4: st.data_editor(res["pay_match"], use_container_width=True, disabled=True)
    with tab5:
        c1, c2 = st.columns(2)
        with c1: st.write("Bizim Taraf (Kapsam Dışı)"); st.data_editor(res["ignored_our"], disabled=True)
        with c2: st.write("Karşı Taraf (Kapsam Dışı)"); st.data_editor(res["ignored_their"], disabled=True)
            
    with tab6:
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        res["balance_summary"].to_excel(writer, sheet_name='Ozet_Bakiye', index=False)
        res["inv_match"].to_excel(writer, sheet_name='Fatura_Eslesme', index=False)
        res["inv_bizde"].to_excel(writer, sheet_name='Bizde_Var_Onlarda_Yok', index=False)
        res["inv_onlar"].to_excel(writer, sheet_name='Onlarda_Var_Bizde_Yok', index=False)
        res["pay_match"].to_excel(writer, sheet_name='Odeme_Eslesme', index=False)
        res["ignored_our"].to_excel(writer, sheet_name='Audit_Biz', index=False)
        res["ignored_their"].to_excel(writer, sheet_name='Audit_Onlar', index=False)
        writer.close()
        st.download_button("Excel Raporunu İndir", output.getvalue(), "RecoMatch_Rapor.xlsx")
