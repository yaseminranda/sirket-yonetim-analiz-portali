"""Streamlit page for tracking and collecting outstanding payments on vehicle rental contracts."""

import pandas as pd
import streamlit as st

from api_client import api_get, api_get_raw, api_post

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()
user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])
is_arac_departmani = any(dep in all_session_values for dep in ["D2"])

if not (is_genel_mudur or is_arac_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()

st.title("🚗 Araba Kiralama Ödeme Yönetimi")
st.divider()

contracts = api_get("/vehicles/contracts") or []
if not contracts:
    st.info("Kayıtlı aktif araç sözleşmesi bulunamadı.")
    st.stop()

df_vehicle = pd.DataFrame(contracts)
df_vehicle = df_vehicle[~df_vehicle["sozlesme_durumu"].astype(str).str.upper().str.contains("İPTAL|IPTAL", na=False)]

if df_vehicle.empty:
    st.info("Kayıtlı aktif araç sözleşmesi bulunamadı.")
    st.stop()

df_vehicle["kalan_borc"] = pd.to_numeric(df_vehicle["kalan_borc"], errors="coerce").fillna(0).clip(lower=0)
df_vehicle = df_vehicle[df_vehicle["kalan_borc"] > 0].copy()

if df_vehicle.empty:
    st.success("🎉 Ödenmemiş borcu olan araç sözleşmesi bulunmuyor. Tüm ödemeler tamamlanmış.")
    st.stop()

search_term = st.text_input("🔍 Sözleşme No veya Müşteri Adına Göre Ara:", key="arac_odeme_arama").strip().lower()
df_search = df_vehicle
if search_term:
    df_search = df_vehicle[
        df_vehicle["sozlesme_no"].astype(str).str.lower().str.contains(search_term, na=False)
        | df_vehicle["musteri_adi"].astype(str).str.lower().str.contains(search_term, na=False)
    ]
    if df_search.empty:
        st.warning("Aramanızla eşleşen sözleşme bulunamadı.")

contract_no_list = ["--- Seçiniz ---"] + df_search["sozlesme_no"].tolist()
contract_label_map = {
    row["sozlesme_no"]: f"#{row['sozlesme_no']} - {row['musteri_adi']} (Borç: ₺{row['kalan_borc']:,.2f})"
    for _, row in df_vehicle.iterrows()
}
st.caption(f"💳 {len(df_search)} adet borçlu sözleşme listeleniyor")
selected_option = st.selectbox(
    "📌 Araç Sözleşmesi Seçiniz:", contract_no_list,
    format_func=lambda x: "--- Seçiniz ---" if x == "--- Seçiniz ---" else contract_label_map[x],
    key="arac_odeme_sozlesme_secim",
)

if selected_option != "--- Seçiniz ---":
    contract_no = selected_option
    selected_row = df_vehicle[df_vehicle["sozlesme_no"] == contract_no].iloc[0]
    remaining_balance = float(selected_row["kalan_borc"])
    min_down_payment = float(selected_row["toplam_tutar"]) * 0.50

    col1, col2, col3 = st.columns(3)
    col1.metric("Toplam Araç Kira Bedeli", f"₺{float(selected_row['toplam_tutar']):,.2f}")
    col2.metric("Alınması Gereken Ön Ödeme (%50)", f"₺{min_down_payment:,.2f}")
    col3.metric("Kalan Borç", f"₺{remaining_balance:,.2f}")

    st.markdown("---")

    if remaining_balance > 0:
        st.subheader("💳 Kalan Ödeme Tahsilatı Yap")

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            doviz_cinsi = st.selectbox("Döviz Cinsi:", ["TRY", "USD", "EUR", "GBP"], key="arac_odeme_doviz")
        with col_d2:
            odeme_yontemi = st.selectbox(
                "Ödeme Yöntemi:", ["NAKİT", "KREDİ_KARTI", "EFT_HAVALE"], key="arac_odeme_yontemi"
            )

        if doviz_cinsi != "TRY":
            rate_info = api_get("/finance/exchange-rate", {"currency": doviz_cinsi}) or {}
            satis_kuru = rate_info.get("satis")
            if satis_kuru:
                st.caption(
                    f"💱 Bugünün TCMB döviz satış kuru: 1 {doviz_cinsi} ≈ ₺{satis_kuru:,.4f} "
                    "(ödeme kaydedilirken kesin kur yeniden hesaplanır)"
                )
                max_in_currency = round(remaining_balance / satis_kuru, 2)
            else:
                st.warning("⚠️ Güncel kur alınamadı, tutar sınırlaması yapılamıyor -- lütfen dikkatli girin.")
                max_in_currency = None
            payment_input = st.number_input(
                f"Tahsil Edilecek Tutar ({doviz_cinsi}):", min_value=0.01,
                max_value=max_in_currency, value=max_in_currency or 1.0, step=1.0,
            )
        else:
            payment_input = st.number_input(
                "Tahsil Edilecek Tutar (₺):", min_value=1.0, max_value=remaining_balance, value=remaining_balance, step=100.0
            )

        tam_kapat = st.checkbox(
            "💯 Bu ödemeyle borcu tamamen kapat (döviz çeviriminden kaynaklanan küçük kuruş farklarını otomatik sıfırla)",
            value=True, key="arac_odeme_tam_kapat",
        )

        if st.button("✅ Araç Ödemesini Kaydet", type="primary"):
            result = api_post(
                "/finance/payments",
                {
                    "contract_no": contract_no, "category": "ARAC", "customer_id": int(selected_row["musteri_id"]),
                    "amount_paid": payment_input, "payment_type": "ARAÇ_KİRA_ÖDEMESİ",
                    "description": "Araç kiralama kalan borç tahsilatı",
                    "doviz_cinsi": doviz_cinsi, "odeme_yontemi": odeme_yontemi, "tam_kapat": tam_kapat,
                },
            )
            if result and result.get("success"):
                invoice_bytes = api_get_raw(
                    f"/finance/invoice/{contract_no}",
                    {
                        "customer_name": selected_row["musteri_adi"], "amount": payment_input,
                        "transaction_type": "Araç Kiralama Tahsilatı", "remaining_balance": remaining_balance - payment_input,
                    },
                )
                st.session_state["arac_odeme_basarili"] = {
                    "contract_no": contract_no, "invoice_bytes": invoice_bytes,
                }
            elif result:
                st.error(result["message"])

        pending = st.session_state.get("arac_odeme_basarili")
        if pending and pending["contract_no"] == contract_no:
            st.success("✅ Ödeme başarıyla kaydedildi! Faturanızı indirebilirsiniz.")

            @st.fragment
            def _arac_fatura_indirme_fragmani():
                if pending["invoice_bytes"]:
                    st.download_button(
                        "📄 Makbuz / Fatura İndir (Excel)", data=pending["invoice_bytes"], file_name=f"Fatura_Arac_{contract_no}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_fatura_arac",
                    )

            _arac_fatura_indirme_fragmani()
            if st.button("📋 Sözleşme Detaylarına / Ödeme Geçmişine Git"):
                del st.session_state["arac_odeme_basarili"]
                st.session_state["secili_sozlesme_no"] = contract_no
                st.session_state["secili_sozlesme_kategori"] = "ARAC"
                st.switch_page("app_pages/sozlesmeler.py")
    else:
        st.success("🎉 Bu sözleşmenin tüm ödemeleri tamamlanmıştır.")

    st.markdown("---")
    st.subheader("📋 Geçmiş Ödeme Kayıtları")
    history = api_get(f"/finance/payments/{contract_no}") or []
    if history:
        df_history = pd.DataFrame(history)
        st.dataframe(df_history, use_container_width=True, hide_index=True)

        import io
        buffer_history = io.BytesIO()
        with pd.ExcelWriter(buffer_history, engine="openpyxl") as writer:
            df_history.to_excel(writer, index=False, sheet_name="Odeme_Gecmisi")
        buffer_history.seek(0)

        @st.fragment
        def _arac_odeme_gecmisi_indirme_fragmani():
            st.download_button(
                "📥 Tüm Ödeme Geçmişini İndir (Excel)", data=buffer_history, file_name=f"Odeme_Gecmisi_{contract_no}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        _arac_odeme_gecmisi_indirme_fragmani()
    else:
        st.info("Bu sözleşmeye ait henüz yapılmış bir ödeme bulunmuyor.")