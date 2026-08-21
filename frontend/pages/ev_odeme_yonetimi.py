import pandas as pd
import streamlit as st

from api_client import api_get, api_post

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()
user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
# Madde 5 düzeltmesi: "Genel Müdür" rolü TEK BAŞINA yeterli sayılmamalı --
# departmanı D3 (şirket geneli) olmayan bir "Genel Müdür" bu sayfaya
# erişememeli. bkz. Home.py'deki is_genel_mudur_d3 ile aynı kural.
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])
is_ev_departmani = any(dep in all_session_values for dep in ["D1"])

if not (is_genel_mudur or is_ev_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()

st.title("🏠 Ev Kiralama Finans & Ödeme Yönetimi")
st.caption("Kira tahsilatı, esnek ödeme planı ve geçmiş finansal hareket takibi.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)

contracts = api_get("/housing/contracts") or []
if not contracts:
    st.warning("Sistemde kayıtlı ev sözleşmesi bulunamadı.")
    st.stop()

df_contracts = pd.DataFrame(contracts)
df_contracts = df_contracts[~df_contracts["sozlesme_durumu"].astype(str).str.upper().str.contains("İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False)]

if df_contracts.empty:
    st.warning("Sistemde kayıtlı aktif ev sözleşmesi bulunamadı.")
    st.stop()

df_contracts["kalan_borc"] = pd.to_numeric(df_contracts["kalan_borc"], errors="coerce").fillna(0).clip(lower=0)
df_contracts = df_contracts[df_contracts["kalan_borc"] > 0].copy()

if df_contracts.empty:
    st.success("🎉 Ödenmemiş borcu olan ev sözleşmesi bulunmuyor. Tüm kira ödemeleri tamamlanmış.")
    st.stop()

contract_no_list = ["--- Seçiniz ---"] + df_contracts["sozlesme_no"].tolist()
contract_label_map = {
    row["sozlesme_no"]: f"#{row['sozlesme_no']} - {row['musteri_adi']} (Borç: ₺{row['kalan_borc']:,.2f}) [{row['sozlesme_durumu']}]"
    for _, row in df_contracts.iterrows()
}
st.caption(f"💳 {len(df_contracts)} adet borçlu sözleşme listeleniyor")
selected = st.selectbox(
    "📌 İşlem Yapılacak Ev Sözleşmesini Seçiniz:", contract_no_list,
    format_func=lambda x: "--- Seçiniz ---" if x == "--- Seçiniz ---" else contract_label_map[x],
    key="ev_odeme_sozlesme_secim",
)

if selected != "--- Seçiniz ---":
    contract_no = selected
    c_row = df_contracts[df_contracts["sozlesme_no"] == contract_no].iloc[0]
    monthly_rent = float(c_row["aylik_kira"]) if c_row["aylik_kira"] else 0.0
    deposit = float(c_row["depozito"]) if c_row["depozito"] else 0.0
    total_amount = float(c_row["toplam_tutar"]) if c_row["toplam_tutar"] else 0.0
    remaining_debt = float(c_row["kalan_borc"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Toplam Sözleşme Tutarı", f"₺{total_amount:,.2f}")
    m2.metric("Aylık Kira Bedeli", f"₺{monthly_rent:,.2f}")
    m3.metric("Depozito Tutarı", f"₺{deposit:,.2f}")
    m4.metric("Kalan Borç Tutarı", f"₺{remaining_debt:,.2f}")

    installments = api_get(f"/housing/contracts/{contract_no}/installments") or []

    if installments:
        # ============ MADDE 8: TAKSİT PLANI ============
        st.markdown("---")
        st.subheader("📅 Esnek Ödeme Planı (Taksit Takvimi)")

        df_plan = pd.DataFrame(installments)
        df_show_plan = df_plan[["taksit_no", "planlanan_tarih", "planlanan_tutar", "odenen_tutar", "durum"]].rename(columns={
            "taksit_no": "Taksit No", "planlanan_tarih": "Vade Tarihi", "planlanan_tutar": "Planlanan Tutar (₺)",
            "odenen_tutar": "Ödenen Tutar (₺)", "durum": "Durum",
        })
        st.dataframe(df_show_plan, use_container_width=True, hide_index=True)

        unpaid = df_plan[df_plan["durum"].astype(str).str.upper() != "ÖDENDİ"]
        if unpaid.empty:
            st.success("🎉 Bu sözleşmeye ait tüm taksitler ödenmiştir!")
        else:
            st.markdown("#### 💳 Taksit Ödemesi Yap")
            taksit_options = ["--- Seçiniz ---"] + [
                f"{r['taksit_no']}. Taksit - Vade: {r['planlanan_tarih']} - Kalan: ₺{(float(r['planlanan_tutar']) - float(r['odenen_tutar'] or 0)):,.2f} [{r['durum']}]"
                for _, r in unpaid.iterrows()
            ]
            taksit_map = {
                f"{r['taksit_no']}. Taksit - Vade: {r['planlanan_tarih']} - Kalan: ₺{(float(r['planlanan_tutar']) - float(r['odenen_tutar'] or 0)):,.2f} [{r['durum']}]": r
                for _, r in unpaid.iterrows()
            }
            selected_taksit_label = st.selectbox("Ödeme Yapılacak Taksiti Seçiniz:", taksit_options, key="ev_taksit_secim")

            if selected_taksit_label != "--- Seçiniz ---":
                taksit_row = taksit_map[selected_taksit_label]
                taksit_kalan = round(float(taksit_row["planlanan_tutar"]) - float(taksit_row["odenen_tutar"] or 0), 2)

                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    t_doviz = st.selectbox("Döviz Cinsi:", ["TRY", "USD", "EUR", "GBP"], key="ev_taksit_doviz")
                    t_yontem = st.selectbox("Ödeme Yöntemi:", ["NAKİT", "KREDİ_KARTI", "EFT_HAVALE"], key="ev_taksit_yontem")

                    if t_doviz != "TRY":
                        rate_info = api_get("/finance/exchange-rate", {"currency": t_doviz}) or {}
                        satis_kuru = rate_info.get("satis")
                        if satis_kuru:
                            st.caption(f"💱 Bugünün TCMB döviz satış kuru: 1 {t_doviz} ≈ ₺{satis_kuru:,.4f}")
                            t_max = round(taksit_kalan / satis_kuru, 2)
                        else:
                            st.warning("⚠️ Güncel kur alınamadı -- lütfen dikkatli girin.")
                            t_max = None
                        t_amount = st.number_input(
                            f"Ödenecek Tutar ({t_doviz}):", min_value=0.01, max_value=t_max, value=t_max or 1.0, step=1.0, key="ev_taksit_tutar_odeme",
                        )
                    else:
                        t_amount = st.number_input(
                            "Ödenecek Tutar (₺):", min_value=1.0, max_value=float(taksit_kalan), value=float(taksit_kalan), step=100.0, key="ev_taksit_tutar_odeme_try",
                        )
                        st.caption(
                            "ℹ️ Tutarı düşürerek **parçalı ödeme** yapabilirsiniz -- kalan tutar için "
                            "sistem otomatik olarak yeni bir taksit oluşturacaktır."
                        )
                with col_t2:
                    t_note = st.text_input("Ödeme Açıklaması / Not:", value=f"{taksit_row['taksit_no']}. taksit ödemesi", key="ev_taksit_not")

                t_tam_kapat = st.checkbox(
                    "💯 Bu ödemeyle taksidi tamamen kapat (döviz çeviriminden kaynaklanan küçük kuruş farklarını otomatik sıfırla)",
                    value=True, key="ev_taksit_tam_kapat",
                )

                if st.button("✅ Taksit Ödemesini Kaydet", type="primary", key="ev_taksit_odeme_btn"):
                    result = api_post(
                        f"/housing/contracts/{contract_no}/installments/{int(taksit_row['id'])}/pay",
                        {
                            "customer_id": int(c_row["musteri_id"]), "amount_paid": t_amount,
                            "doviz_cinsi": t_doviz, "odeme_yontemi": t_yontem, "description": t_note,
                            "tam_kapat": t_tam_kapat,
                        },
                    )
                    if result and result.get("success"):
                        st.session_state["toast_mesaj"] = result["message"]
                        st.session_state["toast_icon"] = "💳"
                        st.rerun()
                    elif result:
                        st.error(result["message"])
        st.markdown("---")

    st.markdown("---")
    st.subheader("💳 Ödeme Tahsilatı Yap" if not installments else "💳 Serbest / Ek Ödeme Tahsilatı Yap (Taksit Planı Dışında)")

    if remaining_debt <= 0:
        st.success("🎉 Bu sözleşmeye ait tüm kira borçları ödenmiştir!")
    else:
        col1, col2 = st.columns(2)
        with col1:
            payment_mode = st.radio("Ödeme Şekli:", ["Aylık Tek Taksit", "Esnek / Toplu Ay Ödemesi"])
            doviz_cinsi = st.selectbox("Döviz Cinsi:", ["TRY", "USD", "EUR", "GBP"], key="ev_odeme_doviz")
            odeme_yontemi = st.selectbox(
                "Ödeme Yöntemi:", ["NAKİT", "KREDİ_KARTI", "EFT_HAVALE"], key="ev_odeme_yontemi"
            )

            if doviz_cinsi != "TRY":
                rate_info = api_get("/finance/exchange-rate", {"currency": doviz_cinsi}) or {}
                satis_kuru = rate_info.get("satis")
                if satis_kuru:
                    st.caption(
                        f"💱 Bugünün TCMB döviz satış kuru: 1 {doviz_cinsi} ≈ ₺{satis_kuru:,.4f} "
                        "(ödeme kaydedilirken kesin kur yeniden hesaplanır)"
                    )
                    max_in_currency = round(remaining_debt / satis_kuru, 2)
                else:
                    st.warning("⚠️ Güncel kur alınamadı, tutar sınırlaması yapılamıyor -- lütfen dikkatli girin.")
                    max_in_currency = None
                payment_amount = st.number_input(
                    f"Tahsil Edilecek Tutar ({doviz_cinsi}):", min_value=0.01,
                    max_value=max_in_currency, value=max_in_currency or 1.0, step=1.0,
                )
            elif payment_mode == "Aylık Tek Taksit":
                default_payment = min(monthly_rent, remaining_debt)
                payment_amount = st.number_input("Tahsil Edilecek Tutar (₺):", value=float(default_payment), min_value=1.0, max_value=float(remaining_debt), step=500.0)
            else:
                payment_amount = st.number_input("Esnek Tahsil Edilecek Tutar (₺):", value=float(min(monthly_rent * 2, remaining_debt)), min_value=1.0, max_value=float(remaining_debt), step=500.0)

            payment_type = "KİRA_TAHSİLATI" if payment_mode == "Aylık Tek Taksit" else "TOPLU_KİRA_TAHSİLATI"
        with col2:
            payment_note = st.text_input("Ödeme Açıklaması / Not:", value=f"{payment_mode} Tahsilatı")

        tam_kapat = st.checkbox(
            "💯 Bu ödemeyle borcu tamamen kapat (döviz çeviriminden kaynaklanan küçük kuruş farklarını otomatik sıfırla)",
            value=True, key="ev_odeme_tam_kapat",
        )

        if st.button("✅ Ödemeyi Kaydet ve Tahsil Et", type="primary"):
            result = api_post(
                "/finance/payments",
                {
                    "contract_no": contract_no, "category": "EV", "customer_id": int(c_row["musteri_id"]),
                    "amount_paid": payment_amount, "payment_type": payment_type, "description": payment_note,
                    "doviz_cinsi": doviz_cinsi, "odeme_yontemi": odeme_yontemi, "tam_kapat": tam_kapat,
                },
            )
            if result and result.get("success"):
                st.session_state["ev_odeme_basarili"] = {"contract_no": contract_no, "tutar": payment_amount}
            elif result:
                st.error(result["message"])

        # Ödeme başarılıysa: rerun ile kaybolmadan kalıcı olarak göster
        pending = st.session_state.get("ev_odeme_basarili")
        if pending and pending["contract_no"] == contract_no:
            st.success(f"✅ ₺{pending['tutar']:,.2f} tutarındaki ödeme başarıyla kaydedildi!")
            if st.button("🔄 Sayfayı Yenile (Güncel Bakiyeyi Göster)", key="ev_yenile_btn"):
                del st.session_state["ev_odeme_basarili"]
                st.rerun()

    st.markdown("---")
    st.subheader("📋 Geçmiş Ödeme Kayıtları")
    history = api_get(f"/finance/payments/{contract_no}") or []
    if history:
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
    else:
        st.info("Bu sözleşmeye ait henüz yapılmış bir kira ödemesi bulunmuyor.")

    st.markdown("---")
    if remaining_debt > 0:
        if st.button("📲 Vade Hatırlatma SMS'i Gönder", type="secondary"):
            sms_text = f"Sayın {c_row['musteri_adi']}, #{contract_no} numaralı ev sözleşmenize ait kalan ₺{remaining_debt:,.2f} borcunuz bulunmaktadır."
            api_post("/finance/notify-sms", {"phone_number": "+90 (555) 123 4567", "message": sms_text, "notification_type": "KİRA HATIRLATMA SMS"})
            st.success(f"✅ {c_row['musteri_adi']} isimli kiracıya ödeme hatırlatma SMS'i gönderildi!")