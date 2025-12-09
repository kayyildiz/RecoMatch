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
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. AKILLI ŞABLON & YARDIMCI FONKSİYONLAR
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
        search_key = filename.split('_')[0].lower()
        for key, val in templates.items():
            if key in filename.lower(): return val
        return {}

def normalize_text(s):
    if pd.isna(s): return ""
    s = str(s).strip().upper()
    s = s.replace(" ", "").replace("O", "0")
    return s

def get_invoice_key(raw_val):
    # [cite_start]Fatura No: Sadece alfanümerik, normalize [cite: 115]
    clean = re.sub(r'[^A-Z0-9]', '', normalize_text(raw_val))
    return clean

def read_and_merge(uploaded_files):
    if not uploaded_files: return pd.DataFrame()
    df_list = []
    for f in uploaded_files:
        try:
            temp_df = pd.read_excel(f)
            # Object kolonları string'e çevir
            for col in temp_df.select_dtypes(include=['object']).columns:
                temp_df[col] = temp_df[col].astype(str).str.strip()
            temp_df["Kaynak_Dosya"] = f.name
            df_list.append(temp_df)
        except Exception as e:
            st.error(f"Dosya hatası ({f.name}): {e}")
    return pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()

# ==========================================
# 3. VERİ HAZIRLAMA VE HESAPLAMA (CORE LOGIC)
# ==========================================

def calculate_net_amount(row, map_cfg, role):
    """
    [cite_start]Tutar hesaplama mantığı[cite: 36, 25]:
    - Tek kolon seçildiyse: Role göre işaret (+/-) belirlenir.
    - Ayrı Borç/Alacak seçildiyse: (Alacak - Borç) yapılır.
    """
    mode = map_cfg.get("amount_mode", "single")
    net_val = 0.0
    
    # 1. Ham Tutarı Bul
    if mode == "separate":
        # Ayrı kolonlar: Alacak - Borç
        c_debt = map_cfg.get("col_debt")
        c_credit = map_cfg.get("col_credit")
        
        debt_val = pd.to_numeric(str(row.get(c_debt, 0)).replace('.','').replace(',','.'), errors='coerce') or 0
        credit_val = pd.to_numeric(str(row.get(c_credit, 0)).replace('.','').replace(',','.'), errors='coerce') or 0
        
        # Muhasebe mantığı: Bakiye = Alacak - Borç (Genel kabul)
        # Ancak bizim "Role" tablosuna uydurmak için:
        # Eğer Biz Alıcıysak: Fatura(Alacak) +, Ödeme(Borç) -. Yani (Alacak - Borç) formülü doğru çalışır.
        # Eğer Biz Satıcıysak: Fatura(Borç) -, Ödeme(Alacak) +. Yani (Alacak - Borç) yine formülü verir.
        net_val = credit_val - debt_val
        
        # Ayrı kolon kullanıldığında sign multiplier genellikle 1 dir çünkü matematiksel işlem yaptık.
        # Ancak dokümandaki satıcı rolü ters işaret gerektiriyorsa buraya müdahale edilebilir.
        # Şimdilik standart (Alacak - Borç) formülünü uyguluyoruz.
        
    else:
        # Tek kolon
        c_amt = map_cfg.get("col_amount")
        try:
            val_str = str(row.get(c_amt, 0)).replace('.','').replace(',','.')
            net_val = pd.to_numeric(val_str, errors='coerce') or 0
        except:
            net_val = 0

    return net_val

def get_doc_category(row_type_val, type_config):
    """Satırın türünü belirle (Fatura mı, Ödeme mi?)"""
    val = normalize_text(row_type_val)
    
    # type_config: {'FATURA': ['FAT', 'INV'], 'ODEME': ['EFT', 'HAVALE']}
    if val in [normalize_text(x) for x in type_config.get("FATURA", [])]:
        return "FATURA"
    elif val in [normalize_text(x) for x in type_config.get("ODEME", [])]:
        return "ODEME"
    elif val in [normalize_text(x) for x in type_config.get("IADE_FATURA", [])]:
        return "IADE_FATURA"
    elif val in [normalize_text(x) for x in type_config.get("IADE_ODEME", [])]:
        return "IADE_ODEME"
    
    return "DIGER"

