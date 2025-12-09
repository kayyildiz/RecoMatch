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

    # Tutar Modu
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

    # Kolonlar
    c1, c2, c3 = st.columns(3)
    with c1: c_inv = st.selectbox("Fatura No", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
    with c2: c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
    with c3: c_curr = st.selectbox("Para Birimi", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
    
    c_pay_no = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")

    # Belge Türü
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

    # İlave Kolonlar
    st.markdown("---")
    extra_cols = st.multiselect(
        "Rapora Eklenecek İlave Kolonlar (Opsiyonel)", 
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
# 5. MAIN FLOW
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

            # --- DENETİM: KAPSAM DIŞI KALANLAR ---
            ignored_our = prep_our[prep_our["Doc_Category"] == "DIGER"]
            ignored_their = prep_their[prep_their["Doc_Category"] == "DIGER"]

            # --- A) FATURA ---
            inv_our = prep_our[prep_our["Doc_Category"].str.contains("FATURA")]
            inv_their = prep_their[prep_their["Doc_Category"].str.contains("FATURA")]
            
            def build_agg(mapping):
                agg = {"Signed_TL": "sum", "std_date": "max", mapping["inv_no"]: "first"}
                for ec in mapping.get("extra_cols", []): agg[ec] = "first"
                return agg

            gk_our = ["key_invoice_norm"] + ([map_our["curr"]] if map_our["curr"] else [])
            gk_their = ["key_invoice_norm"] + ([map_their["curr"]] if map_their["curr"] else [])
            
            grp_our = inv_our.groupby(gk_our, as_index=False).agg(build_agg(map_our))
            grp_their = inv_their.groupby(gk_their, as_index=False).agg(build_agg(map_their))
            
            merged_inv = pd.merge(grp_our, grp_their, on="key_invoice_norm", how="outer", suffixes=("_Biz", "_Onlar"))
            merged_inv["Fark_TL"] = merged_inv["Signed_TL_Biz"].fillna(0) - merged_inv["Signed_TL_Onlar"].fillna(0)

            # --- B) ÖDEME ---
            pay_our = prep_our[prep_our["Doc_Category"].str.contains("ODEME")]
            pay_their = prep_their[prep_their["Doc_Category"].str.contains("ODEME")]
            
            def create_pay_key(df, cfg, scenario):
                d = df["std_date"].astype(str)
                a = df["Signed_TL"].abs().round(2).astype(str)
                if "Ödeme No" in scenario:
                    p = df[cfg["pay_no"]].astype(str) if cfg["pay_no"] else ""
                    return d + "_" + p + "_" + a
                return d + "_" + (df[cfg["doc_type"]].astype(str) if cfg["doc_type"] else "") + "_" + a

            pay_our["match_key"] = create_pay_key(pay_our, map_our, pay_scenario)
            pay_their["match_key"] = create_pay_key(pay_their, map_their, pay_scenario)
            
            merged_pay = pd.merge(pay_our, pay_their, on="match_key", how="outer", suffixes=("_Biz", "_Onlar"))
            merged_pay["Fark_TL"] = merged_pay["Signed_TL_Biz"].fillna(0) + merged_pay["Signed_TL_Onlar"].fillna(0)

            # --- GÖRÜNÜM & FORMATLAMA ---
            def format_cols(df, map1, map2, is_pay=False):
                col_map = {
                    "Signed_TL_Biz": "Tutar (Biz)", "Signed_TL_Onlar": "Tutar (Onlar)",
                    "std_date_Biz": "Tarih (Biz)", "std_date_Onlar": "Tarih (Onlar)",
                    "Fark_TL": "Fark (TL)"
                }
                
                if not is_pay:
                    col_map[map1.get("inv_no") + "_Biz"] = "Belge No (Biz)"
                    col_map[map2.get("inv_no") + "_Onlar"] = "Belge No (Onlar)"
                else:
                    # Ödeme için özel sütunlar
                    if map1.get("pay_no"): col_map[map1.get("pay_no") + "_Biz"] = "Açıklama/No (Biz)"
                    if map2.get("pay_no"): col_map[map2.get("pay_no") + "_Onlar"] = "Açıklama/No (Onlar)"

                for ec in map1.get("extra_cols", []): col_map[ec + "_Biz"] = f"{ec} (Biz)"
                for ec in map2.get("extra_cols", []): col_map[ec + "_Onlar"] = f"{ec} (Onlar)"
                
                # Sadece mevcut kolonları rename et
                avail_cols = [c for c in col_map.keys() if c in df.columns]
                final_map = {k: v for k, v in col_map.items() if k in avail_cols}
                
                # Sadeleştirilmiş tablo döndür
                base_cols = list(final_map.values())
                df = df.rename(columns=final_map)
                
                # Eğer kolon varsa seçerek döndür, yoksa hepsini
                try:
                    return df[base_cols]
                except:
                    return df

            st.session_state["res"] = {
                "inv_match": format_cols(merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].notna()], map_our, map_their),
                "inv_bizde": format_cols(merged_inv[merged_inv["Signed_TL_Biz"].notna() & merged_inv["Signed_TL_Onlar"].isna()], map_our, map_their),
                "inv_onlar": format_cols(merged_inv[merged_inv["Signed_TL_Biz"].isna() & merged_inv["Signed_TL_Onlar"].notna()], map_our, map_their),
                "pay_match": format_cols(merged_pay, map_our, map_their, is_pay=True),
                "ignored_our": ignored_our,
                "ignored_their": ignored_their
            }

if "res" in st.session_state:
    res = st.session_state["res"]
    
    st.markdown("### 📊 Analiz Sonuçları")
    m1, m2, m3 = st.columns(3)
    m1.metric("Eşleşen Fatura Farkı", f"{res['inv_match']['Fark (TL)'].sum():,.2f} TL")
    m2.metric("Bizde Olup Onlarda Olmayan", len(res["inv_bizde"]))
    m3.metric("Onlarda Olup Bizde Olmayan", len(res["inv_onlar"]))

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "✅ Fatura Eşleşme", "⚠️ Bizde Var / Yok", "⚠️ Onlarda Var / Yok", 
        "💳 Ödemeler", "🔍 Analiz Dışı (Audit)", "📥 İndir"
    ])
    
    with tab1: st.dataframe(res["inv_match"], use_container_width=True)
    with tab2: st.dataframe(res["inv_bizde"], use_container_width=True)
    with tab3: st.dataframe(res["inv_onlar"], use_container_width=True)
    with tab4: st.dataframe(res["pay_match"], use_container_width=True)
    
    with tab5:
        st.info("Aşağıdaki satırlar 'Belge Türü' seçimlerinize uymadığı için (örn: Virman, Açılış) analize dahil edilmemiştir.")
        c1, c2 = st.columns(2)
        with c1: 
            st.write("Bizim Taraf (Dahil Edilmeyenler)")
            st.dataframe(res["ignored_our"])
        with c2: 
            st.write("Karşı Taraf (Dahil Edilmeyenler)")
            st.dataframe(res["ignored_their"])
            
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
        st.download_button("Tüm Raporu İndir", output.getvalue(), "RecoMatch_Rapor.xlsx")
