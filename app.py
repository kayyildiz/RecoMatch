import streamlit as st
import pandas as pd
import io
import os
import json
import re
from io import BytesIO

# ==========================================
# 1. HELPER CLASSES & FUNCTIONS (UTILS, LOADERS, TEMPLATES)
# ==========================================

TEMPLATE_PATH = "templates.json"

def normalize_text(s: str) -> str:
    """Metin temizliği: boşlukları sil, büyük harfe çevir, 0-O değişimini yap."""
    if pd.isna(s):
        return ""
    s = str(s).strip()
    s = s.replace(" ", "")
    s = s.replace("O", "0")  # O harfini sıfıra çevir
    return s.upper()

def invoice_number_key(raw: str, last_digits: int = 6) -> str:
    """Fatura no karşılaştırma anahtarı (Sadece sayısal karakterler ve son X hane)."""
    n = normalize_text(raw)
    digits = re.sub(r"\D", "", n) # Sadece rakamları al
    if len(digits) <= last_digits:
        return digits
    return digits[-last_digits:]

def read_excel_file(uploaded_file, header_row: int = 0) -> pd.DataFrame:
    """Excel okuma fonksiyonu."""
    try:
        return pd.read_excel(BytesIO(uploaded_file.read()), header=header_row)
    except Exception as e:
        st.error(f"Dosya okuma hatası: {e}")
        return pd.DataFrame()

