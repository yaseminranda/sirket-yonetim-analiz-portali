from datetime import date

import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta

from api_client import api_get, api_get_cached, api_post, clear_cache
from validators import clean_phone, is_valid_email, is_valid_identity_no, is_valid_phone

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()
user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
# Madde 5 düzeltmesi: "Genel Müdür" rolü TEK BAŞINA yeterli sayılmamalı --
# departmanı D3 (şirket geneli) olmayan bir "Genel Müdür" (örn. Araç
# departmanının Genel Müdürü) bu sayfaya erişememeli. bkz. Home.py'deki
# is_genel_mudur_d3 ile aynı kural (menüde gizleme ile sayfa içi kontrol
# tutarlı olsun diye).
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])
is_ev_departmani = any(dep in all_session_values for dep in ["D1"])

if not (is_genel_mudur or is_ev_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()

st.title("🏠 Ev / Konut Kiralama Yönetimi")
st.caption("Yeni konut kiralama sözleşmesi oluşturma ve aktif kiracı takip paneli.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)


def get_contracts_df() -> pd.DataFrame:
    # Madde 2 düzeltmesi: bu fonksiyon tab1/tab3/tab4 içinde tekrar tekrar
    # çağrılıyor VE Streamlit'te tüm sekmelerin kodu her widget etkileşiminde
    # (örn. "Yeni Kiralama" sekmesindeki İl/İlçe filtresine tıklanınca da)
    # yeniden çalışıyor -- önbelleksiz api_get() kullanmak her tıklamada
    # gereksiz bir backend isteğine (ve yavaşlığa) sebep oluyordu.
    data = api_get_cached("/housing/contracts")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["baslangic_tarihi"] = pd.to_datetime(df["baslangic_tarihi"], errors="coerce").dt.normalize()
    df["bitis_tarihi"] = pd.to_datetime(df["bitis_tarihi"], errors="coerce").dt.normalize()
    return df


tab1, tab2, tab3, tab4 = st.tabs(
    ["⏰ Teslimat Takibi & SMS İhtar", "➕ Yeni Kiralama Oluştur", "🔄 Ev Tahliye / İptal Et / Kapat", "📋 Tüm Ev Sözleşmeleri"]
)

# ============ TAB 1 ============
with tab1:
    st.subheader("⏰ Konut Sözleşme Durumu, Vade & Zaman Aşımı Takibi")
    df_house = get_contracts_df()

    if df_house.empty:
        st.info("Sözleşme verisi bulunamadı.")
    else:
        today = pd.to_datetime("today").normalize()
        df_house["kalan_gun"] = (df_house["bitis_tarihi"] - today).dt.days
        status_str = df_house["sozlesme_durumu"].astype(str).str.upper()

        t_overdue, t_rent_overdue, t_completed, t_upcoming, t_pending = st.tabs(
            ["🚨 Teslimi Gecikenler", "💸 Geciken Kira Ödemeleri", "🔴 Son 1 Ayda Bitenler", "🟡 Yaklaşanlar (30 Gün)", "⏳ Beklemede"]
        )

        with t_overdue:
            is_overdue = (df_house["kalan_gun"] < 0) & status_str.str.contains("DEVAM EDİYOR|DEVAM EDIYOR|AKTİF|AKTIF", na=False)
            df_o = df_house[is_overdue].sort_values("kalan_gun")
            if not df_o.empty:
                st.error(f"⚠️ Süresi dolduğu hâlde kapatılmamış **{len(df_o)}** adet sözleşme bulunmaktadır.")

                def highlight_red_rows(row):
                    return ["background-color: #ffcccc; color: #990000; font-weight: bold;"] * len(row)

                display_overdue = df_o[["sozlesme_no", "musteri_adi", "il", "ilce", "baslangic_tarihi", "bitis_tarihi", "aylik_kira"]].copy()
                display_overdue["baslangic_tarihi"] = display_overdue["baslangic_tarihi"].dt.strftime("%d.%m.%Y")
                display_overdue["bitis_tarihi"] = display_overdue["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_overdue = display_overdue.rename(columns={
                    "sozlesme_no": "Sözleşme No", "musteri_adi": "Müşteri Adı", "il": "İl", "ilce": "İlçe",
                    "baslangic_tarihi": "Başlangıç Tarihi", "bitis_tarihi": "Bitiş Tarihi", "aylik_kira": "Aylık Kira (₺)",
                })
                styled_overdue = display_overdue.style.apply(highlight_red_rows, axis=1).format({"Aylık Kira (₺)": "₺{:,.2f}"})
                st.dataframe(styled_overdue, use_container_width=True, hide_index=True)
                st.markdown("---")
                sel = st.selectbox("Sözleşme Seçin:", df_o["sozlesme_no"].tolist(), key="sb_ev_geciken_sms")
                row = df_o[df_o["sozlesme_no"] == sel].iloc[0]
                msg = f"Sayın {row['musteri_adi']}, #{row['sozlesme_no']} numaralı konut sözleşmenizin süresi {abs(int(row['kalan_gun']))} gün önce dolmuştur."
                if st.button("📲 Tahliye/İhtar SMS'i Gönder", type="primary", key="btn_ev_geciken_sms"):
                    api_post("/finance/notify-sms", {"phone_number": "+90 (555) 123 4567", "message": msg, "notification_type": "EV GECİKME İHTAR SMS"})
                    st.success("✅ Gecikme ihtar SMS'i gönderildi!")
            else:
                st.success("🎉 Süresi dolup da teslimi geciken sözleşme bulunmamaktadır.")

        with t_rent_overdue:
            st.subheader("💸 Vadesi Gelip Ödenmeyen Aylık Kira Taksitleri")
            rent_data = api_get_cached("/housing/contracts/overdue-rent") or []
            if rent_data:
                df_rent = pd.DataFrame(rent_data)
                st.warning(f"⚠️ **{len(df_rent)}** adet sözleşmede ödenmemiş vadesi geçmiş kira borcu var!")
                display_rent = df_rent[["sozlesme_no", "musteri_adi", "apartman_adi", "daire_no", "aylik_kira", "gecen_ay_sayisi", "odenen_kira", "geciken_kira_borcu"]].rename(
                    columns={
                        "sozlesme_no": "Sözleşme No", "musteri_adi": "Müşteri Adı", "apartman_adi": "Apartman Adı",
                        "daire_no": "Daire No", "aylik_kira": "Aylık Kira (₺)", "gecen_ay_sayisi": "Geciken Ay Sayısı",
                        "odenen_kira": "Ödenen Kira (₺)", "geciken_kira_borcu": "Geciken Kira Borcu (₺)",
                    }
                )
                st.dataframe(display_rent, use_container_width=True, hide_index=True)
                st.markdown("---")
                sel2 = st.selectbox("Sözleşme Seçin:", df_rent["sozlesme_no"].tolist(), key="sb_ev_kira_geciken")
                row2 = df_rent[df_rent["sozlesme_no"] == sel2].iloc[0]
                msg2 = f"Sayın {row2['musteri_adi']}, #{row2['sozlesme_no']} sözleşmenize ait ₺{row2['geciken_kira_borcu']:,.2f} vadesi geçmiş kira borcunuz bulunmaktadır."
                if st.button("📲 Kira İhtar SMS'i Gönder", type="primary", key="btn_ev_kira_sms"):
                    api_post("/finance/notify-sms", {"phone_number": row2.get("telefon") or "+905550000000", "message": msg2, "notification_type": "KİRA_VADE_UYARISI"})
                    st.success("✅ Kira ihtar SMS'i gönderildi!")
            else:
                st.success("🎉 Vadesi geçmiş aylık kira borcu olan kiracı bulunmamaktadır.")

        with t_completed:
            # Madde 5 düzeltmesi: sekme adı "Son 1 Ayda Bitenler" dese de kod
            # gerçek bir tarih sınırı uygulamıyordu (tüm zamanların tamamlanmış
            # sözleşmelerini gösteriyordu) -- araç tarafıyla tutarlı olacak
            # şekilde gerçek 30 günlük sınır eklendi.
            completed_only_mask = status_str.str.contains("TAMAMLANDI|BITTI|BİTTİ", na=False)
            mask = (df_house["kalan_gun"] <= 0) & (df_house["kalan_gun"] >= -30) & completed_only_mask
            df_c = df_house[mask].sort_values("bitis_tarihi", ascending=False)
            if not df_c.empty:
                display_completed = df_c[["sozlesme_no", "musteri_adi", "il", "ilce", "baslangic_tarihi", "bitis_tarihi", "aylik_kira"]].copy()
                display_completed["baslangic_tarihi"] = display_completed["baslangic_tarihi"].dt.strftime("%d.%m.%Y")
                display_completed["bitis_tarihi"] = display_completed["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_completed = display_completed.rename(columns={
                    "sozlesme_no": "Sözleşme No", "musteri_adi": "Müşteri Adı", "il": "İl", "ilce": "İlçe",
                    "baslangic_tarihi": "Başlangıç Tarihi", "bitis_tarihi": "Bitiş Tarihi", "aylik_kira": "Aylık Kira (₺)",
                })
                st.dataframe(display_completed, use_container_width=True, hide_index=True)
            else:
                st.info("Tamamlanmış/kapatılmış sözleşme bulunmuyor.")

        with t_upcoming:
            # Not: bu liste artık ayrı bir backend endpoint'inden (/housing/contracts/expiring)
            # geliyor -- bitis_tarihi her zaman canlı okunduğu için bir sözleşme uzatılırsa
            # (yeni bitiş tarihi 30 gün penceresinin dışına çıktığı sürece) kendiliğinden
            # bu listeden düşer.
            expiring_data = api_get_cached("/housing/contracts/expiring") or []
            if expiring_data:
                df_u = pd.DataFrame(expiring_data)
                df_u["bitis_tarihi_fmt"] = pd.to_datetime(df_u["bitis_tarihi"], errors="coerce").dt.strftime("%d.%m.%Y")
                display_upcoming = df_u[["sozlesme_no", "musteri_adi", "il", "ilce", "kalan_gun", "bitis_tarihi_fmt"]].rename(
                    columns={
                        "sozlesme_no": "Sözleşme No", "musteri_adi": "Müşteri Adı", "il": "İl", "ilce": "İlçe",
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
                    if st.button("📧 Tüm Listeye E-posta ile Gönder", key="btn_ev_yaklasan_bulk_email", use_container_width=True):
                        # Not: listedeki her sözleşme için sırayla gerçek bir SMTP bağlantısı
                        # açıldığından bu birkaç saniyeden uzun sürebilir -- varsayılan 15sn
                        # yerine daha uzun bir zaman aşımı kullanılıyor (bkz. api_client.py).
                        with st.spinner("E-postalar gönderiliyor, lütfen bekleyin..."):
                            result = api_post("/housing/contracts/notify-expiry/send-all?method=email", timeout=90)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📧"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
                with bulk_col2:
                    if st.button("📲 Tüm Listeye SMS ile Gönder", key="btn_ev_yaklasan_bulk_sms", use_container_width=True):
                        with st.spinner("SMS'ler gönderiliyor, lütfen bekleyin..."):
                            result = api_post("/housing/contracts/notify-expiry/send-all?method=sms", timeout=90)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📲"
                            st.rerun()
                        elif result:
                            st.info(result["message"])

                st.markdown("---")
                st.markdown("#### 📤 Tekil Bitiş Hatırlatması Gönder")
                sel_u = st.selectbox("Sözleşme Seçin:", df_u["sozlesme_no"].tolist(), key="sb_ev_yaklasan_sec")
                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    if st.button("📧 E-posta ile Gönder", key="btn_ev_yaklasan_email", use_container_width=True):
                        with st.spinner("E-posta gönderiliyor, lütfen bekleyin..."):
                            result = api_post(f"/housing/contracts/{sel_u}/notify-expiry?method=email", timeout=30)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📧"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
                with col_u2:
                    if st.button("📲 SMS ile Gönder", key="btn_ev_yaklasan_sms", use_container_width=True):
                        with st.spinner("SMS gönderiliyor, lütfen bekleyin..."):
                            result = api_post(f"/housing/contracts/{sel_u}/notify-expiry?method=sms", timeout=30)
                        if result and result.get("success"):
                            st.session_state["toast_mesaj"] = result["message"]
                            st.session_state["toast_icon"] = "📲"
                            st.rerun()
                        elif result:
                            st.info(result["message"])
            else:
                st.info("Önümüzdeki 30 gün içinde bitecek sözleşme yok.")

        with t_pending:
            mask = status_str.str.contains("BEKLEMEDE|BEKLEYEN", na=False)
            df_p = df_house[mask].sort_values("baslangic_tarihi")
            if not df_p.empty:
                display_pending = df_p[["sozlesme_no", "musteri_adi", "il", "ilce", "baslangic_tarihi", "bitis_tarihi"]].copy()
                display_pending["baslangic_tarihi"] = display_pending["baslangic_tarihi"].dt.strftime("%d.%m.%Y")
                display_pending["bitis_tarihi"] = display_pending["bitis_tarihi"].dt.strftime("%d.%m.%Y")
                display_pending = display_pending.rename(columns={
                    "sozlesme_no": "Sözleşme No", "musteri_adi": "Müşteri Adı", "il": "İl", "ilce": "İlçe",
                    "baslangic_tarihi": "Başlangıç Tarihi", "bitis_tarihi": "Bitiş Tarihi",
                })
                st.dataframe(display_pending, use_container_width=True, hide_index=True)
            else:
                st.info("Şu an beklemede olan sözleşme yok.")

# ============ TAB 2: YENİ KİRALAMA ============
with tab2:
    st.subheader("Yeni Ev Kiralama Sözleşmesi")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Kira Başlangıç Tarihi", value=date.today(), key="ev_baslangic")
    with col2:
        end_date = st.date_input("Kira Bitiş Tarihi", value=date.today(), key="ev_bitis")

    if end_date < start_date:
        st.error("Bitiş tarihi başlangıç tarihinden önce olamaz!")
    else:
        diff = relativedelta(end_date, start_date)
        full_months = diff.years * 12 + diff.months
        total_months_str = f"{full_months}.0" if diff.days == 0 else f"~{round(full_months + diff.days / 30.0, 1)}"

        # Performans notu: müsait daire listesi SADECE tarih aralığı değiştiğinde
        # backend'den yeniden çekilir (session_state üzerinde tutuluyor). İl/İlçe/
        # Oda/Fiyat filtreleri zaten aşağıda bu liste üzerinde pandas ile (client-side)
        # süzülüyor -- eskiden her filtre tıklamasında Streamlit sayfayı baştan
        # çalıştırdığı için bu liste de gereksiz yere yeniden indiriliyordu (5-6 sn.
        # gecikmenin asıl sebebi buydu). Not: zaman bazlı bir önbellek DEĞİL; aynı
        # tarih aralığı için tek sefer indirilip filtre etkileşimlerinde tekrar
        # kullanılıyor, tarih değiştiği an otomatik olarak güncel veri çekilir.
        _avail_cache_key = ("ev_musait_daireler", str(start_date), str(end_date))
        if st.session_state.get("_ev_avail_cache_key") != _avail_cache_key:
            st.session_state["_ev_avail_cache_data"] = api_get(
                "/housing/available", {"start_date": str(start_date), "end_date": str(end_date)}
            ) or []
            st.session_state["_ev_avail_cache_key"] = _avail_cache_key
        apartments = st.session_state["_ev_avail_cache_data"]
        customers = api_get_cached("/housing/customers") or []

        if not apartments:
            st.warning("⚠️ Seçilen tarihler arasında müsait daire bulunamadı.")
        else:
            df_apartments = pd.DataFrame(apartments)

            st.markdown("🔍 **Müsait Daire Filtreleme Paneli**")
            f1, f2, f3, f4 = st.columns(4)

            with f1:
                cities = ["Tümü"] + sorted(df_apartments["il"].dropna().unique().tolist())
                selected_city = st.selectbox("İl Filtresi:", cities, key="filter_il")

            filtered_apartments = df_apartments.copy()
            if selected_city != "Tümü":
                filtered_apartments = filtered_apartments[filtered_apartments["il"] == selected_city]

            with f2:
                districts = ["Tümü"] + sorted(filtered_apartments["ilce"].dropna().unique().tolist())
                selected_district = st.selectbox("İlçe Filtresi:", districts, key="filter_ilce")

            if selected_district != "Tümü":
                filtered_apartments = filtered_apartments[filtered_apartments["ilce"] == selected_district]

            with f3:
                rooms = ["Tümü"] + sorted(filtered_apartments["oda_sayisi"].dropna().unique().tolist())
                selected_room = st.selectbox("Oda Sayısı:", rooms, key="filter_oda")

            if selected_room != "Tümü":
                filtered_apartments = filtered_apartments[filtered_apartments["oda_sayisi"] == selected_room]

            with f4:
                min_rent = float(df_apartments["aylik_kira"].min()) if not df_apartments.empty else 0.0
                max_rent = float(df_apartments["aylik_kira"].max()) if not df_apartments.empty else 100000.0
                if min_rent == max_rent:
                    min_rent = 0.0
                price_range = st.slider(
                    "Aylık Kira Aralığı (₺):", min_value=min_rent, max_value=max_rent,
                    value=(min_rent, max_rent), step=500.0, key="filter_fiyat",
                )

            filtered_apartments = filtered_apartments[
                (filtered_apartments["aylik_kira"] >= price_range[0]) & (filtered_apartments["aylik_kira"] <= price_range[1])
            ]

            st.markdown("---")

            if filtered_apartments.empty:
                st.warning("⚠️ Filtrelerinize uyan müsait daire bulunamadı.")
                apt_options, apt_map = [], {}
            else:
                apt_options = ["--- Daire Seçiniz ---"] + [
                    f"{row['apartman_adi']} No:{row['daire_no']} ({row['oda_sayisi']}) - {row['il']}/{row['ilce']} (₺{row['aylik_kira']:,.0f}/ay)"
                    for _, row in filtered_apartments.iterrows()
                ]
                apt_map = {
                    f"{row['apartman_adi']} No:{row['daire_no']} ({row['oda_sayisi']}) - {row['il']}/{row['ilce']} (₺{row['aylik_kira']:,.0f}/ay)": (row["daire_id"], row["aylik_kira"])
                    for _, row in filtered_apartments.iterrows()
                }

            customer_options = ["--- Seçiniz ---", "➕ [Yeni Müşteri/Kiracı Ekle]"] + [f"{c['isim']} ({c['telefon']})" for c in customers]
            customer_map = {f"{c['isim']} ({c['telefon']})": c["musteri_id"] for c in customers}

            col_m, col_d = st.columns(2)
            with col_m:
                selected_customer = st.selectbox("Kiracı Seçiniz:", customer_options)
                is_new_customer = selected_customer == "➕ [Yeni Müşteri/Kiracı Ekle]"
                new_name = new_phone = new_email = new_tc = ""
                if is_new_customer:
                    new_name = st.text_input("Kiracı Ad Soyad: *")
                    st.caption(
                        "📱 Telefon numarası 0 ile başlamalı, 11 haneli olmalı ve sadece rakamlardan "
                        "oluşmalıdır (örn. 05551234567)."
                    )
                    new_phone = st.text_input("Telefon Numarası: *")
                    new_email = st.text_input("E-posta Adresi: *")
                    new_tc = st.text_input("Kimlik No (TC Kimlik No veya Pasaport No): *")

            with col_d:
                selected_apt = st.selectbox("Müsait Daire Seçiniz:", apt_options) if apt_options else "--- Daire Seçiniz ---"
                is_apt_selected = selected_apt != "--- Daire Seçiniz ---"

            if is_apt_selected:
                apartment_id, monthly_rent = apt_map[selected_apt]
                estimated_total = round((full_months * float(monthly_rent)) + (diff.days * (float(monthly_rent) / 30.0)), 2)
                deposit_amount = st.number_input("Depozito Tutarı (₺):", value=float(monthly_rent), step=500.0)
                st.info(
                    f"📊 **Süre:** {total_months_str} Ay | **Aylık Kira:** ₺{monthly_rent:,.2f} | "
                    f"**Depozito:** ₺{deposit_amount:,.2f} | **Tahmini Toplam Ciro:** ₺{estimated_total:,.2f}"
                )

                st.markdown("---")
                st.markdown("💳 **Esnek Ödeme Planı (Madde 8)**")
                plan_choice = st.radio(
                    "Ödeme planı tipi seçiniz:",
                    ["Süre Bazlı (Aylık Kira Tutarınca Otomatik Taksit)", "Tutar Bazlı (Sabit Taksit Tutarı Girin)"],
                    key="ev_plan_tipi",
                )
                plan_type = "SURE_BAZLI" if plan_choice.startswith("Süre") else "TUTAR_BAZLI"
                installment_amount = None
                if plan_type == "TUTAR_BAZLI":
                    installment_amount = st.number_input(
                        "Sabit Taksit Tutarı (₺):", min_value=1.0, value=float(monthly_rent), step=500.0, key="ev_taksit_tutar"
                    )
                    taksit_sayisi = -(-int(estimated_total) // int(installment_amount)) if installment_amount else 0
                    st.caption(f"ℹ️ Bu tutara göre yaklaşık **{taksit_sayisi}** taksit oluşturulacak.")
                else:
                    st.caption(f"ℹ️ Aylık kira tutarınca (₺{monthly_rent:,.2f}) yaklaşık **{total_months_str}** taksit oluşturulacak.")
            else:
                apartment_id, deposit_amount = None, 0.0
                plan_type, installment_amount = "SURE_BAZLI", None

            if st.button("✅ Ev Kiralamasını Onayla ve Başlat", type="primary"):
                if selected_customer == "--- Seçiniz ---":
                    st.error("⚠️ Lütfen bir kiracı seçiniz.")
                elif not is_apt_selected:
                    st.error("⚠️ Lütfen bir daire seçiniz.")
                elif is_new_customer and not (new_name.strip() and new_phone.strip() and new_email.strip() and new_tc.strip()):
                    st.error("⚠️ Yeni kiracı için Ad Soyad, Telefon, E-posta ve Kimlik No alanlarının tümünü doldurunuz.")
                elif is_new_customer and not is_valid_phone(new_phone):
                    st.error("⚠️ Telefon numarası 0 ile başlamalı, 11 haneli olmalı ve sadece rakamlardan oluşmalıdır (örn. 05551234567).")
                elif is_new_customer and not is_valid_email(new_email):
                    st.error("⚠️ Lütfen geçerli bir e-posta adresi giriniz (@ işareti içermeli, örn. isim@ornek.com).")
                elif is_new_customer and not is_valid_identity_no(new_tc):
                    st.error("⚠️ Lütfen geçerli bir TC Kimlik No (11 haneli, 0 ile başlamayan) ya da pasaport numarası (5-20 karakter, harf/rakam) giriniz.")
                else:
                    payload = {
                        "customer_id": None if is_new_customer else customer_map[selected_customer],
                        "new_customer_name": new_name if is_new_customer else None,
                        "new_customer_phone": clean_phone(new_phone) if is_new_customer else None,
                        "new_customer_email": new_email if is_new_customer else None,
                        "new_customer_tc": new_tc if is_new_customer else None,
                        "apartment_id": apartment_id, "start_date": str(start_date), "end_date": str(end_date),
                        "deposit_amount": deposit_amount,
                        "plan_type": plan_type, "installment_amount": installment_amount,
                    }
                    result = api_post("/housing/contracts", payload)
                    if result and result.get("success"):
                        # Kiralanan daire artık müsait değil -- yukarıdaki önbelleği
                        # geçersiz kılıyoruz ki liste bir sonraki görüntülemede
                        # (tarih aynı kalsa bile) güncel/doğru halde yeniden çekilsin.
                        st.session_state.pop("_ev_avail_cache_key", None)
                        clear_cache()
                        st.session_state["toast_mesaj"] = result["message"]
                        st.session_state["toast_icon"] = "🏠"
                        st.rerun()
                    elif result:
                        st.error(result["message"])

# ============ TAB 3: TAHLİYE / İPTAL ============
with tab3:
    st.subheader("Daire Tahliye Et / Sözleşmeyi İptal Et")
    df_all = get_contracts_df()
    if df_all.empty:
        st.info("İşlem yapılacak sözleşme bulunmuyor.")
    else:
        active = df_all[df_all["sozlesme_durumu"].astype(str).str.upper().isin(["DEVAM EDİYOR", "DEVAM EDIYOR", "BEKLEMEDE", "AKTİF", "AKTIF"])]
        if active.empty:
            st.info("İşlem yapılacak aktif veya beklemede sözleşme bulunmuyor.")
        else:
            options = ["--- Seçiniz ---"] + [
                f"Sözleşme #{r['sozlesme_no']} | {r['apartman_adi']} No:{r['daire_no']} | Kiracı: {r['musteri_adi']} [{r['sozlesme_durumu']}]"
                for _, r in active.iterrows()
            ]
            option_map = {
                f"Sözleşme #{r['sozlesme_no']} | {r['apartman_adi']} No:{r['daire_no']} | Kiracı: {r['musteri_adi']} [{r['sozlesme_durumu']}]": r["sozlesme_no"]
                for _, r in active.iterrows()
            }
            selected = st.selectbox("İşlem Yapılacak Sözleşmeyi Seçiniz:", options)

            if selected != "--- Seçiniz ---":
                contract_no = option_map[selected]
                debt_info = api_get(f"/finance/debt/{contract_no}", {"category": "EV"}) or {"has_debt": False, "remaining": 0}
                contract_row = active[active["sozlesme_no"] == contract_no].iloc[0]

                if debt_info["has_debt"]:
                    st.error(f"⛔ Ev teslim alınamaz! Ödenmemiş ₺{debt_info['remaining']:,.2f} borç bulunuyor.")
                else:
                    st.success("✅ Ödenmemiş kira borcu bulunmuyor. Tahliye işlemine geçebilirsiniz.")

                original_deposit = float(contract_row["depozito"]) if contract_row["depozito"] else 0.0
                st.markdown("#### 🛡️ Depozito İade ve Hasar Düşüm Yönetimi")
                st.info(f"Orijinal Alınan Depozito Tutarı: **₺{original_deposit:,.2f}**")
                damage_cost = st.number_input("Varsa Konuttaki Hasar Bedeli (₺):", min_value=0.0, max_value=original_deposit, value=0.0, step=100.0)
                st.warning(f"Kiracıya İade Edilecek Net Depozito: **₺{original_deposit - damage_cost:,.2f}**")

                col1, col2 = st.columns(2)
                with col1:
                    if not debt_info["has_debt"]:
                        if st.button("🔴 Ev Tahliyesini Onayla ve İşlemi Bitir", type="primary", use_container_width=True):
                            result = api_post(f"/housing/contracts/{contract_no}/complete", {"damage_cost": damage_cost})
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "🔑"
                                st.rerun()
                            elif result:
                                st.error(result["message"])
                with col2:
                    current_status = str(contract_row["sozlesme_durumu"]).upper()
                    if "BEKLEME" in current_status:
                        if st.button("❌ Sözleşmeyi İptal Et / Sil", type="secondary", use_container_width=True):
                            result = api_post(f"/housing/contracts/{contract_no}/cancel")
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
                col3, col4 = st.columns(2)
                with col3:
                    with st.expander("🗓️ Sözleşmeyi Uzat (5. Aşama)"):
                        current_end = contract_row["bitis_tarihi"].date()
                        st.caption(f"Mevcut bitiş tarihi: **{current_end}**. Aynı daire için bu tarihten sonrasına ait başka bir sözleşme varsa uzatma engellenir.")
                        new_end_date = st.date_input(
                            "Yeni Bitiş Tarihi:", value=current_end, min_value=current_end, key="ev_uzatma_tarih"
                        )
                        if st.button("✅ Sözleşmeyi Uzat", key="ev_uzatma_btn"):
                            if new_end_date <= current_end:
                                st.error("⚠️ Yeni bitiş tarihi mevcut bitiş tarihinden sonra olmalıdır.")
                            else:
                                result = api_post(f"/housing/contracts/{contract_no}/extend", {"new_end_date": str(new_end_date)})
                                if result and result.get("success"):
                                    clear_cache()
                                    st.session_state["toast_mesaj"] = result["message"]
                                    st.session_state["toast_icon"] = "🗓️"
                                    st.rerun()
                                elif result:
                                    st.error(result["message"])
                with col4:
                    with st.expander("✅ Tahliye Onayı Ver (5. Aşama)"):
                        st.caption(
                            "Kiracının bu sözleşme bitiminde (veya öncesinde) kesin olarak taşınacağı "
                            "onaylanırsa, bu daire -- sözleşme hâlâ teknik olarak açıkken bile -- yeni "
                            "sözleşmeler için müsait olarak görünmeye başlar (gelir kaybını önlemek için)."
                        )
                        if st.button("✅ Tahliyeyi Onayla", key="ev_tahliye_onay_btn"):
                            result = api_post(f"/housing/contracts/{contract_no}/confirm-vacate")
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "✅"
                                st.rerun()
                            elif result:
                                st.error(result["message"])

# ============ TAB 4: TÜM SÖZLEŞMELER ============
with tab4:
    st.subheader("📋 Tüm Ev Kiralama Geçmişi ve Arama Paneli")
    df_list = get_contracts_df()
    if df_list.empty:
        st.info("Kayıtlı ev kiralama sözleşmesi bulunmamaktadır.")
    else:
        valid_df = df_list[~df_list["sozlesme_durumu"].astype(str).str.upper().str.contains("İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False)]
        today = pd.to_datetime("today").normalize()
        active_now = valid_df[(valid_df["baslangic_tarihi"] <= today) & (valid_df["bitis_tarihi"] >= today)]

        m1, m2, m3 = st.columns(3)
        m1.metric("Toplam Geçerli Sözleşme", f"{len(valid_df)} Adet")
        m2.metric("Aktif Kiradaki Daireler", f"{len(active_now)} Adet")
        m3.metric("Toplam Gerçekleşen Ciro", f"₺{valid_df[valid_df['baslangic_tarihi'] <= today]['toplam_tutar'].sum():,.2f}")

        st.markdown("---")
        st.markdown("**🔍 Sözleşme Filtreleme Paneli**")
        f_col1, f_col2, f_col3 = st.columns(3)
        with f_col1:
            search = st.text_input("Apt Adı / Kiracı / Sözleşme No Ara:", key="f_ev_arama").strip().lower()
        with f_col2:
            statuses = ["Tümü"] + sorted(df_list["sozlesme_durumu"].dropna().unique().tolist())
            f_status = st.selectbox("Sözleşme Durumu:", statuses, key="f_ev_durum")
        with f_col3:
            cities = ["Tümü"] + sorted(df_list["il"].dropna().unique().tolist())
            f_city = st.selectbox("İl Filtresi:", cities, key="f_ev_sehir")

        filtered = df_list.copy()
        if search:
            filtered = filtered[
                filtered["sozlesme_no"].astype(str).str.contains(search, case=False, na=False)
                | filtered["apartman_adi"].astype(str).str.lower().str.contains(search, na=False)
                | filtered["musteri_adi"].astype(str).str.lower().str.contains(search, na=False)
            ]
        if f_status != "Tümü":
            filtered = filtered[filtered["sozlesme_durumu"] == f_status]
        if f_city != "Tümü":
            filtered = filtered[filtered["il"] == f_city]

        st.caption(f"Filtreleme sonucu **{len(filtered)}** adet sözleşme listeleniyor.")

        display_df = filtered[["sozlesme_no", "apartman_adi", "daire_no", "oda_sayisi", "il", "ilce", "musteri_adi", "calisan_adi", "baslangic_tarihi", "bitis_tarihi", "aylik_kira", "depozito", "toplam_tutar", "sozlesme_durumu"]].rename(
            columns={
                "sozlesme_no": "Sözleşme No", "apartman_adi": "Apartman Adı", "daire_no": "Daire No", "oda_sayisi": "Oda",
                "il": "İl", "ilce": "İlçe", "musteri_adi": "Kiracı Ad Soyad", "calisan_adi": "İşlemi Yapan Çalışan",
                "baslangic_tarihi": "Başlangıç", "bitis_tarihi": "Bitiş", "aylik_kira": "Aylık Kira (₺)",
                "depozito": "Depozito (₺)", "toplam_tutar": "Toplam Ciro (₺)", "sozlesme_durumu": "Durum",
            }
        )

        def highlight_overdue(row):
            status = str(row["Durum"]).upper()
            is_active = status in ["DEVAM EDİYOR", "DEVAM EDIYOR", "AKTİF", "AKTIF"]
            end_passed = row["Bitiş"] < today
            if is_active and end_passed:
                return ["background-color: #ffcccc; color: #990000; font-weight: bold;"] * len(row)
            return [""] * len(row)

        styled_df = display_df.style.apply(highlight_overdue, axis=1).format({
            "Aylık Kira (₺)": "₺{:,.2f}", "Depozito (₺)": "₺{:,.2f}", "Toplam Ciro (₺)": "₺{:,.2f}",
            # Not: "Bitiş" sütunu highlight_overdue içinde tarih karşılaştırması için hâlâ
            # datetime tipinde kalmalı -- bu yüzden dtype'ı değiştirmek yerine sadece
            # görüntüleme formatı ayarlanıyor (gereksiz "00:00:00" saatini kaldırmak için).
            "Başlangıç": lambda x: x.strftime("%d.%m.%Y"), "Bitiş": lambda x: x.strftime("%d.%m.%Y"),
        })
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=500)

        st.markdown("---")
        st.markdown("#### 🔗 Sözleşme Detayına Git")
        st.caption("Ödeme geçmişi, döviz/ödeme yöntemi detayı ve makbuz için Sözleşmeler sayfasına gidin.")
        secim_no = st.selectbox(
            "Detayını görüntülemek istediğiniz sözleşmeyi seçin:",
            filtered["sozlesme_no"].tolist(), key="ev_sozlesme_detay_secim",
        )
        if st.button("➡️ Sözleşmeler Sayfasında Aç", key="ev_sozlesme_detay_git"):
            st.session_state["secili_sozlesme_no"] = secim_no
            st.session_state["secili_sozlesme_kategori"] = "EV"
            st.switch_page("pages/sozlesmeler.py")