import streamlit as st
import pandas as pd
import numpy as np
import json
import re
import io
import os

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(page_title="RecoMatch", page_icon="🛡️", layout="wide")

# Logo ve Başlık
col1, col2 = st.columns([1, 5])
with col1:
    if os.path.exists("logo.jpg"):
        st.image("logo.jpg", width=100)
with col2:
    st.title("RecoMatch: Akıllı Mutabakat Sistemi")
    st.markdown("**Reconciliation + Match**")

# --- YARDIMCI FONKSİYONLAR ---

# 0 ve O harf karışıklığını ve özel karakterleri temizleyen fonksiyon [cite: 38, 114]
def normalize_invoice_no(val):
    if pd.isna(val):
        return ""
    val = str(val).upper()
    # O harfini 0 ile değiştir
    val = val.replace('O', '0')
    # Sadece alfanümerik karakterleri tut
    val = re.sub(r'[^A-Z0-9]', '', val)
    return val

# Şablon Yönetimi (Akıllı Hafıza) [cite: 10, 16]
TEMPLATE_FILE = 'templates.json'

def load_templates():
    if os.path.exists(TEMPLATE_FILE):
        with open(TEMPLATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_template(pattern, mapping):
    templates = load_templates()
    templates[pattern] = mapping
    with open(TEMPLATE_FILE, 'w') as f:
        json.dump(templates, f)

def find_matching_template(filename):
    templates = load_templates()
    # Basit bir "contains" mantığı veya regex kullanılabilir [cite: 18]
    for pattern, mapping in templates.items():
        if pattern in filename:
            return mapping
    return None

# --- SIDEBAR: AYARLAR VE YÜKLEME ---
st.sidebar.header("Ayarlar & Veri Yükleme")

# Rol Seçimi [cite: 9, 24]
role = st.sidebar.radio("Sizin Rolünüz Nedir?", ["Biz Alıcıyız", "Biz Satıcıyız"])

# Dosya Yükleme [cite: 6]
st.sidebar.subheader("1. Dosyaları Yükle")
files_bizim = st.sidebar.file_uploader("Bizim Ekstreler (Çoklu Seçim)", type=["xlsx", "xls"], accept_multiple_files=True)
files_karsi = st.sidebar.file_uploader("Karşı Taraf Ekstreler (Çoklu Seçim)", type=["xlsx", "xls"], accept_multiple_files=True)

# Ödeme Eşleştirme Senaryosu [cite: 47]
match_scenario = st.sidebar.radio(
    "Ödeme Eşleştirme Kriteri",
    ("Tarih + Ödeme No + Tutar", "Tarih + Belge Türü + Tutar")
)

# --- VERİ İŞLEME VE MAPPING ---

def process_files(uploaded_files, side_name):
    all_data = []
    
    if not uploaded_files:
        return None

    st.subheader(f"{side_name} Tarafı Sütun Eşleştirme")
    
    # İlk dosya üzerinden mapping yapalım (varsayım: hepsi aynı formatta)
    ref_file = uploaded_files[0]
    df_preview = pd.read_excel(ref_file)
    columns = list(df_preview.columns)
    
    # Akıllı Şablon Kontrolü [cite: 21]
    saved_map = find_matching_template(ref_file.name)
    default_vals = saved_map if saved_map else {}
    
    with st.expander(f"{side_name} İçin Sütun Seçimi", expanded=True):
        col_map = {}
        
        # Fatura Kolonları [cite: 19, 35]
        st.markdown("### Fatura Alanları")
        col_map['Tarih'] = st.selectbox(f"{side_name} - Tarih", columns, index=columns.index(default_vals.get('Tarih')) if default_vals.get('Tarih') in columns else 0, key=f"{side_name}_date")
        col_map['FaturaNo'] = st.selectbox(f"{side_name} - Fatura/Belge No", columns, index=columns.index(default_vals.get('FaturaNo')) if default_vals.get('FaturaNo') in columns else 0, key=f"{side_name}_inv")
        col_map['Tutar'] = st.selectbox(f"{side_name} - Tutar (TL)", columns, index=columns.index(default_vals.get('Tutar')) if default_vals.get('Tutar') in columns else 0, key=f"{side_name}_amt")
        
        # Ödeme ve Döviz Opsiyonel
        st.markdown("### Diğer Alanlar")
        col_map['DovizTutar'] = st.selectbox(f"{side_name} - Döviz Tutar (Varsa)", ["Yok"] + columns, index=columns.index(default_vals.get('DovizTutar')) + 1 if default_vals.get('DovizTutar') in columns else 0, key=f"{side_name}_fx")
        col_map['BelgeTuru'] = st.selectbox(f"{side_name} - Belge Türü (Fatura/Ödeme Ayrımı İçin)", columns, index=columns.index(default_vals.get('BelgeTuru')) if default_vals.get('BelgeTuru') in columns else 0, key=f"{side_name}_type")
        
        # Şablon Kaydetme
        pattern_input = st.text_input(f"Bu formatı hatırlamak için bir anahtar kelime girin (örn: {ref_file.name.split('.')[0]})", key=f"{side_name}_pattern")
        if st.button(f"{side_name} Şablonunu Kaydet"):
            clean_map = {k: v for k, v in col_map.items() if v != "Yok"}
            save_template(pattern_input, clean_map)
            st.success("Şablon kaydedildi!")

    # Verileri Birleştirme [cite: 23]
    for f in uploaded_files:
        df = pd.read_excel(f)
        # Seçilen kolonları al ve standartlaştır
        temp_df = pd.DataFrame()
        temp_df['DosyaAdi'] = [f.name] * len(df) # [cite: 76]
        temp_df['SatirNo'] = df.index + 2 # Excel satırı [cite: 77]
        
        for key, val in col_map.items():
            if val != "Yok":
                temp_df[key] = df[val]
            else:
                temp_df[key] = 0 if 'Tutar' in key else ""
        
        all_data.append(temp_df)
        
    return pd.concat(all_data, ignore_index=True) if all_data else None

# --- ANALİZ MANTIĞI ---

if files_bizim and files_karsi:
    df_bizim = process_files(files_bizim, "Bizim")
    df_karsi = process_files(files_karsi, "Karşı")
    
    if st.button("Analizi Başlat [cite: 62]"):
        if df_bizim is not None and df_karsi is not None:
            
            # 1. Veri Temizliği ve Normalizasyon [cite: 8, 114]
            for df in [df_bizim, df_karsi]:
                df['Norm_FaturaNo'] = df['FaturaNo'].apply(normalize_invoice_no)
                df['Tarih'] = pd.to_datetime(df['Tarih'], errors='coerce')
                df['Tutar'] = pd.to_numeric(df['Tutar'], errors='coerce').fillna(0)
            
            # 2. İşaret (Sign) Atama Mantığı 
            # Basitleştirilmiş kural: "Fatura" içerenler fatura, diğerleri ödeme gibi varsayalım 
            # (Gerçek hayatta Belge Türü içeriğine göre if/else gerekir)
            
            def assign_sign(row, is_bizim, role_selection):
                # Bu kısım dökümandaki Tablo [cite: 25] mantığına göre genişletilmeli
                # Örnek: Biz Alıcıysak, Fatura -> Alacak (-), Ödeme -> Borç (+) gibi.
                # Burada pozitif/negatif ayrımı ile basitleştiriyoruz.
                desc = str(row.get('BelgeTuru', '')).lower()
                is_invoice = 'fatura' in desc
                
                if role_selection == "Biz Alıcıyız":
                    if is_bizim:
                        # Bizim defterde Satıcı alacaklıdır (Fatura), ödeme yapınca borçlanır
                        return -1 * abs(row['Tutar']) if is_invoice else abs(row['Tutar'])
                    else:
                        # Karşı taraf (Satıcı) bizi borçlu görür (Fatura +), ödeme alınca alacak (-)
                        return abs(row['Tutar']) if is_invoice else -1 * abs(row['Tutar'])
                else: # Biz Satıcıyız
                    if is_bizim:
                        # Bizim defterde Müşteri borçludur (Fatura +), ödeme yapınca alacak (-)
                        return abs(row['Tutar']) if is_invoice else -1 * abs(row['Tutar'])
                    else:
                        return -1 * abs(row['Tutar']) if is_invoice else abs(row['Tutar'])

            df_bizim['YönlüTutar'] = df_bizim.apply(lambda x: assign_sign(x, True, role), axis=1)
            df_karsi['YönlüTutar'] = df_karsi.apply(lambda x: assign_sign(x, False, role), axis=1)

            # 3. Fatura Karşılaştırma (Merge) [cite: 41]
            # Fatura No üzerinden tam eşleşme (normalize edilmiş)
            merged_inv = pd.merge(
                df_bizim, 
                df_karsi, 
                on='Norm_FaturaNo', 
                how='outer', 
                suffixes=('_Biz', '_Karsi'),
                indicator=True
            )
            
            # Fark Hesaplama [cite: 78]
            merged_inv['Fark'] = merged_inv['Tutar_Biz'].fillna(0) - merged_inv['Tutar_Karsi'].fillna(0)
            merged_inv['Durum'] = np.where(abs(merged_inv['Fark']) < 0.01, 'Mutabık', 'Fark Var')
            
            # Listeler [cite: 74]
            df_fatura_eslesen = merged_inv[merged_inv['_merge'] == 'both']
            df_bizde_var_onlarda_yok = merged_inv[merged_inv['_merge'] == 'left_only']
            df_onlarda_var_bizde_yok = merged_inv[merged_inv['_merge'] == 'right_only']
            
            # 4. Özet Hesaplama [cite: 63, 65]
            total_biz = df_bizim['YönlüTutar'].sum()
            total_karsi = df_karsi['YönlüTutar'].sum()
            fark_total = total_biz - total_karsi # İşaret mantığına göre düzenlenmeli
            
            st.markdown("## Analiz Sonuçları")
            col_res1, col_res2, col_res3 = st.columns(3)
            col_res1.metric("Bizim Bakiye", f"{total_biz:,.2f}")
            col_res2.metric("Karşı Bakiye", f"{total_karsi:,.2f}")
            col_res3.metric("Fark", f"{fark_total:,.2f}", delta_color="inverse")

            # --- EXCEL ÇIKTISI OLUŞTURMA [cite: 60, 61, 106] ---
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Formatlar [cite: 79, 81, 82]
                format_red = workbook.add_format({'font_color': 'red'})
                format_green = workbook.add_format({'font_color': 'green'})
                
                # Fatura Sayfası
                sheet_name = 'Fatura Karşılaştırma'
                df_fatura_eslesen.to_excel(writer, sheet_name=sheet_name, index=False)
                worksheet = writer.sheets[sheet_name]
                
                # Koşullu Biçimlendirme (Fark kolonu için)
                # Fark kolonu indexini bulmak gerekir, burada basitçe tüm satırlara uyguluyoruz
                # Gerçek uygulamada kolon harfi dinamik bulunmalı.
                
                # Bizde Olup Onlarda Olmayan
                df_bizde_var_onlarda_yok.to_excel(writer, sheet_name='Bizde Var Onlarda Yok', index=False)
                
                # Onlarda Olup Bizde Olmayan
                df_onlarda_var_bizde_yok.to_excel(writer, sheet_name='Onlarda Var Bizde Yok', index=False)
                
                # Özet Sayfası [cite: 94]
                summary_data = {
                    'Tanım': ['Toplam Borç/Alacak', 'Son İşlem Tarihi'],
                    'Bizim Kayıtlar': [total_biz, df_bizim['Tarih'].max()],
                    'Karşı Taraf': [total_karsi, df_karsi['Tarih'].max()],
                    'Fark': [fark_total, '-']
                }
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='C-H Özet', index=False)

            st.download_button(
                label="Sonuç Dosyasını İndir (XLSX)",
                data=buffer,
                file_name="RecoMatch_Sonuc.xlsx",
                mime="application/vnd.ms-excel"
            )

else:
    st.info("Lütfen her iki taraf için de dosya yükleyin.")