def clean_common(df: pd.DataFrame) -> pd.DataFrame:
    """DataFrame temizliği (string kolonlardaki boşluklar vb)."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().str.replace(r"\s+", "", regex=True)
    return df

# --- Template Yönetimi (Otomatik Kayıt) ---
def load_templates():
    if not os.path.exists(TEMPLATE_PATH):
        return {}
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_templates(templates):
    with open(TEMPLATE_PATH, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)

def get_saved_mapping(filename):
    """Dosya ismine göre kayıtlı şablonu getirir."""
    templates = load_templates()
    # Basit bir eşleşme: dosya adını key olarak kullanıyoruz
    return templates.get(filename, {})

def auto_save_mapping(filename, mapping):
    """Kullanıcı seçim yaptıkça otomatik kaydeder."""
    templates = load_templates()
    templates[filename] = mapping
    save_templates(templates)

# ==========================================
# 2. LOGIC FUNCTIONS (MATCHING, DIFF, SUMMARY)
# ==========================================

def prepare_invoices(df: pd.DataFrame, col_invoice_no, col_currency, col_fx_amount, col_tl_amount, group_by_currency=True):
    """Faturaları hazırlar ve gruplar."""
    df = df.copy()
    # Fatura No Key oluştur
    df["invoice_key"] = df[col_invoice_no].apply(invoice_number_key)
    
    group_cols = ["invoice_key"]
    if group_by_currency and col_currency and col_currency in df.columns:
        group_cols.append(col_currency)

    agg_dict = {}
    if col_fx_amount and col_fx_amount in df.columns:
        agg_dict[col_fx_amount] = "sum"
    if col_tl_amount and col_tl_amount in df.columns:
        agg_dict[col_tl_amount] = "sum"

    # Eğer hiç tutar kolonu seçilmediyse sadece count alabiliriz veya olduğu gibi bırakırız
    if not agg_dict:
        # Tutar yoksa duplicate'leri tek satıra indir (drop_duplicates mantığı)
        grouped = df[group_cols].drop_duplicates().reset_index(drop=True)
    else:
        grouped = df.groupby(group_cols, dropna=False).agg(agg_dict).reset_index()
    
    return grouped

def match_invoices(df_our, df_their, col_currency, col_fx_amount, col_tl_amount):
    """Fatura eşleştirme mantığı."""
    merge_on = ["invoice_key"]
    if col_currency and col_currency in df_our.columns:
        merge_on.append(col_currency)

    merged = df_our.merge(
        df_their,
        on=merge_on,
        how="outer",
        suffixes=("_biz", "_onlar")
    )

    # Fark hesaplama (Kolonlar seçildiyse)
    if col_fx_amount:
        merged["Fark_Doviz"] = merged.get(f"{col_fx_amount}_biz", 0) - merged.get(f"{col_fx_amount}_onlar", 0)
    if col_tl_amount:
        merged["Fark_TL"] = merged.get(f"{col_tl_amount}_biz", 0) - merged.get(f"{col_tl_amount}_onlar", 0)

    return merged

def prepare_payment_key(df, cols, scenario):
    """Ödeme eşleştirme anahtarı oluşturur."""
    df = df.copy()
    # Tarih formatlama
    if cols.get("date"):
        df["date_key"] = pd.to_datetime(df[cols["date"]], dayfirst=True, errors='coerce').dt.date
    else:
        df["date_key"] = "0000-00-00"

    # Tutar (string convertion for key)
    amt_col = cols.get("amount_tl")
    if amt_col and amt_col in df.columns:
        # Sayısal olmayanları 0 yap
        amount_series = pd.to_numeric(df[amt_col], errors='coerce').fillna(0)
        amount_str = amount_series.round(2).astype(str)
    else:
        amount_str = "0"

    # Senaryoya göre anahtar
    if scenario == "no_based":
        # Tarih + Ödeme No + Tutar
        pay_no = df[cols["payment_no"]].astype(str) if cols.get("payment_no") else ""
        df["match_key"] = df["date_key"].astype(str) + "_" + pay_no + "_" + amount_str
    else:
        # Tarih + Belge Türü (Opsiyonel) + Tutar
        doc_type = df[cols["doc_type"]].astype(str).str.upper() if cols.get("doc_type") else "GENEL"
        df["match_key"] = df["date_key"].astype(str) + "_" + doc_type + "_" + amount_str

    return df

def match_payments(df_our, df_their, scenario, cols):
    """Ödeme eşleştirme."""
    df_our_prep = prepare_payment_key(df_our, cols, scenario)
    df_their_prep = prepare_payment_key(df_their, cols, scenario)

    merged = df_our_prep.merge(
        df_their_prep,
        on="match_key",
        suffixes=("_biz", "_onlar"),
        how="outer"
    )

    col_amt = cols.get("amount_tl")
    if col_amt:
        val_biz = pd.to_numeric(merged[f"{col_amt}_biz"], errors='coerce').fillna(0)
        val_onlar = pd.to_numeric(merged[f"{col_amt}_onlar"], errors='coerce').fillna(0)
        merged["Fark_TL"] = val_biz - val_onlar

    return merged

def diff_reports(df_match, key_col_prefix):
    """Bizde Var/Onlarda Yok raporları üretir."""
    # Suffixler _biz ve _onlar
    # Eğer eşleşme anahtarı (invoice_key veya match_key) merged datada varsa;
    # Kaynak tarafın verisi NaN ise o kayıt diğer tarafta vardır ama kaynakta yoktur.
    
    # Bizde Var, Onlarda Yok -> Onlar tarafı NaN
    bizde_var = df_match[df_match[f"{key_col_prefix}_onlar"].isna()]
    
    # Onlarda Var, Bizde Yok -> Bizim taraf NaN
    onlarda_var = df_match[df_match[f"{key_col_prefix}_biz"].isna()]
    
    return bizde_var, onlarda_var

def export_to_excel(matched_inv, matched_pay, bizde_yok, onlarda_yok):
    """Excel çıktısı oluşturur."""
    output = BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    
    if matched_inv is not None:
        matched_inv.to_excel(writer, sheet_name='Fatura_Eslesme', index=False)
    if matched_pay is not None:
        matched_pay.to_excel(writer, sheet_name='Odeme_Eslesme', index=False)
    if bizde_yok is not None:
        bizde_yok.to_excel(writer, sheet_name='Bizde_Var_Onlarda_Yok', index=False)
    if onlarda_yok is not None:
        onlarda_yok.to_excel(writer, sheet_name='Onlarda_Var_Bizde_Yok', index=False)
        
    writer.close()
    processed_data = output.getvalue()
    return processed_data

# ==========================================
# 3. STREAMLIT APP (MAIN UI)
# ==========================================

st.set_page_config(page_title="RecoMatch", layout="wide", page_icon="🛡️")

# --- Custom CSS (Görsel Düzenleme) ---
st.markdown("""
<style>
    .main-header {
        font-size: 2rem; 
        font-weight: bold; 
        color: #1E3A8A; 
        margin-bottom: 0px;
    }
    .sub-header {
        font-size: 1rem; 
        color: #6B7280; 
        margin-bottom: 20px;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #F3F4F6;
        border-radius: 5px 5px 0 0;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #FFFFFF;
        border-top: 2px solid #1E3A8A;
    }