def apply_role_sign(net_val, category, role, mode):
    """
    [cite_start]Tek kolon modunda, Belge Türü ve Role göre işareti uygular[cite: 25].
    Ayrı kolon modunda (separate), zaten (Alacak-Borç) yapıldığı için genelde dokunulmaz,
    fakat "Biz Satıcı" isek Fatura Borçtur (-) bu doğru.
    """
    # Tek kolon ise işaret tablosunu uygula
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
    
    # 1. Tarih
    c_date = mapping.get("date")
    if c_date and c_date in df.columns:
        df["std_date"] = pd.to_datetime(df[c_date], dayfirst=True, errors='coerce')
    else:
        df["std_date"] = pd.NaT

    # 2. Belge Türünü Belirle
    c_type = mapping.get("doc_type")
    type_cfg = mapping.get("type_vals", {})
    
    # Satır satır kategori bul
    if c_type and c_type in df.columns:
        df["Doc_Category"] = df[c_type].apply(lambda x: get_doc_category(x, type_cfg))
    else:
        df["Doc_Category"] = "DIGER"

    # 3. Tutar Hesapla
    # apply axis=1 ile satır bazlı işlem
    df["Signed_TL"] = df.apply(lambda row: apply_role_sign(
        calculate_net_amount(row, mapping, role),
        row["Doc_Category"],
        role,
        mapping.get("amount_mode", "single")
    ), axis=1)

    # 4. Fatura Key
    c_inv = mapping.get("inv_no")
    if c_inv and c_inv in df.columns:
        df["key_invoice_norm"] = df[c_inv].apply(get_invoice_key)
    else:
        df["key_invoice_norm"] = ""
        
    return df

# ==========================================
# 4. UI: KOLON EŞLEŞTİRME (SOFT UI)
# ==========================================

