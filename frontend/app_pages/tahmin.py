"""Streamlit page for AI-based revenue/demand forecasting, cancellation risk, and investment recommendations."""
import plotly.express as px
import streamlit as st
import pandas as pd

from api_client import api_get_cached as api_get, api_post

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])

if not is_genel_mudur:
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.info(
        "💡 Yapay zekâ tahmin panelini sadece **Genel Müdür** yetkisine sahip kullanıcılar görüntüleyebilir."
    )
    st.stop()

st.title("🔮 Yapay Zekâ Tahmin & Yatırım Karar Destek Paneli")
st.divider()

tab1, tab2, tab3 = st.tabs(["📈 Gelecek Dönem Ciro & Talep Tahmini", "🚨 Sözleşme İptal Risk Analizi", "💡 Kar, Doluluk & Yatırım Tavsiyeleri"])

with tab1:
    future_months = st.slider("Tahmin Edilecek Dönem Süresi (Ay):", min_value=1, max_value=12, value=6)
    st.subheader(f"Gelecek {future_months} Ay İçin Ciro ve Kiralama Sayısı Tahmini")

    with st.spinner("Makine Öğrenmesi Modeli Eğitiliyor ve Tahminler Üretiliyor..."):
        forecast = api_get("/forecast/revenue-demand", {"months": future_months})

    if forecast is None:
        pass
    elif not forecast.get("available"):
        st.warning(f"⚠️ {forecast.get('message', 'Yeterli geçmiş veri bulunamadı.')}")
    else:
        df_pred = pd.DataFrame(forecast["predictions"])
        if not df_pred.empty:
            total_est_revenue = df_pred["Tahmini Ciro (₺)"].sum()
            total_est_demand = df_pred["Tahmini Kiralama Adedi"].sum()

            m1, m2 = st.columns(2)
            m1.metric(f"Önümüzdeki {future_months} Ayın Toplam Tahmini Cirosu", f"₺{total_est_revenue:,.2f}")
            m2.metric(f"Önümüzdeki {future_months} Ayın Beklenen Kiralama Adedi", f"{total_est_demand} Adet")

            st.markdown("---")
            fig_revenue = px.line(df_pred, x="Tarih", y="Tahmini Ciro (₺)", color="Kategori", markers=True, title="Aylık Bazda Beklenen Tahmini Ciro Trendi (₺)")
            fig_revenue.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_revenue, use_container_width=True, config={"responsive": True})

            st.markdown("##### Detaylı Aylık Tahmin Tablosu")
            st.dataframe(df_pred, use_container_width=True, hide_index=True)

with tab2:
    st.subheader("Oluşturulacak / Aktif Sözleşmenin İptal Edilme Riski Tahmini")

    col1, col2, col3 = st.columns(3)
    with col1:
        category_input = st.selectbox("Kategori / Hizmet Türü:", ["ARAC", "EV"])
    with col2:
        duration_input = st.number_input("Kiralama Süresi (Gün/Ay):", min_value=1, value=5)
    with col3:
        price_input = st.number_input("Toplam Sözleşme Tutarı (₺):", min_value=100.0, value=5000.0, step=500.0)

    if st.button("🚨 İptal Riski Hesapla", type="primary"):
        with st.spinner("Sınıflandırma Modeli Analiz Ediyor..."):
            result = api_post("/forecast/cancellation-risk", {"category": category_input, "duration": duration_input, "price": price_input})

        if result:
            risk_score = result["risk_percentage"]
            st.markdown("---")
            st.markdown(f"### Hesaplanan İptal Riski: **%{risk_score}**")

            if risk_score >= 50.0:
                st.error("⚠️ **Yüksek İptal Riski!** Müşteriden kapora alınması veya şartların gözden geçirilmesi önerilir.")
            elif risk_score >= 20.0:
                st.warning("⚡ **Orta Derece İptal Riski.** Standart prosedürlerin uygulanması uygundur.")
            else:
                st.success("✅ **Düşük İptal Riski.** Sözleşmenin sorunsuz tamamlanma ihtimali son derece yüksek.")

with tab3:
    st.subheader("🏢 Kapasite Yeterlilik Testi ve Satın Alma Tavsiye Şeması")

    analysis_res = api_get("/forecast/capacity-investment")
    if analysis_res:
        col1, col2 = st.columns(2)
        with col1:
            st.info("### 🚗 Araç Filosu Doluluk Durumu")
            st.metric("Araç Doluluk Oranı", f"%{analysis_res['arac_doluluk']}", delta=f"{analysis_res['aktif_arac']} / {analysis_res['toplam_arac']} Kirada")
        with col2:
            st.success("### 🏠 Konut Portföyü Doluluk Durumu")
            st.metric("Ev Doluluk Oranı", f"%{analysis_res['ev_doluluk']}", delta=f"{analysis_res['aktif_ev']} / {analysis_res['toplam_ev']} Kirada")

        st.markdown("---")
        st.markdown("### 💡 Yapay Zekâ Satın Alma & Yatırım Tavsiyeleri")
        for rec in analysis_res["oneriler"]:
            with st.expander(f"{rec['Kategori']} - {rec['Durum']}", expanded=True):
                st.write(f"**Güncel Doluluk:** {rec['Doluluk Oranı']}")
                st.write(f"**Aciliyet Durumu:** `{rec['Aciliyet']}`")
                st.markdown(f"**Yatırım Tavsiyesi:** {rec['Tavsiye']}")