</style>
""", unsafe_allow_html=True)

# --- Sidebar ---
with st.sidebar:
    if os.path.exists("logo.jpg"):
        st.image("logo.jpg", use_column_width=True)
    else:
        st.header("🛡️ RecoMatch")
    
    st.markdown("---")
    st.subheader("📁 Dosya Yükleme")
    
    uploaded_our = st.file_uploader("Bizim Ekstre (Excel)", accept_multiple_files=False)
    uploaded_their = st.file_uploader("Karşı Taraf Ekstre (Excel)", accept_multiple_files=False)
    
    st.markdown("---")
    st.subheader("⚙️ Parametreler")
    payment_scenario = st.radio(
        "Ödeme Eşleştirme Kriteri",
        ["Tarih + Ödeme No + Tutar", "Tarih + Belge Türü + Tutar"]
    )
    scenario_key = "no_based" if "Ödeme No" in payment_scenario else "type_based"

# --- Main Page ---
st.markdown('<p class="main-header">Mutabakat ve Eşleştirme Paneli</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">Fatura ve ödemeleri otomatik eşleştirin, farkları raporlayın.</p>', unsafe_allow_html=True)

if uploaded_our and uploaded_their:
    # 1. Dosyaları Oku
    df_our = read_excel_file(uploaded_our)
    df_their = read_excel_file(uploaded_their)
    
    # 2. Temizlik
    df_our = clean_common(df_our)
    df_their = clean_common(df_their)
    
    # 3. Şablon / Kolon Seçimi (Otomatik Kayıtlı)
    saved_map = get_saved_mapping(uploaded_our.name)
    
    # Tab yapısı
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📋 Kolon Ayarları", "🧾 Fatura Eşleşme", "💳 Ödeme Eşleşme", "🔍 Farklar & Özet", "📥 İndir"])
    
    with tab1:
        st.info("Sistem yaptığınız seçimleri dosya ismine göre otomatik hatırlar.")
        col1, col2 = st.columns(2)
        
        # Kolon Listeleri
        cols_our = [None] + list(df_our.columns)
        
        with col1:
            st.markdown("### Fatura Ayarları")
            c_inv_no = st.selectbox("Fatura No", cols_our, index=cols_our.index(saved_map.get("inv_no")) if saved_map.get("inv_no") in cols_our else 0)
            c_date = st.selectbox("Tarih", cols_our, index=cols_our.index(saved_map.get("date")) if saved_map.get("date") in cols_our else 0)
            c_tl = st.selectbox("Tutar (TL) [Opsiyonel]", cols_our, index=cols_our.index(saved_map.get("tl")) if saved_map.get("tl") in cols_our else 0)
            c_curr = st.selectbox("Para Birimi [Opsiyonel]", cols_our, index=cols_our.index(saved_map.get("curr")) if saved_map.get("curr") in cols_our else 0)
            c_fx = st.selectbox("Döviz Tutar [Opsiyonel]", cols_our, index=cols_our.index(saved_map.get("fx")) if saved_map.get("fx") in cols_our else 0)

        with col2:
            st.markdown("### Ödeme Ayarları")
            c_pay_no = st.selectbox("Ödeme No / Açıklama", cols_our, index=cols_our.index(saved_map.get("pay_no")) if saved_map.get("pay_no") in cols_our else 0)
            c_doc_type = st.selectbox("Belge Türü (Fatura/Ödeme Ayrımı)", cols_our, index=cols_our.index(saved_map.get("doc_type")) if saved_map.get("doc_type") in cols_our else 0)
            # Ödeme için Tutar ve Tarih yukarıdakilerle aynı olabilir veya ayrı seçtirilebilir. 
            # Basitlik adına yukarıdakileri kullanacağız ama "Ödeme Tutarı" ayrı bir kolonda ise buraya eklenmeli.
            # Şimdilik genel tutar kolonunu baz alıyoruz.

        # Otomatik Kayıt Tetikleyici
        current_map = {
            "inv_no": c_inv_no, "date": c_date, "tl": c_tl, 
            "curr": c_curr, "fx": c_fx, "pay_no": c_pay_no, "doc_type": c_doc_type
        }
        if current_map != saved_map:
            auto_save_mapping(uploaded_our.name, current_map)
            # Sayfayı yenilemeye gerek yok, session'da tutabiliriz ama basitlik için devam.

        # Karşı taraf için kolonlar (Varsayılan olarak aynı isimde kolonları arayalım veya seçtirelim)
        # Kullanıcı kolaylığı için "Karşı Tarafın Kolonları Bizimkiyle Aynı mı?" varsayımı yapabiliriz
        # Ama genelde farklıdır. Hızlıca map edelim:
        st.markdown("---")
        st.markdown("#### Karşı Taraf Kolon Eşleşmesi")
        st.caption("Karşı taraf dosyasındaki ilgili kolonları seçiniz.")
        cols_their = [None] + list(df_their.columns)
        
        col3, col4 = st.columns(2)
        with col3:
            t_inv_no = st.selectbox("Karşı Fatura No", cols_their)
            t_tl = st.selectbox("Karşı Tutar (TL)", cols_their)
        with col4:
            t_curr = st.selectbox("Karşı Para Birimi", cols_their)
            t_pay_no = st.selectbox("Karşı Ödeme No", cols_their)

        run_btn = st.button("Analizi Başlat", type="primary")

    if run_btn:
        if not c_inv_no or not t_inv_no:
            st.error("Lütfen en azından Fatura Numarası kolonlarını seçiniz.")
            st.stop()

        # ---------------------------
        # ANALİZ MOTORU
        # ---------------------------
        
        # A) FATURA EŞLEŞTİRME
        our_inv_prep = prepare_invoices(df_our, c_inv_no, c_curr, c_fx, c_tl)
        their_inv_prep = prepare_invoices(df_their, t_inv_no, t_curr, None, t_tl) # Karşı tarafta FX yok varsaydık opsiyonel

        matched_invoices = match_invoices(
            our_inv_prep, their_inv_prep, 
            col_currency=c_curr, 
            col_fx_amount=c_fx, 
            col_tl_amount=c_tl if c_tl else "Tutar_Yok"
        )

        # B) ÖDEME EŞLEŞTİRME (Eğer seçildiyse)
        matched_payments = pd.DataFrame()
        if c_tl and t_tl: # Tutar olmadan ödeme eşleşmez
             # Sadece ödeme satırlarını filtrelemek gerekir mi? 
             # Kullanıcı Belge Türü seçtiyse filtreleyelim.
             df_our_pay = df_our.copy()
             df_their_pay = df_their.copy()

             # Basit bir filtreleme mantığı (Eğer belge türü seçildiyse "FATURA" olmayanları al gibi)
             # Veya kullanıcı tüm data üzerinden eşleşme istiyor olabilir.
             # Senaryo gereği tüm datayı gönderiyoruz, eşleşen eşleşir.
             
             pay_cols_our = {"date": c_date, "amount_tl": c_tl, "payment_no": c_pay_no, "doc_type": c_doc_type}
             pay_cols_their = {"date": c_date, "amount_tl": t_tl, "payment_no": t_pay_no, "doc_type": None} 
             # Not: Karşı tarafın tarihi için map istemedik, ilk tarih kolonunu varsayıyoruz ya da ekletmek gerekir.
             # Hızlı çözüm: t_curr vb. seçtirdik ama tarih seçtirmedik. 
             # Doğrusu: Karşı taraf için de tarih seçtirmektir. Şimdilik 't_inv_no' ile aynı sırada index varsayımı riskli.
             # Kodun çalışması için 't_date' kolonunu ilk datetime kolonu olarak tahmin edelim:
             possible_dates = [c for c in df_their.columns if "tarih" in c.lower() or "date" in c.lower()]
             t_date = possible_dates[0] if possible_dates else df_their.columns[0]
             pay_cols_their["date"] = t_date

             matched_payments = match_payments(df_our_pay, df_their_pay, scenario_key, pay_cols_our)

        # C) FARKLAR
        # Fatura bazlı farklar (Bizde var onlarda yok)
        # match_invoices sonucu 'invoice_key' üzerinden merge edildi.
        bizde_yok_inv, onlarda_yok_inv = diff_reports(matched_invoices, "invoice_key")
        
        # ---------------------------
        # SONUÇLARI GÖSTER (SESSION STATE)
        # ---------------------------
        st.session_state["res_inv"] = matched_invoices
        st.session_state["res_pay"] = matched_payments
        st.session_state["res_bizde_yok"] = bizde_yok_inv
        st.session_state["res_onlarda_yok"] = onlarda_yok_inv
        st.success("Analiz tamamlandı! Sekmeleri gezerek sonuçları inceleyebilirsiniz.")

    # Sonuçlar varsa göster (Button basılmasa bile state'den)
    if "res_inv" in st.session_state:
        m_inv = st.session_state["res_inv"]
        m_pay = st.session_state["res_pay"]
        b_yok = st.session_state["res_bizde_yok"]
        o_yok = st.session_state["res_onlarda_yok"]

        with tab2:
            st.markdown("#### Fatura Eşleşmeleri")
            st.dataframe(m_inv, use_container_width=True)
            if "Fark_TL" in m_inv.columns:
                total_diff = m_inv["Fark_TL"].sum()
                st.metric("Toplam Fatura Farkı (TL)", f"{total_diff:,.2f}")

        with tab3:
            st.markdown("#### Ödeme Eşleşmeleri")
            if not m_pay.empty:
                st.dataframe(m_pay, use_container_width=True)
                if "Fark_TL" in m_pay.columns:
                    st.metric("Toplam Ödeme Farkı (TL)", f"{m_pay['Fark_TL'].sum():,.2f}")
            else:
                st.warning("Ödeme eşleşmesi yapılamadı veya gerekli kolonlar seçilmedi.")

        with tab4:
            c1, c2 = st.columns(2)
            with c1:
                st.error("Bizde Var - Onlarda Yok (Fatura)")
                st.dataframe(b_yok, use_container_width=True)
            with c2:
                st.warning("Onlarda Var - Bizde Yok (Fatura)")
                st.dataframe(o_yok, use_container_width=True)

        with tab5:
            st.write("### Tüm Raporu İndir")
            excel_data = export_to_excel(m_inv, m_pay, b_yok, o_yok)
            st.download_button(
                label="📥 Excel Raporunu İndir",
                data=excel_data,
                file_name="RecoMatch_Rapor.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

else:
    st.info("Lütfen sol menüden her iki tarafın Excel dosyasını yükleyiniz.")
