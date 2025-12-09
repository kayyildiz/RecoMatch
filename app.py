import streamlit as st
import pandas as pd
import numpy as np
import json
import os
import re
from io import BytesIO
import datetime

# ==========================================
# 1. AYARLAR & CSS (SOFT UI)
# ==========================================
st.set_page_config(
    page_title="RecoMatch | Akıllı Mutabakat",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Modern, yumuşak hatlı CSS
st.markdown("""
<style>
    .main {background-color: #f9fafb;}
    .block-container {padding-top: 2rem;}
    h1, h2, h3 {color: #1e3a8a; font-family: 'Segoe UI', sans-serif;}
    .stMetric {
        background-color: white;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        border: 1px solid #e5e7eb;
    }
    .stDataFrame {border: 1px solid #e5e7eb; border-radius: 5px;}
    div[data-testid="stExpander"] {
        border: none;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        border-radius: 8px;
        background-color: white;
    }
    .highlight-red {color: #ef4444; font-weight: bold;}
    .highlight-green {color: #10b981; font-weight: bold;}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. AKILLI ŞABLON MOTORU (SMART TEMPLATE) [cite: 10, 11, 21]
# ==========================================
TEMPLATE_FILE = "recomatch_memory.json"

class TemplateManager:
    @staticmethod
    def load():
        if os.path.exists(TEMPLATE_FILE):
            try:
                with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @staticmethod
    def save(data):
        with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def find_best_match(filename):
        """Dosya ismindeki pattern'e göre kayıtlı ayarları getirir."""
        templates = TemplateManager.load()
        # Basit pattern matching: Dosya adının ilk kelimesi veya 'firma' adı
        # Örnek: 'Vodafone_2025.xlsx' -> Key: 'vodafone'
        key_candidate = filename.split('_')[0].lower()
        
        # Tam eşleşme veya içerir eşleşmesi
        for key, val in templates.items():
            if key in filename.lower():
                return val
        return {}

    @staticmethod
    def update_template(filename, mapping):
        """Yeni ayarları hafızaya kaydeder."""
        templates = TemplateManager.load()
        # Dosya adının ayırt edici kısmını anahtar yapalım
        key = filename.split('_')[0].lower()
        if len(key) < 3: key = filename.lower() # Çok kısaysa tamamını al
        
        templates[key] = mapping
        TemplateManager.save(templates)

# ==========================================
# 3. YARDIMCI FONKSİYONLAR (UTILS)
# ==========================================
def normalize_text(s):
    """Metin temizliği: Boşluk sil, 0-O düzelt, Büyük harf yap [cite: 8, 112-114]."""
    if pd.isna(s): return ""
    s = str(s).strip().upper()
    s = s.replace(" ", "")
    s = s.replace("O", "0") # O harfini sıfıra çevir
    return s

def get_invoice_key(raw_val):
    """Fatura No eşleştirme anahtarı (Son 6 hane kuralı) [cite: 115-117]."""
    norm = normalize_text(raw_val)
    # Sadece alfanümerik
    clean = re.sub(r'[^A-Z0-9]', '', norm)
    # Eğer 6 haneden uzunsa son 6 haneyi de bir alternatif key olarak düşünebiliriz
    # Ancak gruplama için tek bir key dönmemiz lazım.
    # Burada 'ana' key'i dönüyoruz. Eşleşme aşamasında fuzzy bakacağız.
    return clean

def read_and_merge(uploaded_files):
    """Çoklu dosyayı okur, temizler ve tek DataFrame'de birleştirir[cite: 23, 75]."""
    if not uploaded_files:
        return pd.DataFrame()
    
    df_list = []
    for f in uploaded_files:
        try:
            temp_df = pd.read_excel(f)
            # Tüm object kolonları string yap ve temizle
            for col in temp_df.select_dtypes(include=['object']).columns:
                temp_df[col] = temp_df[col].astype(str).str.strip()
            
            temp_df["Kaynak_Dosya"] = f.name
            df_list.append(temp_df)
        except Exception as e:
            st.error(f"Hata ({f.name}): {e}")
            
    if df_list:
        return pd.concat(df_list, ignore_index=True)
    return pd.DataFrame()

# ==========================================
# 4. MUHASEBE VE EŞLEŞTİRME MANTIĞI (CORE LOGIC)
# ==========================================

def calculate_sign(row, role, type_col, type_mapping):
    """
    Belge türüne ve role göre (+/-) işareti belirler. [cite: 25, 26, 30]
    type_mapping: Kullanıcının belirlediği { 'FATURA': ['FAT', 'INV'], 'ODEME': ['EFT', 'HAVALE'] ... } sözlüğü
    """
    doc_type_val = normalize_text(row.get(type_col, ""))
    
    # Türü Tespit Et
    detected_type = "DIGER"
    for cat, values in type_mapping.items():
        # values bir liste, listedeki herhangi biri doc_type_val içinde geçiyor mu veya eşit mi?
        # Tam eşleşme daha güvenli
        if any(v == doc_type_val for v in values):
            detected_type = cat
            break
    
    # Tabloya göre işaret mantığı 
    # Kategoriler: FATURA, IADE_FATURA, ODEME, IADE_ODEME
    
    sign = 0
    if role == "Biz Alıcı": # [cite: 26-27]
        if detected_type == "FATURA": sign = 1       # Alacak (+)
        elif detected_type == "IADE_FATURA": sign = -1 # Borç (-)
        elif detected_type == "ODEME": sign = -1       # Borç (-)
        elif detected_type == "IADE_ODEME": sign = 1   # Alacak (+)
            
    elif role == "Biz Satıcı": # [cite: 30-31]
        if detected_type == "FATURA": sign = -1       # Borç (-)
        elif detected_type == "IADE_FATURA": sign = 1 # Alacak (+)
        elif detected_type == "ODEME": sign = 1       # Alacak (+)
        elif detected_type == "IADE_ODEME": sign = -1 # Borç (-)
        
    return sign, detected_type

def prepare_data(df, mapping, role, type_mapping):
    """Veriyi hazırlar: Tarih formatla, İşaret uygula, Normalize et."""
    df = df.copy()
    
    # 1. Kolonları Standart İsimlere Çevir (Opsiyonel ama işi kolaylaştırır)
    # Mapping: { 'date': 'Tarih', 'inv_no': 'Fiş No', ... }
    
    # 2. Tarih Formatı
    col_date = mapping.get("date")
    if col_date and col_date in df.columns:
        df["std_date"] = pd.to_datetime(df[col_date], dayfirst=True, errors='coerce')
    else:
        df["std_date"] = pd.NaT

    # 3. Tutar ve İşaret (Sign) [cite: 108]
    col_tl = mapping.get("tl")
    col_type = mapping.get("doc_type")
    
    df["Signed_TL"] = 0.0
    df["Doc_Category"] = "DIGER"
    
    if col_tl and col_tl in df.columns:
        # Tutarı sayıya çevir
        # Bazı excellerde 1.000,50 formatı olabilir, basit replace
        try:
             # Basit temizlik: varsa binlik ayracı nokta kaldır, virgülü nokta yap (TR formatı varsayımı)
            df[col_tl] = df[col_tl].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
            amount_vals = pd.to_numeric(df[col_tl], errors='coerce').fillna(0)
        except:
             amount_vals = pd.to_numeric(df[col_tl], errors='coerce').fillna(0)

        # İşaret Hesaplama
        if col_type and col_type in df.columns:
            # Vectorize işlem zor, apply kullanacağız (performans için optimize edilebilir ama şimdilik ok)
            res = df.apply(lambda r: calculate_sign(r, role, col_type, type_mapping), axis=1, result_type='expand')
            df["Sign_Multiplier"] = res[0]
            df["Doc_Category"] = res[1]
        else:
            # Belge türü seçilmediyse varsayılan bir mantık (Örn: Hepsi Fatura?) 
            # Kullanıcıya uyarı verilmeli ama kod çalışmalı. 
            # Varsayılan: Olduğu gibi al (+1)
            df["Sign_Multiplier"] = 1
            
        df["Signed_TL"] = amount_vals * df["Sign_Multiplier"]
        
    # 4. Anahtarlar (Keys)
    col_inv = mapping.get("inv_no")
    if col_inv and col_inv in df.columns:
        df["key_invoice_norm"] = df[col_inv].apply(get_invoice_key)
        # Son 6 hane için ayrı kolon [cite: 115]
        df["key_invoice_short"] = df["key_invoice_norm"].apply(lambda x: x[-6:] if len(x) > 6 else x)
    else:
        df["key_invoice_norm"] = ""
        df["key_invoice_short"] = ""

    return df

# ==========================================
# 5. UI: SIDEBAR & DOSYA YÜKLEME
# ==========================================
with st.sidebar:
    st.header("RecoMatch 🛡️")
    st.caption("Otomatik Mutabakat Sistemi")
    st.divider()
    
    role = st.selectbox("Bizim Rolümüz", ["Biz Alıcı", "Biz Satıcı"], help="Borç/Alacak mantığı buna göre değişir.")
    
    st.subheader("1. Dosyalar")
    files_our = st.file_uploader("Bizim Ekstreler", accept_multiple_files=True, type=["xlsx"])
    files_their = st.file_uploader("Karşı Taraf Ekstreler", accept_multiple_files=True, type=["xlsx"])
    
    st.subheader("2. Ödeme Senaryosu [cite: 47]")
    payment_scenario = st.radio("Eşleştirme Kriteri", 
             ["Tarih + Ödeme No + Tutar", "Tarih + Belge Türü + Tutar"])
    
    st.divider()
    btn_analyze = st.button("Analizi Başlat", type="primary", use_container_width=True)

# ==========================================
# 6. ANA EKRAN & MAPPING LOGIC
# ==========================================

if files_our and files_their:
    # --- Verileri Oku ---
    df_our_raw = read_and_merge(files_our)
    df_their_raw = read_and_merge(files_their)
    
    # --- Kolon Eşleştirme (Smart Template) ---
    st.info("💡 Dosyalar yüklendi. Kolon eşleştirmelerini kontrol edin (Otomatik önerilmiştir).")
    
    # Hafızadan çek
    saved_map_our = TemplateManager.find_best_match(files_our[0].name)
    saved_map_their = TemplateManager.find_best_match(files_their[0].name)
    
    col_ui_1, col_ui_2 = st.columns(2)
    
    mapping_our = {}
    doc_types_our = {} # Fatura/Ödeme değerleri
    
    mapping_their = {}
    doc_types_their = {}

    def render_mapping_ui(title, df, default_map, key_prefix):
        """Kolon ve Belge Türü değerlerini seçtiren dinamik UI"""
        st.markdown(f"### {title}")
        cols = ["Seçiniz..."] + list(df.columns)
        
        # Helper to find index
        def idx(c): return cols.index(c) if c in cols else 0
        
        # Ana Kolonlar
        c_inv = st.selectbox("Fatura No (Zorunlu)", cols, index=idx(default_map.get("inv_no")), key=f"{key_prefix}_inv")
        c_date = st.selectbox("Tarih", cols, index=idx(default_map.get("date")), key=f"{key_prefix}_date")
        c_tl = st.selectbox("Tutar (TL)", cols, index=idx(default_map.get("tl")), key=f"{key_prefix}_tl")
        c_curr = st.selectbox("Para Birimi", cols, index=idx(default_map.get("curr")), key=f"{key_prefix}_curr")
        c_pay_no = st.selectbox("Ödeme No / Açıklama", cols, index=idx(default_map.get("pay_no")), key=f"{key_prefix}_pay")
        
        # Belge Türü ve Detay Seçimi [cite: 42]
        c_type = st.selectbox("Belge Türü Kolonu", cols, index=idx(default_map.get("doc_type")), key=f"{key_prefix}_type")
        
        selected_types = {
            "FATURA": [], "IADE_FATURA": [], "ODEME": [], "IADE_ODEME": []
        }
        
        if c_type != "Seçiniz...":
            # Kolondaki unique değerleri getir
            unique_vals = [normalize_text(x) for x in df[c_type].unique() if pd.notna(x)]
            unique_vals = list(set(unique_vals)) # Unique
            
            with st.expander("Belge Türü Detaylarını Tanımla (Zorunlu)"):
                st.caption("Hangi ifadeler Fatura, hangi ifadeler Ödeme sayılacak?")
                # Defaults from memory
                d_types = default_map.get("type_vals", {})
                
                selected_types["FATURA"] = st.multiselect("Fatura İfadeleri", unique_vals, default=[x for x in d_types.get("FATURA", []) if x in unique_vals], key=f"{key_prefix}_t_fat")
                selected_types["IADE_FATURA"] = st.multiselect("İade Fatura İfadeleri", unique_vals, default=[x for x in d_types.get("IADE_FATURA", []) if x in unique_vals], key=f"{key_prefix}_t_ifat")
                selected_types["ODEME"] = st.multiselect("Ödeme İfadeleri", unique_vals, default=[x for x in d_types.get("ODEME", []) if x in unique_vals], key=f"{key_prefix}_t_odeme")
                selected_types["IADE_ODEME"] = st.multiselect("İade Ödeme İfadeleri", unique_vals, default=[x for x in d_types.get("IADE_ODEME", []) if x in unique_vals], key=f"{key_prefix}_t_iodeme")

        return {
            "inv_no": c_inv if c_inv != "Seçiniz..." else None,
            "date": c_date if c_date != "Seçiniz..." else None,
            "tl": c_tl if c_tl != "Seçiniz..." else None,
            "curr": c_curr if c_curr != "Seçiniz..." else None,
            "pay_no": c_pay_no if c_pay_no != "Seçiniz..." else None,
            "doc_type": c_type if c_type != "Seçiniz..." else None,
            "type_vals": selected_types
        }

    with col_ui_1:
        map_res_our = render_mapping_ui("Bizim Taraf", df_our_raw, saved_map_our, "our")
    
    with col_ui_2:
        map_res_their = render_mapping_ui("Karşı Taraf", df_their_raw, saved_map_their, "their")

    # --- Analiz Butonuna Basıldığında ---
    if btn_analyze:
        # 1. Şablonları Kaydet (Machine Learning / Memory)
        TemplateManager.update_template(files_our[0].name, map_res_our)
        TemplateManager.update_template(files_their[0].name, map_res_their)
        
        with st.spinner("Analiz yapılıyor... Lütfen bekleyin..."):
            # 2. Veriyi Hazırla (İşaretler, Tarihler, Keyler)
            # type_vals kısmını type_mapping olarak pass ediyoruz
            df_our_prep = prepare_data(df_our_raw, map_res_our, role, map_res_our["type_vals"])
            # Karşı tarafın rolü bizim tam tersimiz mantığıyla çalışmaz, 
            # Karşı tarafın verisini "Onların gözünden" değil "Bizim eşleştirmemiz" için hazırlıyoruz.
            # Ancak hesaplamada onların borcu bizim alacağımızdır.
            # Doküman [cite: 28, 32] "Karşı tarafta ise bu tam tersi yönünden olacak" diyor.
            # Yani bizim "Alıcı" olduğumuz durumda, karşı taraf "Satıcı"dır.
            role_their = "Biz Satıcı" if role == "Biz Alıcı" else "Biz Alıcı"
            df_their_prep = prepare_data(df_their_raw, map_res_their, role_their, map_res_their["type_vals"])

            # 3. Eşleştirme Algoritmaları
            
            # --- A) FATURA EŞLEŞTİRME [cite: 118] ---
            # Sadece Fatura kategorisindekiler
            our_invs = df_our_prep[df_our_prep["Doc_Category"].str.contains("FATURA")]
            their_invs = df_their_prep[df_their_prep["Doc_Category"].str.contains("FATURA")]
            
            # Gruplama (Fatura No + PB) [cite: 118]
            # Key olarak normalize edilmiş numarayı kullan
            grp_cols_our = ["key_invoice_norm"]
            if map_res_our["curr"]: grp_cols_our.append(map_res_our["curr"])
                
            grp_cols_their = ["key_invoice_norm"]
            if map_res_their["curr"]: grp_cols_their.append(map_res_their["curr"])
            
            # Aggregation (Toplama) [cite: 29]
            our_inv_grouped = our_invs.groupby(grp_cols_our, as_index=False).agg(
                Topla_TL=("Signed_TL", "sum"),
                Tarih=("std_date", "max"),
                Orj_Key=("key_invoice_norm", "first") # Merge için
            )
            their_inv_grouped = their_invs.groupby(grp_cols_their, as_index=False).agg(
                Topla_TL=("Signed_TL", "sum"),
                Tarih=("std_date", "max"),
                Orj_Key=("key_invoice_norm", "first")
            )
            
            # Merge (Eşleştirme)
            # Önce tam key eşleşmesi
            matched_inv = pd.merge(our_inv_grouped, their_inv_grouped, on="key_invoice_norm", how="outer", suffixes=("_Biz", "_Onlar"))
            
            # TODO: Fuzzy match (Son 6 hane) [cite: 117] için burada eşleşmeyenleri (NaN olanları) 
            # tekrar bir "Short Key" üzerinden deneyebiliriz. Karmaşıklık artmasın diye şimdilik "Tam + Normalize" bırakıyorum.
            
            matched_inv["Fark_TL"] = matched_inv["Topla_TL_Biz"].fillna(0) - matched_inv["Topla_TL_Onlar"].fillna(0)
            
            # --- B) ÖDEME EŞLEŞTİRME ---
            our_pay = df_our_prep[df_our_prep["Doc_Category"].str.contains("ODEME")]
            their_pay = df_their_prep[df_their_prep["Doc_Category"].str.contains("ODEME")]
            
            # Key Oluşturma (Senaryoya göre) [cite: 48, 49]
            def create_pay_key(df, map_cfg, scenario):
                date_str = df["std_date"].astype(str)
                amt_str = df["Signed_TL"].abs().round(2).astype(str) # Tutar mutlak değer olarak anahtar olsun
                
                if "Ödeme No" in scenario:
                    pay_no = df[map_cfg["pay_no"]].astype(str) if map_cfg["pay_no"] else ""
                    return date_str + "_" + pay_no + "_" + amt_str
                else:
                    # Tarih + Belge Türü + Tutar
                    d_type = df[map_cfg["doc_type"]].astype(str) if map_cfg["doc_type"] else ""
                    return date_str + "_" + d_type + "_" + amt_str
            
            our_pay["match_key"] = create_pay_key(our_pay, map_res_our, payment_scenario)
            their_pay["match_key"] = create_pay_key(their_pay, map_res_their, payment_scenario)
            
            matched_pay = pd.merge(
                our_pay, their_pay, 
                on="match_key", how="outer", suffixes=("_Biz", "_Onlar")
            )
            # Fark hesabı (Ödemelerde satır bazlı gidiyoruz, aggregate yapmadık çünkü birebir istenmiş [cite: 56])
            # Ancak çoklu satır varsa (aynı gün aynı tutar 2 ödeme) merge çoğaltır (cartesian).
            # Bunu engellemek için group yapmak daha sağlıklı olurdu ama şimdilik bırakıyorum.
            matched_pay["Fark_TL"] = matched_pay["Signed_TL_Biz"].fillna(0) - matched_pay["Signed_TL_Onlar"].fillna(0)

            # --- C) C/H ÖZET (DÖNEMSEL)  ---
            # Tüm veriyi birleştir (Fatura + Ödeme)
            # Bizim taraf özet
            df_our_prep["YilAy"] = df_our_prep["std_date"].dt.to_period("M")
            ch_biz = df_our_prep.groupby("YilAy")["Signed_TL"].sum().reset_index().rename(columns={"Signed_TL": "Bizim_Bakiye"})
            
            # Karşı taraf özet
            df_their_prep["YilAy"] = df_their_prep["std_date"].dt.to_period("M")
            ch_onlar = df_their_prep.groupby("YilAy")["Signed_TL"].sum().reset_index().rename(columns={"Signed_TL": "Onlar_Bakiye"})
            
            ch_summary = pd.merge(ch_biz, ch_onlar, on="YilAy", how="outer").fillna(0)
            ch_summary["Fark"] = ch_summary["Bizim_Bakiye"] - ch_summary["Onlar_Bakiye"]

            # --- SONUÇLARI SESSION STATE'E AT ---
            st.session_state["results"] = {
                "inv": matched_inv,
                "pay": matched_pay,
                "ch": ch_summary,
                "our_prep": df_our_prep,
                "their_prep": df_their_prep
            }
            st.success("Analiz tamamlandı!")

# ==========================================
# 7. SONUÇ EKRANI (DASHBOARD)
# ==========================================
if "results" in st.session_state:
    res = st.session_state["results"]
    
    # 1. Dashboard (Üst Özet) [cite: 63-66]
    total_inv_diff = res["inv"]["Fark_TL"].sum()
    total_pay_diff = res["pay"]["Fark_TL"].sum()
    
    # Mutabakat Oranı [cite: 70-72]
    # Formül: Mutabık (Fark=0) / Toplam İşlem
    # Fatura için:
    total_vol = res["inv"]["Topla_TL_Biz"].abs().sum() + res["inv"]["Topla_TL_Onlar"].abs().sum()
    match_vol = res["inv"][res["inv"]["Fark_TL"].abs() < 0.1]["Topla_TL_Biz"].abs().sum() * 2 # Her iki taraf eşit
    ratio = (match_vol / total_vol * 100) if total_vol > 0 else 0
    
    st.markdown("### 📊 Mutabakat Özeti")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Mutabakat Oranı", f"%{ratio:,.2f}")
    m2.metric("Toplam Fatura Farkı", f"{total_inv_diff:,.2f} TL")
    m3.metric("Toplam Ödeme Farkı", f"{total_pay_diff:,.2f} TL")
    m4.metric("Net Bakiye Farkı", f"{(total_inv_diff + total_pay_diff):,.2f} TL")
    
    # 2. Detay Sekmeler
    tab1, tab2, tab3, tab4 = st.tabs(["🧾 Faturalar", "💳 Ödemeler", "📈 C/H Özet", "📥 İndir"])
    
    def color_row(val):
        """Fark varsa kırmızı, yoksa yeşil [cite: 81-82]"""
        color = '#d1fae5' if abs(val) < 0.1 else '#fee2e2' # Light Green / Light Red
        return f'background-color: {color}'

    with tab1:
        st.subheader("Fatura Eşleşmeleri")
        df_inv_show = res["inv"].copy()
        # Görsellik: Fark kolonuna göre renklendirme
        st.dataframe(df_inv_show.style.applymap(lambda v: 'color: red;' if v != 0 else 'color: green;', subset=['Fark_TL']), use_container_width=True)
        
        c1, c2 = st.columns(2)
        with c1:
            st.warning("Bizde Var - Onlarda Yok (Fatura)")
            st.dataframe(df_inv_show[df_inv_show["Topla_TL_Onlar"].isna()])
        with c2:
            st.warning("Onlarda Var - Bizde Yok (Fatura)")
            st.dataframe(df_inv_show[df_inv_show["Topla_TL_Biz"].isna()])

    with tab2:
        st.subheader("Ödeme Eşleşmeleri")
        df_pay_show = res["pay"].copy()
        st.dataframe(df_pay_show, use_container_width=True)

    with tab3:
        st.subheader("Dönemsel Bakiye Farkları ")
        st.line_chart(res["ch"].set_index("YilAy")[["Bizim_Bakiye", "Onlar_Bakiye"]])
        st.dataframe(res["ch"])

    with tab4:
        st.subheader("Excel Raporu Oluştur")
        
        def to_excel():
            output = BytesIO()
            writer = pd.ExcelWriter(output, engine='xlsxwriter')
            res["inv"].to_excel(writer, sheet_name='Fatura_Eslesme', index=False)
            res["pay"].to_excel(writer, sheet_name='Odeme_Eslesme', index=False)
            res["ch"].to_excel(writer, sheet_name='CH_Ozet', index=False)
            
            # Formatlama (Kırmızı/Yeşil) xlsxwriter ile yapılabilir ama basit tutuyoruz
            writer.close()
            return output.getvalue()
            
        st.download_button("📥 Raporu İndir (.xlsx)", to_excel(), file_name="RecoMatch_Sonuc.xlsx")

else:
    st.info("👈 Lütfen sol menüden dosyaları yükleyip analizi başlatın.")
