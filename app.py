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
st.set_page_config(
    page_title="RecoMatch | Akıllı Mutabakat",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main {background-color: #f9fafb;}
    .stMetric {background-color: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px;}
    .stDataFrame {border: 1px solid #e5e7eb; border-radius: 5px;}
    div[data-testid="stExpander"] {background-color: white; border-radius: 8px; border: none; box-shadow: 0 1px 2px rgba(0,0,0,0.05);}
    .header-text {color: #1e3a8a; font-weight: bold;}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. TEMPLATE & UTILS
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
            temp_df = pd.read_excel(f)
            # Satır No (Excel mantığı)
            temp_df["Satır_No"] = temp_df.index + 2 
            for col in temp_df.select_dtypes(include=['object']).columns:
                temp_df[col] = temp_df[col].astype(str).str.strip()
            temp_df["Kaynak_Dosya"] = f.name
            df_list.append(temp_df)
        except Exception as e:
            st.error(f"Dosya okuma hatası ({f.name}): {e}")
    return pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()

# ==========================================
# 3. VERİ HAZIRLAMA (CORE)
# ==========================================

def calculate_net_amount(row, map_cfg, role):
    mode = map_cfg.get("amount_mode", "single")
    net_val = 0.0
    if mode == "separate":
        c_debt = map_cfg.get("col_debt")
        c_credit = map_cfg.get("col_credit")
        debt_val = pd.to_numeric(str(row.get(c_debt, 0)).replace('.','').replace(',','.'), errors='coerce') or 0
        credit_val = pd.to_numeric(str(row.get(c_credit, 0)).replace('.','').replace(',','.'), errors='coerce') or 0
        net_val = credit_val - debt_val
    else:
        c_amt = map_cfg.get("col_amount")
        try:
            val_str = str(row.get(c_amt, 0)).replace('.','').replace(',','.')
            net_val = pd.to_numeric(val_str, errors='coerce') or 0
        except:
            net_val = 0
    return net_val

def get_doc_category(row_type_val, type_config):
    val = normalize_text(row_type_val)
    if val in [normalize_text(x) for x in type_config.get("FATURA", [])]: return "FATURA"
    elif val in [normalize_text(x) for x in type_config.get("ODEME", [])]: return "ODEME"
    elif val in [normalize_text(x) for x in type_config.get("IADE_FATURA", [])]: return "IADE_FATURA"
    elif val in [normalize_text(x) for x in type_config.get("IADE_ODEME", [])]: return "IADE_ODEME"
    return "DIGER"

def apply_role_sign(net_val, category, role, mode):
    if mode == "single":
        sign = 1
        if role == "Biz Alıcı":
            if category == "FATURA": sign = 1
            elif category == "IADE_FATURA": sign = -1
            elif category == "ODEME": sign = -1
            elif category == "IADE_ODEME": sign = 1
        elif role == "Biz Satıcı":
            if category == "FATURA": sign = -1
            elif category == "IADE_FATURA": sign = 1
            elif category == "ODEME": sign = 1
            elif category == "IADE_ODEME": sign = -1
        return net_val * sign
    return net_val

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

    # Tutar
    df["Signed_TL"] = df.apply(lambda row: apply_role_sign(
        calculate_net_amount(row, mapping, role),
        row["Doc_Category"],
        role,
        mapping.get("amount_mode", "single")
    ), axis=1)

    # Fatura Key
    c_inv = mapping.get("inv_no")
    if c_inv and c_inv in df.columns:
        df["key_invoice_norm"] = df[c_inv].apply(get_invoice_key)
    else:
        df["key_invoice_norm"] = ""
        
    return df

# ==========================================
# 4. UI & MAPPING
# ==========================================
def render_mapping_ui(title, df, default_map, key_prefix):
    st.markdown(f"#### {title} Ayarları")
    cols = ["Seçiniz..."] + list(df.columns)
    def idx(c): return cols.index(c) if c in cols else 0

    amount_mode = st.radio(f"{title} Tutar Tipi", ["Tek Kolon", "Ayrı (Borç/Alacak)"], 
                           index=0 if default_map.get("amount_mode") != "separate" else 1,
                           horizontal=True, key=f"{key_prefix}_mode")
    mode_val = "single" if amount_mode == "Tek Kolon" else "separate"
    
    c_debt, c_credit, c_amt = None, None, None
    if mode_val == "separate":
        c1, c2 = st.columns(2)
        with c1: c_debt = st.selectbox("Borç Kolonu", cols, index=idx(default_map.get("col_debt")), key=f"{key_prefix}_debt")
        with c2: c_credit = st.selectbox("Alacak Kolonu", cols, index=idx(default_map.get("col_credit")), key=f"{key_prefix}_credit")
    else:
        c_amt = st.selectbox("Tutar Kolonu", cols, index=idx(default_map.get("col_amount")), key=f"{key_prefix}_amt")

    c1, c2, c3 = st.columns(3)
    with c1: c_inv = st.selectbox("Fatura No", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
    with c2: c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
    with c3: c_curr = st.selectbox("Para Birimi", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
    
    c_pay_no = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")

    st.markdown("---")
    c_type = st.selectbox("Belge Türü Kolonu", cols, index=idx(default_map.get("doc_type")), key=f"{key_prefix}_type")
    selected_types = {"FATURA": [], "IADE_FATURA": [], "ODEME": [], "IADE_ODEME": []}
    
    if c_type != "Seçiniz...":
        unique_vals = sorted([str(x) for x in df[c_type].unique() if pd.notna(x)])
        d_types = default_map.get("type_vals", {})
        
        with st.expander(f"📂 {title} - Belge Türü Tanımları", expanded=False):
            c_f, c_o = st.columns(2)
            with c_f:
                st.caption("Fatura Grubu")
                selected_types["FATURA"] = st.multiselect("Faturalar", unique_vals, default=[x for x in d_types.get("FATURA", []) if x in unique_vals], key=f"{key_prefix}_mf")
                selected_types["IADE_FATURA"] = st.multiselect("İade Faturalar", unique_vals, default=[x for x in d_types.get("IADE_FATURA", []) if x in unique_vals], key=f"{key_prefix}_mif")
            with c_o:
                st.caption("Ödeme Grubu")
                selected_types["ODEME"] = st.multiselect("Ödemeler", unique_vals, default=[x for x in d_types.get("ODEME", []) if x in unique_vals], key=f"{key_prefix}_mo")
                selected_types["IADE_ODEME"] = st.multiselect("İade Ödemeler", unique_vals, default=[x for x in d_types.get("IADE_ODEME", []) if x in unique_vals], key=f"{key_prefix}_mio")

    st.markdown("---")
    extra_cols = st.multiselect(
        "Rapora Eklenecek İlave Kolonlar (Açıklama, Proje Kodu vb.)", 
        [c for c in cols if c != "Seçiniz..."],
        default=[x for x in default_map.get("extra_cols", []) if x in cols],
        key=f"{key_prefix}_extra"
    )

    def clean(v): return None if v == "Seçiniz..." else v
    return {
        "amount_mode": mode_val,
        "col_debt": clean(c_debt), "col_credit": clean(c_credit), "col_amount": clean(c_amt),
        "inv_no": clean(c_inv), "date": clean(c_date), "curr": clean(c_curr),
        "pay_no": clean(c_pay_no), "doc_type": clean(c_type),
        "type_vals": selected_types,
        "extra_cols": extra_cols
    }

# ==========================================
# 5. COLUMN FORMATTER
# ==========================================
def format_clean_view(df, map_our, map_their, type="FATURA"):
    """
    Kullanıcıya gösterilen nihai tabloyu oluşturur.
    Önemli Değişiklik: Seçilen ID kolonunu (Fatura No veya Ödeme No) mutlaka rename eder.
    """
    
    # --- BİZİM TARAF ---
    cols_our, rename_our = [], {}
    
    # 1. Kaynak Dosya / Satır
    if "Kaynak_Dosya_Biz" in df.columns:
        cols_our.append("Kaynak_Dosya_Biz"); rename_our["Kaynak_Dosya_Biz"] = "Kaynak (Biz)"
    if "Satır_No_Biz" in df.columns:
        cols_our.append("Satır_No_Biz"); rename_our["Satır_No_Biz"] = "Satır (Biz)"
    
    # 2. ANAHTAR KOLONLAR (FATURA NO / ÖDEME NO)
    # Merge işleminden sonra kolon adı "OrijinalAd_Biz" olmuştur.
    if type == "FATURA":
        orig_col = map_our.get("inv_no")
        # Eğer orig_col tanımlıysa ve df içinde suffix'li hali varsa
        if orig_col and (orig_col + "_Biz") in df.columns: 
            cols_our.append(orig_col + "_Biz")
            rename_our[orig_col + "_Biz"] = "Fatura No (Biz)"
    else: 
        orig_col = map_our.get("pay_no")
        if orig_col and (orig_col + "_Biz") in df.columns:
            cols_our.append(orig_col + "_Biz")
            rename_our[orig_col + "_Biz"] = "Ödeme No/Açıklama (Biz)"
            
    # 3. Tarih, Tutar, PB
    cols_our.append("std_date_Biz"); rename_our["std_date_Biz"] = "Tarih (Biz)"
    cols_our.append("Signed_TL_Biz"); rename_our["Signed_TL_Biz"] = "Tutar (Biz)"
    
    if map_our.get("curr") and (map_our.get("curr") + "_Biz") in df.columns:
        cols_our.append(map_our.get("curr") + "_Biz"); rename_our[map_our.get("curr") + "_Biz"] = "PB (Biz)"

    # 4. İlave Kolonlar
    for ec in map_our.get("extra_cols", []):
        if (ec + "_Biz") in df.columns:
            cols_our.append(ec + "_Biz"); rename_our[ec + "_Biz"] = f"{ec} (Biz)"

    # --- KARŞI TARAF ---
    cols_their, rename_their = [], {}
    
    if "Kaynak_Dosya_Onlar" in df.columns:
        cols_their.append("Kaynak_Dosya_Onlar"); rename_their["Kaynak_Dosya_Onlar"] = "Kaynak (Onlar)"
    if "Satır_No_Onlar" in df.columns:
        cols_their.append("Satır_No_Onlar"); rename_their["Satır_No_Onlar"] = "Satır (Onlar)"

    if type == "FATURA":
        orig_col = map_their.get("inv_no")
        if orig_col and (orig_col + "_Onlar") in df.columns: 
            cols_their.append(orig_col + "_Onlar")
            rename_their[orig_col + "_Onlar"] = "Fatura No (Onlar)"
    else:
        orig_col = map_their.get("pay_no")
        if orig_col and (orig_col + "_Onlar") in df.columns:
            cols_their.append(orig_col + "_Onlar")
            rename_their[orig_col + "_Onlar"] = "Ödeme No/Açıklama (Onlar)"
            
    cols_their.append("std_date_Onlar"); rename_their["std_date_Onlar"] = "Tarih (Onlar)"
    cols_their.append("Signed_TL_Onlar"); rename_their["Signed_TL_Onlar"] = "Tutar (Onlar)"

    if map_their.get("curr") and (map_their.get("curr") + "_Onlar") in df.columns:
        cols_their.append(map_their.get("curr") + "_Onlar"); rename_their[map_their.get("curr") + "_Onlar"] = "PB (Onlar)"

    for ec in map_their.get("extra_cols", []):
        if (ec + "_Onlar") in df.columns:
            cols_their.append(ec + "_Onlar"); rename_their[ec + "_Onlar"] = f"{ec} (Onlar)"

    # --- BİRLEŞTİRME ---
    final_cols = cols_our + cols_their + ["Fark_TL"]
    final_rename = {**rename_our, **rename_their, "Fark_TL": "Fark (TL)"}
    
    existing_cols = [c for c in final_cols if c in df.columns]
    
    view_df = df[existing_cols].copy()
    view_df = view_df.rename(columns=final_rename)
    
    return view_df

# ==========================================
# 6. MAIN FLOW
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
            
            # Group By: Önemli -> Fatura No, PB, Kaynak, Satır'ı koru.
            def build_agg(mapping):
                agg = {
                    "Signed_TL": "sum", "std_date": "max", 
                    mapping["inv_no"]: "first", # Orijinal kolon ismini koru
                    "Kaynak_Dosya": "first", "Satır_No": "first"
                }
                if mapping.get("curr"): agg[mapping["curr"]] = "first" 
                for ec in mapping.get("extra_cols", []): agg[ec] = "first"
                return agg

            gk_our = ["key_invoice_norm"] + ([map_our["curr"]] if map_our["curr"] else [])
            gk_their = ["key_invoice_norm"] + ([map_their["curr"]] if map_their["curr"] else [])
            
            grp_our = inv_our.groupby(gk_our, as_index=False).agg(build_agg(map_our))
            grp_their = inv_their.groupby(gk_their, as_index=False).agg(build_agg(map_their))
            
            merged_inv = pd.merge(grp_our, grp_their, on="key_invoice_norm", how="outer", suffixes=("_Biz", "_Onlar"))
            merged_inv["Fark_TL"] = merged_inv["Signed_TL_Biz"].fillna(0) - merged_inv["Signed_TL_Onlar"].fillna(0)

            # --- B) ÖDEME (RANK BASED) ---
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

            # --- C) RAPORLAR ---
            inv_match = merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].notna()]
            inv_bizde = merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].isna()]
            inv_onlar = merged_inv[merged_inv["Signed_TL_Biz"].isna() & merged_inv["Signed_TL_Onlar"].notna()]

            st.session_state["res"] = {
                "inv_match": format_clean_view(inv_match, map_our, map_their, "FATURA"),
                "inv_bizde": format_clean_view(inv_bizde, map_our, map_their, "FATURA"),
                "inv_onlar": format_clean_view(inv_onlar, map_our, map_their, "FATURA"),
                "pay_match": format_clean_view(merged_pay, map_our, map_their, "ODEME"),
                "ignored_our": ignored_our,
                "ignored_their": ignored_their,
                "total_our": prep_our["Signed_TL"].sum(),
                "total_their": prep_their["Signed_TL"].sum()
            }

if "res" in st.session_state:
    res = st.session_state["res"]
    
    st.markdown("### 📊 Analiz Sonuçları")
    
    # 1. CARİ BAKİYE ÖZETİ (YENİ EKLENDİ)
    [cite_start]# [cite: 63-65] - Yüklenen ekstrelerin toplam bakiyesi
    col_bal1, col_bal2, col_bal3, col_bal4 = st.columns(4)
    
    col_bal1.metric("Bizim Hesaplanan Bakiye", f"{res['total_our']:,.2f} TL")
    col_bal2.metric("Karşı Taraf Hesaplanan Bakiye", f"{res['total_their']:,.2f} TL")
    
    # Fark (Bizim Bakiye + Karşı Bakiye) -> Muhasebe mantığı ters çalıştığı için toplam sıfır olmalı
    # Eğer fark varsa bu "Mutabakat Farkı"dır.
    net_diff = res['total_our'] + res['total_their'] 
    col_bal3.metric("NET BAKİYE FARKI", f"{net_diff:,.2f} TL", delta_color="inverse")
    
    # Eşleşen Fatura Farkı (Detay)
    diff_val = res['inv_match']['Fark (TL)'].sum() if not res['inv_match'].empty else 0
    col_bal4.metric("Eşleşen Fatura Farkı", f"{diff_val:,.2f} TL")

    st.markdown("---")

    # 2. DETAY TABLOLAR
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
        res["inv_match"].to_excel(writer, sheet_name='Fatura_Eslesme', index=False)
        res["inv_bizde"].to_excel(writer, sheet_name='Bizde_Var_Onlarda_Yok', index=False)
        res["inv_onlar"].to_excel(writer, sheet_name='Onlarda_Var_Bizde_Yok', index=False)
        res["pay_match"].to_excel(writer, sheet_name='Odeme_Eslesme', index=False)
        res["ignored_our"].to_excel(writer, sheet_name='Audit_Biz', index=False)
        res["ignored_their"].to_excel(writer, sheet_name='Audit_Onlar', index=False)
        writer.close()
        st.download_button("Excel Raporunu İndir", output.getvalue(), "RecoMatch_Rapor.xlsx")
