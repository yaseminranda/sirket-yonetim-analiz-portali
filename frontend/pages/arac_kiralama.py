import html as html_lib
from datetime import date

import pandas as pd
import streamlit as st

from api_client import api_get, api_get_cached, api_post, clear_cache
from validators import clean_phone, is_valid_email, is_valid_identity_no, is_valid_phone

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()
user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
# Madde 5 düzeltmesi: "Genel Müdür" rolü TEK BAŞINA yeterli sayılmamalı --
# departmanı D3 (şirket geneli) olmayan bir "Genel Müdür" (örn. Ev
# departmanının Genel Müdürü) bu sayfaya erişememeli. bkz. Home.py'deki
# is_genel_mudur_d3 ile aynı kural (menüde gizleme ile sayfa içi kontrol
# tutarlı olsun diye).
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])
is_arac_departmani = any(dep in all_session_values for dep in ["D2"])

if not (is_genel_mudur or is_arac_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()


def render_scrollable_table(df: pd.DataFrame, highlight_col: str, highlight_value, height_px: int = 500) -> None:
    """Streamlit'in st.dataframe+Styler kombinasyonunda kaydırma çalışmadığı için,
    kaydırmayı garanti etmek üzere düz HTML/CSS tablo ile render eder.
    'highlight_col' sütunu 'highlight_value' değerine eşit olan satırlar kırmızı vurgulanır.
    """
    headers = "".join(f"<th style='padding:8px 12px; text-align:left; white-space:nowrap;'>{html_lib.escape(str(c))}</th>" for c in df.columns)
    rows_html = []
    for _, row in df.iterrows():
        is_red = row[highlight_col] == highlight_value
        row_style = "background-color:#FFCDD2; color:#B71C1C; font-weight:bold;" if is_red else ""
        cells = "".join(
            f"<td style='padding:6px 12px; border-bottom:1px solid #eee; white-space:nowrap;'>{html_lib.escape(str(v))}</td>"
            for v in row
        )
        rows_html.append(f"<tr style='{row_style}'>{cells}</tr>")

    table_html = f"""
    <div style="max-height:{height_px}px; overflow-y:auto; overflow-x:auto; border:1px solid #ddd; border-radius:6px;">
        <table style="width:100%; border-collapse:collapse; font-size:14px;">
            <thead style="position:sticky; top:0; background:#F8F9FA; z-index:1;">
                <tr>{headers}</tr>
            </thead>
            <tbody>{"".join(rows_html)}</tbody>
        </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

st.title("🚗 Araç Kiralama Yönetimi")
st.caption("Yeni araç kiralama sözleşmesi oluşturma ve aktif kiralama takip paneli.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)


def get_contracts_df() -> pd.DataFrame:
    # Madde 2 düzeltmesi: bu fonksiyon tab1/tab3/tab4 içinde tekrar tekrar
    # çağrılıyor VE Streamlit'te tüm sekmelerin kodu her widget etkileşiminde
    # (örn. "Yeni Kiralama" sekmesindeki Marka/Model filtresine tıklanınca da)
    # yeniden çalışıyor -- önbelleksiz api_get() kullanmak her tıklamada
    # gereksiz bir backend isteğine (ve yavaşlığa) sebep oluyordu.
    data = api_get_cached("/vehicles/contracts")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["baslangic_tarihi"] = pd.to_datetime(df["baslangic_tarihi"], errors="coerce").dt.normalize()
    df["bitis_tarihi"] = pd.to_datetime(df["bitis_tarihi"], errors="coerce").dt.normalize()
    return df


tab1, tab2, tab3, tab4 = st.tabs(
    ["⏰ Teslimat Takibi & SMS İhtar", "➕ Yeni Kiralama Oluştur", "🔄 Teslim Al / İptal Et / Kapat", "📋 Tüm Sözleşmeler"]
)

# ============ TAB 1: TESLİMAT TAKİBİ ============
with tab1:
    st.markdown("### ⏰ Sözleşme Durumları ve Zaman Aşımı Takibi")
    df_track = get_contracts_df()

    if df_track.empty:
        st.info("Sözleşme verisi bulunamadı.")
    else:
        today = pd.to_datetime("today").normalize()
        df_track["kalan_gun"] = (df_track["bitis_tarihi"] - today).dt.days
        closed_mask = df_track["sozlesme_durumu"].astype(str).str.upper().str.contains(
            "TAMAMLA|BITTI|BİTTİ|İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False
        )
        # Madde 5 düzeltmesi: "Son 1 Ayda Tamamlananlar" sekmesi SADECE gerçekten
        # tamamlanmış/bitmiş sözleşmeleri göstermeli -- iptal edilenler bu listeye
        # karışmamalı (bkz. kullanıcı raporu). closed_mask (yukarıdaki, iptalleri
        # de içeren geniş tanım) sadece "Geciken" sekmesinde kapanmış her şeyi
        # hariç tutmak için kullanılmaya devam ediyor.
        completed_only_mask = df_track["sozlesme_durumu"].astype(str).str.upper().str.contains(
            "TAMAMLA|BITTI|BİTTİ", na=False
        )

        t_overdue, t_completed, t_upcoming, t_pending = st.tabs(
            ["🔴 Geciken", "✅ Son 1 Ayda Tamamlananlar", "🟡 Yaklaşanlar (0-3 Gün)", "⏳ Beklemede"]
        )

        with t_overdue:
            is_overdue = (df_track["kalan_gun"] <= 0) & (~closed_mask)
            df_overdue = df_track[is_overdue].sort_values("kalan_gun")
            if not df_overdue.empty:
                st.error(f"⚠️ **{len(df_overdue)}** adet sözleşmenin süresi dolduğu halde araç teslim alınmamış!")

                def highlight_red_rows(row):
                    return ["background-color: #ffcccc; color: #990000; font-weight: bold;"] * len(row)

                display_overdue = df_overdue[["sozlesme_no", "plaka", "marka", "model", "musteri_adi", "kalan_gun", "bitis_tarihi"]].copy()
                display_overdue["bitis_tarihi"] = display_overdue["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_overdue = display_overdue.rename(columns={
                    "sozlesme_no": "Sözleşme No", "plaka": "Plaka", "marka": "Marka", "model": "Model",
                    "musteri_adi": "Müşteri Adı", "kalan_gun": "Kalan Gün", "bitis_tarihi": "Bitiş Tarihi",
                })
                styled_overdue = display_overdue.style.apply(highlight_red_rows, axis=1)
                st.dataframe(styled_overdue, use_container_width=True, hide_index=True)
                st.markdown("---")
                st.markdown("#### 🚨 Geciken Müşteriye İhtar SMS'i Gönder")
                sel = st.selectbox("Sözleşme Seçin:", df_overdue["sozlesme_no"].tolist(), key="sb_arac_geciken_sms")
                row = df_overdue[df_overdue["sozlesme_no"] == sel].iloc[0]
                msg = (
                    f"Sayın {row['musteri_adi']}, #{row['sozlesme_no']} numaralı kiralık aracınızın "
                    f"({row['plaka']}) teslim süresi {abs(int(row['kalan_gun']))} gün gecikmiştir. "
                    "Lütfen aracı acilen teslim ediniz."
                )
                if st.button("📲 Gecikme İhtar SMS'i Gönder", type="primary", key="btn_arac_geciken_sms"):
                    api_post("/finance/notify-sms", {"phone_number": "+90 (555) 987 6543", "message": msg, "notification_type": "GECİKME İHTAR SMS"})
                    st.success("✅ Gecikme ihtar SMS'i gönderildi!")
            else:
                st.success("✅ Zamanı geçtiği halde teslim edilmeyen araç bulunmuyor.")

        with t_completed:
            mask = (df_track["kalan_gun"] <= 0) & (df_track["kalan_gun"] >= -30) & completed_only_mask
            df_c = df_track[mask].sort_values("bitis_tarihi", ascending=False)
            if not df_c.empty:
                display_completed = df_c[["sozlesme_no", "plaka", "musteri_adi", "baslangic_tarihi", "bitis_tarihi", "sozlesme_durumu"]].copy()
                display_completed["baslangic_tarihi"] = display_completed["baslangic_tarihi"].dt.strftime("%d.%m.%Y")
                display_completed["bitis_tarihi"] = display_completed["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_completed = display_completed.rename(columns={
                    "sozlesme_no": "Sözleşme No", "plaka": "Plaka", "musteri_adi": "Müşteri Adı",
                    "baslangic_tarihi": "Başlangıç Tarihi", "bitis_tarihi": "Bitiş Tarihi", "sozlesme_durumu": "Sözleşme Durumu",
                })
                st.dataframe(display_completed, use_container_width=True, hide_index=True)
            else:
                st.info("Son 1 ay içinde tamamlanan sözleşme bulunmuyor.")

        with t_upcoming:
            # Not: bu liste artık ayrı bir backend endpoint'inden (/vehicles/contracts/expiring)
            # geliyor -- bitis_tarihi her zaman canlı okunduğu için bir sözleşme uzatılırsa
            # (yeni bitiş tarihi 0-3 gün penceresinin dışına çıktığı sürece) kendiliğinden
            # bu listeden düşer (ev kiralamadaki 'Yaklaşanlar (30 Gün)' sekmesiyle aynı desen).
            expiring_data = api_get_cached("/vehicles/contracts/expiring") or []
            if expiring_data:
                df_u = pd.DataFrame(expiring_data)
                df_u["bitis_tarihi_fmt"] = pd.to_datetime(df_u["bitis_tarihi"], errors="coerce").dt.strftime("%d.%m.%Y")
                display_upcoming = df_u[["sozlesme_no", "plaka", "musteri_adi", "kalan_gun", "bitis_tarihi_fmt"]].rename(
                    columns={
                        "sozlesme_no": "Sözleşme No", "plaka": "Plaka", "musteri_adi": "Müşteri Adı",
                        "kalan_gun": "Kalan Gün", "bitis_tarihi_fmt": "Bitiş Tarihi",
                    }
                )
                st.dataframe(display_upcoming, use_container_width=True, hide_index=True)

                st.caption(
                    "ℹ️ Bu sözleşmelere sistem her gün otomatik olarak (09:00, sözleşme başına sadece "
                    "ilk seferinde) hem e-posta hem SMS ile bitiş hatırlatması gönderir. Aşağıdan istediğiniz "
                    "an manuel olarak da (tekrar) gönderebilirsiniz."
                )
                st.markdown("---")
                st.markdown("#### 📤 Tüm Listeye Bitiş Hatırlatması Gönder")
                bulk_col1, bulk_col2 = st.columns(2)
                with bulk_col1:
                    if st.button("📧 Tüm Listeye E-posta ile Gönder", key="btn_arac_yaklasan_bulk_email", use_container_width=True):
                        with st.spinner("E-postalar gönderiliyor, lütfen bekleyin..."):
                            result = api_post("/vehicles/contracts/notify-expiry/send-all?method=email", timeout=90)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📧"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
                with bulk_col2:
                    if st.button("📲 Tüm Listeye SMS ile Gönder", key="btn_arac_yaklasan_bulk_sms", use_container_width=True):
                        with st.spinner("SMS'ler gönderiliyor, lütfen bekleyin..."):
                            result = api_post("/vehicles/contracts/notify-expiry/send-all?method=sms", timeout=90)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📲"
                            st.rerun()
                        elif result:
                            st.info(result["message"])

                st.markdown("---")
                st.markdown("#### 📤 Tekil Bitiş Hatırlatması Gönder")
                sel_u = st.selectbox("Sözleşme Seçin:", df_u["sozlesme_no"].tolist(), key="sb_arac_yaklasan_sec")
                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    if st.button("📧 E-posta ile Gönder", key="btn_arac_yaklasan_email", use_container_width=True):
                        with st.spinner("E-posta gönderiliyor, lütfen bekleyin..."):
                            result = api_post(f"/vehicles/contracts/{sel_u}/notify-expiry?method=email", timeout=30)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📧"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
                with col_u2:
                    if st.button("📲 SMS ile Gönder", key="btn_arac_yaklasan_sms", use_container_width=True):
                        with st.spinner("SMS gönderiliyor, lütfen bekleyin..."):
                            result = api_post(f"/vehicles/contracts/{sel_u}/notify-expiry?method=sms", timeout=30)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📲"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
            else:
                st.info("Önümüzdeki 3 gün içinde teslim edilecek sözleşme yok.")

        with t_pending:
            mask = df_track["sozlesme_durumu"].astype(str).str.upper().str.contains("BEKLEMEDE|BEKLEYEN", na=False)
            df_p = df_track[mask].sort_values("baslangic_tarihi")
            if not df_p.empty:
                display_pending = df_p[["sozlesme_no", "plaka", "musteri_adi", "baslangic_tarihi", "bitis_tarihi"]].copy()
                display_pending["baslangic_tarihi"] = display_pending["baslangic_tarihi"].dt.strftime("%d.%m.%Y")
                display_pending["bitis_tarihi"] = display_pending["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_pending = display_pending.rename(columns={
                    "sozlesme_no": "Sözleşme No", "plaka": "Plaka", "musteri_adi": "Müşteri Adı",
                    "baslangic_tarihi": "Başlangıç Tarihi", "bitis_tarihi": "Bitiş Tarihi",
                })
                st.dataframe(display_pending, use_container_width=True, hide_index=True)
            else:
                st.info("Şu an beklemede olan sözleşme yok.")

# ============ TAB 2: YENİ KİRALAMA ============
with tab2:
    st.subheader("Yeni Araç Kiralama Sözleşmesi")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Başlangıç Tarihi", value=date.today(), key="k_baslangic")
    with col2:
        end_date = st.date_input("Bitiş Tarihi", value=date.today(), key="k_bitis")

    if end_date < start_date:
        st.error("Bitiş tarihi başlangıç tarihinden önce olamaz!")
    else:
        # Performans notu: müsait araç listesi SADECE tarih aralığı değiştiğinde
        # backend'den yeniden çekilir (session_state üzerinde tutuluyor). Marka/
        # Model/Fiyat filtreleri zaten aşağıda bu liste üzerinde pandas ile
        # (client-side) süzülüyor -- eskiden her filtre tıklamasında Streamlit
        # sayfayı baştan çalıştırdığı için bu liste de gereksiz yere yeniden
        # indiriliyordu. Not: zaman bazlı bir önbellek DEĞİL; aynı tarih aralığı
        # için tek sefer indirilip filtre etkileşimlerinde tekrar kullanılıyor,
        # tarih değiştiği an otomatik olarak güncel veri çekilir.
        _avail_cache_key = ("arac_musait_araclar", str(start_date), str(end_date))
        if st.session_state.get("_arac_avail_cache_key") != _avail_cache_key:
            st.session_state["_arac_avail_cache_data"] = api_get(
                "/vehicles/available", {"start_date": str(start_date), "end_date": str(end_date)}
            ) or []
            st.session_state["_arac_avail_cache_key"] = _avail_cache_key
        vehicles = st.session_state["_arac_avail_cache_data"]
        customers = api_get_cached("/vehicles/customers") or []

        if not vehicles:
            st.warning("⚠️ Seçilen tarihler arasında müsait araç bulunamadı.")
        else:
            df_vehicles = pd.DataFrame(vehicles)

            st.markdown("🔍 **Müsait Araç Filtreleme Paneli**")
            f1, f2, f3 = st.columns(3)

            with f1:
                brands = ["Tümü"] + sorted(df_vehicles["marka"].dropna().unique().tolist())
                selected_brand = st.selectbox("Marka Filtresi:", brands, key="filter_marka")

            filtered_vehicles = df_vehicles.copy()
            if selected_brand != "Tümü":
                filtered_vehicles = filtered_vehicles[filtered_vehicles["marka"] == selected_brand]

            with f2:
                models = ["Tümü"] + sorted(filtered_vehicles["model"].dropna().unique().tolist())
                selected_model = st.selectbox("Model Filtresi:", models, key="filter_model")

            if selected_model != "Tümü":
                filtered_vehicles = filtered_vehicles[filtered_vehicles["model"] == selected_model]

            with f3:
                min_price = float(df_vehicles["gunluk_ucret"].min()) if not df_vehicles.empty else 0.0
                max_price = float(df_vehicles["gunluk_ucret"].max()) if not df_vehicles.empty else 50000.0
                if min_price == max_price:
                    min_price = 0.0
                price_range = st.slider(
                    "Günlük Ücret Aralığı (₺):", min_value=min_price, max_value=max_price,
                    value=(min_price, max_price), step=100.0, key="filter_arac_fiyat",
                )

            filtered_vehicles = filtered_vehicles[
                (filtered_vehicles["gunluk_ucret"] >= price_range[0]) & (filtered_vehicles["gunluk_ucret"] <= price_range[1])
            ]

            st.markdown("---")

            if filtered_vehicles.empty:
                st.warning("⚠️ Filtrelerinize uyan müsait araç bulunamadı.")
                vehicle_options, vehicle_map = [], {}
            else:
                vehicle_options = ["--- Seçiniz ---"] + [
                    f"{row['marka']} {row['model']} - {row['plaka']} (₺{row['gunluk_ucret']:,.0f}/gün)"
                    for _, row in filtered_vehicles.iterrows()
                ]
                vehicle_map = {
                    f"{row['marka']} {row['model']} - {row['plaka']} (₺{row['gunluk_ucret']:,.0f}/gün)": (row["arac_id"], row["gunluk_ucret"])
                    for _, row in filtered_vehicles.iterrows()
                }

            customer_options = ["--- Seçiniz ---", "➕ [Yeni Müşteri Ekle]"] + [f"{c['isim']} ({c['telefon']})" for c in customers]
            customer_map = {f"{c['isim']} ({c['telefon']})": c["musteri_id"] for c in customers}

            col_m, col_v = st.columns(2)
            with col_m:
                selected_customer = st.selectbox("Müşteri Seçiniz:", customer_options)
                is_new_customer = selected_customer == "➕ [Yeni Müşteri Ekle]"
                new_name = new_phone = new_email = new_tc = ""
                if is_new_customer:
                    new_name = st.text_input("Müşteri Ad Soyad: *")
                    st.caption(
                        "📱 Telefon numarası 0 ile başlamalı, 11 haneli olmalı ve sadece rakamlardan "
                        "oluşmalıdır (örn. 05551234567)."
                    )
                    new_phone = st.text_input("Telefon Numarası: *")
                    new_email = st.text_input("E-posta Adresi: *")
                    new_tc = st.text_input("Kimlik No (TC Kimlik No veya Pasaport No): *")

            with col_v:
                selected_vehicle = st.selectbox("Müsait Araç Seçiniz:", vehicle_options) if vehicle_options else "--- Seçiniz ---"
                is_vehicle_selected = selected_vehicle != "--- Seçiniz ---"

            if is_vehicle_selected:
                vehicle_id, daily_rate = vehicle_map[selected_vehicle]
                total_days = max(1, (end_date - start_date).days)
                estimated_total = total_days * float(daily_rate)
                min_down = estimated_total * 0.5
                st.info(
                    f"📊 **Süre:** {total_days} Gün | **Günlük Ücret:** ₺{daily_rate:,.2f} | "
                    f"**Tahmini Toplam:** ₺{estimated_total:,.2f} | **Asgari Ön Ödeme (%50):** ₺{min_down:,.2f}"
                )
                down_confirmed = st.checkbox(f"Müşteriden minimum %50 ön ödeme (₺{min_down:,.2f}) tahsil edildi.", value=True)
            else:
                vehicle_id = None
                down_confirmed = False

            # Madde 6: kiralayan müşteriye ek en fazla 2 şoför -- eklemek hâlâ
            # isteğe bağlı (hiç şoför eklenmeyebilir), ama bir şoför eklemeye
            # başlarsanız (herhangi bir alanını doldurursanız) 4 alanın tümü
            # zorunlu hale gelir (bkz. aşağıdaki _driver_error kontrolü).
            d1_name = d1_phone = d1_email = d1_tc = ""
            d2_name = d2_phone = d2_email = d2_tc = ""
            with st.expander("🧑‍✈️ Ek Şoför Ekle (İsteğe Bağlı, En Fazla 2 Kişi — Eklerseniz Tüm Bilgiler Zorunludur)"):
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    st.markdown("**1. Ek Şoför**")
                    d1_name = st.text_input("Ad Soyad", key="d1_name")
                    st.caption("📱 0 ile başlamalı, 11 haneli, sadece rakam (örn. 05551234567).")
                    d1_phone = st.text_input("Telefon", key="d1_phone")
                    d1_email = st.text_input("E-posta", key="d1_email")
                    d1_tc = st.text_input("Kimlik No (TC veya Pasaport)", key="d1_tc")
                with col_d2:
                    st.markdown("**2. Ek Şoför**")
                    d2_name = st.text_input("Ad Soyad", key="d2_name")
                    st.caption("📱 0 ile başlamalı, 11 haneli, sadece rakam (örn. 05551234567).")
                    d2_phone = st.text_input("Telefon", key="d2_phone")
                    d2_email = st.text_input("E-posta", key="d2_email")
                    d2_tc = st.text_input("Kimlik No (TC veya Pasaport)", key="d2_tc")

            def _driver_error(name, phone, email, tc, label):
                filled = [v for v in [name, phone, email, tc] if v and v.strip()]
                if 0 < len(filled) < 4:
                    return (
                        f"⚠️ {label} bilgilerini eklemeye başladıysanız Ad Soyad, Telefon, E-posta ve "
                        "Kimlik No alanlarının tümünü doldurmanız gerekiyor (ya da bu şoförü tamamen boş bırakabilirsiniz)."
                    )
                if len(filled) == 4:
                    if not is_valid_phone(phone):
                        return f"⚠️ {label}: Telefon numarası 0 ile başlamalı, 11 haneli olmalı ve sadece rakamlardan oluşmalıdır."
                    if not is_valid_email(email):
                        return f"⚠️ {label}: Lütfen geçerli bir e-posta adresi giriniz (@ işareti içermeli)."
                    if not is_valid_identity_no(tc):
                        return f"⚠️ {label}: Lütfen geçerli bir TC Kimlik No (11 haneli) ya da pasaport numarası (5-20 karakter) giriniz."
                return None

            if st.button("✅ Kiralamayı Onayla ve Başlat", type="primary"):
                driver_error = _driver_error(d1_name, d1_phone, d1_email, d1_tc, "1. Ek Şoför") or _driver_error(
                    d2_name, d2_phone, d2_email, d2_tc, "2. Ek Şoför"
                )
                if selected_customer == "--- Seçiniz ---":
                    st.error("⚠️ Lütfen bir müşteri seçiniz.")
                elif not is_vehicle_selected:
                    st.error("⚠️ Lütfen bir araç seçiniz.")
                elif is_new_customer and not (new_name.strip() and new_phone.strip() and new_email.strip() and new_tc.strip()):
                    st.error("⚠️ Yeni müşteri için Ad Soyad, Telefon, E-posta ve Kimlik No alanlarının tümünü doldurunuz.")
                elif is_new_customer and not is_valid_phone(new_phone):
                    st.error("⚠️ Telefon numarası 0 ile başlamalı, 11 haneli olmalı ve sadece rakamlardan oluşmalıdır (örn. 05551234567).")
                elif is_new_customer and not is_valid_email(new_email):
                    st.error("⚠️ Lütfen geçerli bir e-posta adresi giriniz (@ işareti içermeli, örn. isim@ornek.com).")
                elif is_new_customer and not is_valid_identity_no(new_tc):
                    st.error("⚠️ Lütfen geçerli bir TC Kimlik No (11 haneli, 0 ile başlamayan) ya da pasaport numarası (5-20 karakter, harf/rakam) giriniz.")
                elif driver_error:
                    st.error(driver_error)
                else:
                    payload = {
                        "customer_id": None if is_new_customer else customer_map[selected_customer],
                        "new_customer_name": new_name if is_new_customer else None,
                        "new_customer_phone": clean_phone(new_phone) if is_new_customer else None,
                        "new_customer_email": new_email if is_new_customer else None,
                        "new_customer_tc": new_tc if is_new_customer else None,
                        "vehicle_id": vehicle_id,
                        "start_date": str(start_date),
                        "end_date": str(end_date),
                        "down_payment_confirmed": down_confirmed,
                        "driver1_name": d1_name or None,
                        "driver1_phone": clean_phone(d1_phone) if d1_phone else None,
                        "driver1_email": d1_email or None,
                        "driver1_tc": d1_tc or None,
                        "driver2_name": d2_name or None,
                        "driver2_phone": clean_phone(d2_phone) if d2_phone else None,
                        "driver2_email": d2_email or None,
                        "driver2_tc": d2_tc or None,
                    }
                    result = api_post("/vehicles/contracts", payload)
                    if result and result.get("success"):
                        # Kiralanan araç artık müsait değil -- yukarıdaki önbelleği
                        # geçersiz kılıyoruz ki liste bir sonraki görüntülemede
                        # (tarih aynı kalsa bile) güncel/doğru halde yeniden çekilsin.
                        st.session_state.pop("_arac_avail_cache_key", None)
                        clear_cache()
                        st.session_state["toast_mesaj"] = result["message"]
                        st.session_state["toast_icon"] = "🚗"
                        st.rerun()
                    elif result:
                        st.error(result["message"])

# ============ TAB 3: TESLİM AL / İPTAL ============
with tab3:
    st.subheader("Araç Teslim Al / Sözleşmeyi İptal Et")
    df_all = get_contracts_df()
    if df_all.empty:
        st.info("İşlem yapılacak sözleşme bulunmuyor.")
    else:
        active = df_all[df_all["sozlesme_durumu"].astype(str).str.upper().isin(["DEVAM EDIYOR", "BEKLEMEDE", "AKTİF", "AKTIF"])]
        if active.empty:
            st.info("İşlem yapılacak aktif veya beklemede sözleşme bulunmuyor.")
        else:
            options = ["--- Seçiniz ---"] + [
                f"Sözleşme #{r['sozlesme_no']} | Plaka: {r['plaka']} | Müşteri: {r['musteri_adi']} [{r['sozlesme_durumu']}]"
                for _, r in active.iterrows()
            ]
            option_map = {
                f"Sözleşme #{r['sozlesme_no']} | Plaka: {r['plaka']} | Müşteri: {r['musteri_adi']} [{r['sozlesme_durumu']}]": r["sozlesme_no"]
                for _, r in active.iterrows()
            }
            selected = st.selectbox("İşlem Yapılacak Sözleşmeyi Seçiniz:", options)

            if selected != "--- Seçiniz ---":
                contract_no = option_map[selected]
                debt_info = api_get(f"/finance/debt/{contract_no}", {"category": "ARAC"}) or {"has_debt": False, "remaining": 0}

                col1, col2 = st.columns(2)
                with col1:
                    if debt_info["has_debt"]:
                        st.error(f"⛔ Araç teslim alınamaz! Ödenmemiş ₺{debt_info['remaining']:,.2f} borç bulunuyor.")
                    else:
                        st.success("✅ Ödenmemiş borç bulunmuyor. Teslim alma işlemini onaylayabilirsiniz.")
                        if st.button("🔴 Aracı Teslim Al (Tamamlandı)", type="primary", use_container_width=True):
                            result = api_post(f"/vehicles/contracts/{contract_no}/complete")
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "🔑"
                                st.rerun()
                            elif result:
                                st.error(result["message"])
                with col2:
                    current_status = str(active[active["sozlesme_no"] == contract_no].iloc[0]["sozlesme_durumu"]).upper()
                    if "BEKLEME" in current_status:
                        if st.button("❌ Sözleşmeyi İptal Et / Sil", type="secondary", use_container_width=True):
                            result = api_post(f"/vehicles/contracts/{contract_no}/cancel")
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "🚫"
                                st.rerun()
                            elif result:
                                st.error(result["message"])
                    else:
                        st.info(
                            "ℹ️ Bu sözleşme artık iptal edilemez -- iptal seçeneği sadece henüz "
                            "başlamamış (BEKLEMEDE) sözleşmelerde geçerlidir."
                        )

                st.markdown("---")
                with st.expander("🗓️ Sözleşmeyi Uzat (5. Aşama)"):
                    current_row = active[active["sozlesme_no"] == contract_no].iloc[0]
                    current_end = current_row["bitis_tarihi"].date()
                    st.caption(f"Mevcut bitiş tarihi: **{current_end}**. Aynı araç için bu tarihten sonrasına ait başka bir sözleşme varsa uzatma engellenir.")
                    new_end_date = st.date_input(
                        "Yeni Bitiş Tarihi:", value=current_end, min_value=current_end, key="arac_uzatma_tarih"
                    )
                    if st.button("✅ Sözleşmeyi Uzat", key="arac_uzatma_btn"):
                        if new_end_date <= current_end:
                            st.error("⚠️ Yeni bitiş tarihi mevcut bitiş tarihinden sonra olmalıdır.")
                        else:
                            result = api_post(f"/vehicles/contracts/{contract_no}/extend", {"new_end_date": str(new_end_date)})
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "🗓️"
                                st.rerun()
                            elif result:
                                st.error(result["message"])

# ============ TAB 4: TÜM SÖZLEŞMELER ============
with tab4:
    st.subheader("📋 Tüm Kiralama Geçmişi ve Arama Paneli")
    df_list = get_contracts_df()
    if df_list.empty:
        st.info("Kayıtlı sözleşme bulunmamaktadır.")
    else:
        valid_df = df_list[~df_list["sozlesme_durumu"].astype(str).str.upper().str.contains("İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False)]
        today = pd.to_datetime("today").normalize()
        active_now = valid_df[(valid_df["baslangic_tarihi"] <= today) & (valid_df["bitis_tarihi"] >= today)]

        m1, m2, m3 = st.columns(3)
        m1.metric("Toplam Geçerli Sözleşme", f"{len(valid_df)} Adet")
        m2.metric("Aktif Kiradaki Araçlar", f"{len(active_now)} Adet")
        m3.metric("Toplam Gerçekleşen Ciro", f"₺{valid_df[valid_df['baslangic_tarihi'] <= today]['toplam_tutar'].sum():,.2f}")

        st.markdown("---")
        st.markdown("**🔍 Sözleşme Filtreleme Paneli**")
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            search = st.text_input("Plaka / Müşteri / Sözleşme No Ara:", key="f_arama").strip().lower()
        with f_col2:
            statuses = ["Tümü"] + sorted(df_list["sozlesme_durumu"].dropna().unique().tolist())
            f_status = st.selectbox("Sözleşme Durumu:", statuses, key="f_durum")
        with f_col3:
            brands = ["Tümü"] + sorted(df_list["marka"].dropna().unique().tolist())
            f_brand = st.selectbox("Araç Markası:", brands, key="f_marka")

        filtered = df_list.copy()
        if search:
            filtered = filtered[
                filtered["sozlesme_no"].astype(str).str.contains(search, case=False, na=False)
                | filtered["plaka"].astype(str).str.lower().str.contains(search, na=False)
                | filtered["musteri_adi"].astype(str).str.lower().str.contains(search, na=False)
            ]
        if f_status != "Tümü":
            filtered = filtered[filtered["sozlesme_durumu"] == f_status]
        if f_brand != "Tümü":
            filtered = filtered[filtered["marka"] == f_brand]

        st.caption(f"Filtreleme sonucu **{len(filtered)}** adet sözleşme listeleniyor.")

        closed_or_cancelled = filtered["sozlesme_durumu"].astype(str).str.upper().str.contains(
            "TAMAMLAN|BITTI|BİTTİ|İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False
        )
        is_overdue_mask = (filtered["bitis_tarihi"] <= today) & (~closed_or_cancelled)
        filtered = filtered.copy()
        filtered.loc[is_overdue_mask, "sozlesme_durumu"] = "🚨 GECİKTİ / TESLİM EDİLMEDİ"

        display_df = filtered[["sozlesme_no", "plaka", "marka", "model", "musteri_adi", "calisan_adi", "baslangic_tarihi", "bitis_tarihi", "toplam_tutar", "sozlesme_durumu"]].rename(
            columns={
                "sozlesme_no": "Sözleşme No", "plaka": "Plaka", "marka": "Marka", "model": "Model",
                "musteri_adi": "Müşteri Ad Soyad", "calisan_adi": "İşlemi Yapan Çalışan",
                "baslangic_tarihi": "Başlangıç", "bitis_tarihi": "Bitiş",
                "toplam_tutar": "Toplam Tutar (₺)", "sozlesme_durumu": "Durum",
            }
        )
        display_df["Toplam Tutar (₺)"] = display_df["Toplam Tutar (₺)"].apply(lambda x: f"₺{x:,.2f}")
        display_df["Başlangıç"] = display_df["Başlangıç"].dt.strftime("%Y-%m-%d")
        display_df["Bitiş"] = display_df["Bitiş"].dt.strftime("%Y-%m-%d")

        render_scrollable_table(display_df, highlight_col="Durum", highlight_value="🚨 GECİKTİ / TESLİM EDİLMEDİ")

        st.markdown("---")
        st.markdown("#### 🔗 Sözleşme Detayına Git")
        st.caption("Ödeme geçmişi, döviz/ödeme yöntemi detayı ve makbuz için Sözleşmeler sayfasına gidin.")
        secim_no = st.selectbox(
            "Detayını görüntülemek istediğiniz sözleşmeyi seçin:",
            filtered["sozlesme_no"].tolist(), key="arac_sozlesme_detay_secim",
        )
        if st.button("➡️ Sözleşmeler Sayfasında Aç", key="arac_sozlesme_detay_git"):
            print(f"[TESHIS][arac_kiralama] BUTON TIKLANDI -> secim_no={secim_no!r} type={type(secim_no).__name__}", flush=True)
            st.session_state["secili_sozlesme_no"] = secim_no
            st.session_state["secili_sozlesme_kategori"] = "ARAC"
            print(f"[TESHIS][arac_kiralama] session_state ayarlandi -> secili_sozlesme_no={st.session_state.get('secili_sozlesme_no')!r}, secili_sozlesme_kategori={st.session_state.get('secili_sozlesme_kategori')!r}, switch_page cagriliyor", flush=True)
            st.switch_page("pages/sozlesmeler.py")