def render_mapping_ui(title, df, default_map, key_prefix):
    st.markdown(f"#### {title} Ayarları")
    cols = ["Seçiniz..."] + list(df.columns)
    def idx(c): return cols.index(c) if c in cols else 0

    # [cite_start]1. Tutar Tipi Seçimi [cite: 36]
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

    # 2. Standart Kolonlar
    c1, c2, c3 = st.columns(3)
    with c1: c_inv = st.selectbox("Fatura No", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
    with c2: c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
    with c3: c_curr = st.selectbox("Para Birimi (Opsiyonel)", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
    
    c_pay_no = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")

    # [cite_start]3. Belge Türü ve Detaylı Filtreleme [cite: 42-45]
    st.info("👇 Eşleştirme için Hangi Belge Türlerinin Kullanılacağını Seçin")
    c_type = st.selectbox("Belge Türü Kolonu", cols, index=idx(default_map.get("doc_type")), key=f"{key_prefix}_type")
    
    selected_types = {"FATURA": [], "IADE_FATURA": [], "ODEME": [], "IADE_ODEME": []}
    
    if c_type != "Seçiniz...":
        unique_vals = sorted([str(x) for x in df[c_type].unique() if pd.notna(x)])
        d_types = default_map.get("type_vals", {})
        
        with st.expander("📂 Belge Türü Detaylarını Belirle (Fatura vs Ödeme)", expanded=True):
            c_f, c_o = st.columns(2)
            with c_f:
                st.markdown("**Fatura Grubuna Girenler**")
                selected_types["FATURA"] = st.multiselect("Faturalar", unique_vals, default=[x for x in d_types.get("FATURA", []) if x in unique_vals], key=f"{key_prefix}_mf")
                selected_types["IADE_FATURA"] = st.multiselect("İade Faturalar", unique_vals, default=[x for x in d_types.get("IADE_FATURA", []) if x in unique_vals], key=f"{key_prefix}_mif")
            with c_o:
                st.markdown("**Ödeme Grubuna Girenler**")
                selected_types["ODEME"] = st.multiselect("Ödemeler", unique_vals, default=[x for x in d_types.get("ODEME", []) if x in unique_vals], key=f"{key_prefix}_mo")
                selected_types["IADE_ODEME"] = st.multiselect("İade Ödemeler", unique_vals, default=[x for x in d_types.get("IADE_ODEME", []) if x in unique_vals], key=f"{key_prefix}_mio")

    # Return temizlenmiş map
    def clean(v): return None if v == "Seçiniz..." else v
    return {
        "amount_mode": mode_val,
        "col_debt": clean(c_debt), "col_credit": clean(c_credit), "col_amount": clean(c_amt),
        "inv_no": clean(c_inv), "date": clean(c_date), "curr": clean(c_curr),
        "pay_no": clean(c_pay_no), "doc_type": clean(c_type),
        "type_vals": selected_types
    }

# ==========================================
# 5. UI: MAIN FLOW
# ==========================================
with st.sidebar:
    st.header("RecoMatch 🛡️")
    role = st.selectbox("Bizim Rolümüz", ["Biz Alıcı", "Biz Satıcı"])
    st.divider()
    files_our = st.file_uploader("Bizim Ekstreler", accept_multiple_files=True)
    files_their = st.file_uploader("Karşı Taraf Ekstreler", accept_multiple_files=True)
    st.divider()
    pay_scenario = st.radio("Ödeme Eşleşme Kriteri", ["Tarih + Ödeme No + Tutar", "Tarih + Belge Türü + Tutar"])
    analyze_btn = st.button("Analizi Başlat", type="primary", use_container_width=True)

if files_our and files_their:
    df_our = read_and_merge(files_our)
    df_their = read_and_merge(files_their)
    
    st.success(f"Dosyalar Yüklendi! ({len(df_our)} satır vs {len(df_their)} satır)")
    
    # Şablonları Yükle
    saved_our = TemplateManager.find_best_match(files_our[0].name)
    saved_their = TemplateManager.find_best_match(files_their[0].name)
    
    col1, col2 = st.columns(2)
    with col1:
        map_our = render_mapping_ui("Bizim Taraf", df_our, saved_our, "our")
    with col2:
        map_their = render_mapping_ui("Karşı Taraf", df_their, saved_their, "their")
        
    if analyze_btn:
        # Şablon Kaydet
        TemplateManager.update_template(files_our[0].name, map_our)
        TemplateManager.update_template(files_their[0].name, map_their)
        
        with st.spinner("Analiz yapılıyor..."):
            # 1. Veriyi Hazırla (Role göre)
            # Karşı tarafın rolü: Biz Alıcı isek onlar Satıcı mantığıyla değil,
            # Onların alacağı bizim borcumuzdur mantığıyla eşleşir.
            # Veriyi hazırlarken "Biz Alıcı" isek, Bizim Fatura (+), Ödeme (-)
            # Karşı tarafın listesinde "Onlar Satıcı" ise Fatura (Borç -), Ödeme (Alacak +).
            # Ancak biz onların listesini de "Bizim gözümüzden" eşleştireceğiz.
            # Basitleştirme: Her iki tarafın "Fatura" dediklerini eşleştir.
            # İşaretler bakiye farkı için önemli. Eşleşme "Mutlak Değer" üzerinden yapılmalı.
            
            prep_our = prepare_data(df_our, map_our, role)
            
            # Karşı taraf için rolü tersine çevirip veriyi hazırla
            role_their = "Biz Satıcı" if role == "Biz Alıcı" else "Biz Alıcı"
            prep_their = prepare_data(df_their, map_their, role_their)
            
            # --- A) FATURA EŞLEŞTİRME ---
            inv_our = prep_our[prep_our["Doc_Category"].str.contains("FATURA")]
            inv_their = prep_their[prep_their["Doc_Category"].str.contains("FATURA")]
            
            # Gruplama Keys
            # Key hatasını önlemek için None olan kolonları listeye ekleme
            g_cols_our = ["key_invoice_norm"]
            if map_our["curr"]: g_cols_our.append(map_our["curr"])
            
            g_cols_their = ["key_invoice_norm"]
            if map_their["curr"]: g_cols_their.append(map_their["curr"])
            
            # GroupBy
            grp_our = inv_our.groupby(g_cols_our, as_index=False).agg(
                Topla_TL=("Signed_TL", "sum"),
                Tarih=("std_date", "max")
            )
            grp_their = inv_their.groupby(g_cols_their, as_index=False).agg(
                Topla_TL=("Signed_TL", "sum"),
                Tarih=("std_date", "max")
            )
            
            # Merge
            # Sadece 'key_invoice_norm' üzerinden merge yap (PB bazen tutmayabilir)
            # Eğer PB zorunlu ise on=['key_invoice_norm', 'PB'] yapılmalı
            # RecoMatch dökümanı Fatura No zorunlu diyor.
            matched_inv = pd.merge(grp_our, grp_their, on="key_invoice_norm", how="outer", suffixes=("_Biz", "_Onlar"))
            matched_inv["Fark_TL"] = matched_inv["Topla_TL_Biz"].fillna(0) - matched_inv["Topla_TL_Onlar"].fillna(0)
            
            # --- B) ÖDEME EŞLEŞTİRME ---
            # [cite_start]Sadece kullanıcının "ODEME" olarak seçtiği tipleri filtrele [cite: 42-45]
            pay_our = prep_our[prep_our["Doc_Category"].str.contains("ODEME")]
            pay_their = prep_their[prep_their["Doc_Category"].str.contains("ODEME")]
            
            # Key Oluşturucu
            def create_pay_key(df, cfg, scenario):
                d_str = df["std_date"].astype(str)
                # Tutarın mutlak değeri (biri +, biri - olabilir, mutlak kıyasla)
                amt_str = df["Signed_TL"].abs().round(2).astype(str)
                
                if "Ödeme No" in scenario:
                    # No varsa ekle
                    p_no = df[cfg["pay_no"]].astype(str) if cfg["pay_no"] else ""
                    return d_str + "_" + p_no + "_" + amt_str
                else:
                    # Belge Türü Bazlı
                    # Burada normalize edilmiş türü kullanıyoruz
                    # Kullanıcı "EFT" ve "Gelen Havale" seçtiyse, metinler farklı olabilir.
                    # Bu senaryoda sadece Tarih + Tutar daha güvenli olabilir
                    # veya kullanıcının seçtiği türü key'e ekleriz.
                    t_str = df[cfg["doc_type"]].astype(str) if cfg["doc_type"] else ""
                    return d_str + "_" + t_str + "_" + amt_str

            pay_our["match_key"] = create_pay_key(pay_our, map_our, pay_scenario)
            pay_their["match_key"] = create_pay_key(pay_their, map_their, pay_scenario)
            
            matched_pay = pd.merge(pay_our, pay_their, on="match_key", how="outer", suffixes=("_Biz", "_Onlar"))
            
            # Fark Hesabı: Bizimki (-100) vs Onlarınki (+100) -> Toplamları 0 olmalı (Alacak/Borç mantığıyla)
            # Veya direkt tutar kıyaslıyorsak mutlak değer farkı:
            matched_pay["Fark_TL"] = matched_pay["Signed_TL_Biz"].fillna(0) + matched_pay["Signed_TL_Onlar"].fillna(0)
            # Not: İşaretler zıt olduğu için topladığımızda 0 vermeli (biri -, biri +)
            # Eğer 0 değilse fark vardır.
            
            # --- C) SONUÇLARI GÖSTER ---
            st.session_state["res"] = {
                "inv": matched_inv,
                "pay": matched_pay,
                "ch_diff": prep_our["Signed_TL"].sum() + prep_their["Signed_TL"].sum() # Genel bakiye farkı
            }

if "res" in st.session_state:
    res = st.session_state["res"]
    
    st.markdown("### 📊 Sonuçlar")
    m1, m2 = st.columns(2)
    m1.metric("Toplam Fatura Farkı", f"{res['inv']['Fark_TL'].sum():,.2f}")
    m2.metric("Toplam Ödeme Farkı", f"{res['pay']['Fark_TL'].sum():,.2f}")
    
    tab1, tab2, tab3 = st.tabs(["Fatura Eşleşme", "Ödeme Eşleşme", "İndir"])
    
    with tab1:
        st.dataframe(res["inv"], use_container_width=True)
        
    with tab2:
        st.dataframe(res["pay"], use_container_width=True)
        
    with tab3:
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='xlsxwriter')
        res["inv"].to_excel(writer, sheet_name='Fatura', index=False)
        res["pay"].to_excel(writer, sheet_name='Odeme', index=False)
        writer.close()
        st.download_button("Excel İndir", output.getvalue(), "recomatch_sonuc.xlsx")
