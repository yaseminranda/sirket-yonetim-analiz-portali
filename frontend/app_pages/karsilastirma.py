"""Streamlit page showing cross-department (vehicle vs housing) comparison analytics for the General Manager."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from api_client import api_get_cached as api_get

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])

if not is_genel_mudur:
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.info(
        "💡 Departmanlar arası karşılaştırma analizini sadece **Genel Müdür** yetkisine sahip kullanıcılar görüntüleyebilir."
    )
    st.stop()

st.title("📊 Departmanlar Arası Genel Karşılaştırma")
st.caption("Araç Kiralama (D2) ve Ev/Emlak Kiralama (D1) Departmanlarının Operasyonel Performans Analizi")
st.divider()

data = api_get("/comparison/summary")
if data is None:
    st.stop()

st.subheader("📈 Genel Finansal Özet")
col1, col2, col3, col4 = st.columns(4)

total_revenue = data["vehicle"]["revenue"] + data["housing"]["revenue"]
total_salary = data["vehicle"]["salary"] + data["housing"]["salary"]
total_net_profit = data["vehicle"]["net_profit"] + data["housing"]["net_profit"]
total_active = data["vehicle"]["active_contracts"] + data["housing"]["active_contracts"]

col1.metric("Toplam Şirket Cirosu", f"₺{total_revenue:,.2f}")
col2.metric("Toplam Maaş Gideri", f"₺{total_salary:,.2f}")
col3.metric("Toplam Net Kâr", f"₺{total_net_profit:,.2f}")
col4.metric("Toplam Aktif Sözleşme", f"{total_active} Adet")

st.divider()
st.subheader("🏢 Departman Bazlı Karşılaştırma")

col_v, col_h = st.columns(2)
with col_v:
    st.markdown("##### 🚗 Araç Kiralama (D2)")
    st.metric("Ciro", f"₺{data['vehicle']['revenue']:,.2f}")
    st.metric("Net Kâr", f"₺{data['vehicle']['net_profit']:,.2f}")
    st.metric("Toplam / Aktif Sözleşme", f"{data['vehicle']['total_contracts']} / {data['vehicle']['active_contracts']}")
    st.metric("Çalışan Sayısı", f"{data['vehicle']['employee_count']} Kişi")
with col_h:
    st.markdown("##### 🏠 Ev Kiralama (D1)")
    st.metric("Ciro", f"₺{data['housing']['revenue']:,.2f}")
    st.metric("Net Kâr", f"₺{data['housing']['net_profit']:,.2f}")
    st.metric("Toplam / Aktif Sözleşme", f"{data['housing']['total_contracts']} / {data['housing']['active_contracts']}")
    st.metric("Çalışan Sayısı", f"{data['housing']['employee_count']} Kişi")

st.markdown("###### 📈 Aylık Ciro / Net Kâr Trendi (Tüm Zamanlar)")

trend = api_get("/comparison/monthly-trend") or {"vehicle": [], "housing": []}
df_v_trend = pd.DataFrame(trend["vehicle"]).rename(columns={"ciro": "Araç Ciro", "net_kar": "Araç Net Kâr"})
df_h_trend = pd.DataFrame(trend["housing"]).rename(columns={"ciro": "Ev Ciro", "net_kar": "Ev Net Kâr"})

if df_v_trend.empty and df_h_trend.empty:
    st.info("Henüz aylık trend grafiği için yeterli sözleşme verisi bulunmuyor.")
else:
    trend_df = pd.merge(df_v_trend, df_h_trend, on="ay", how="outer").sort_values("ay").fillna(0).reset_index(drop=True)
    trend_df["ay_dt"] = pd.to_datetime(trend_df["ay"], format="%Y-%m")
    METRIC_COLORS = {
        "Araç Ciro": "#2B6CB0", "Araç Net Kâr": "#63B3ED", "Ev Ciro": "#319795", "Ev Net Kâr": "#81E6D9",
    }

    def _build_trend_figure(df: pd.DataFrame) -> go.Figure:
        """Build a line chart figure of revenue/net-profit metrics over months from the given dataframe."""
        fig = go.Figure()
        for metric, color in METRIC_COLORS.items():
            if metric in df.columns:
                fig.add_trace(go.Scatter(
                    x=df["ay"], y=df[metric], mode="lines+markers", name=metric,
                    line=dict(color=color, width=2), marker=dict(size=5),
                ))
        fig.update_layout(
            height=380, margin=dict(l=10, r=10, t=30, b=20), yaxis_title="Tutar (₺)", xaxis_title="Ay",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        return fig

    cumulative_df_full = trend_df.copy()
    for metric in METRIC_COLORS:
        if metric in cumulative_df_full.columns:
            cumulative_df_full[metric] = cumulative_df_full[metric].cumsum()

    min_ay = trend_df["ay_dt"].min().date()
    max_ay = trend_df["ay_dt"].max().date()

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        trend_start = st.date_input(
            "Başlangıç Ayı", value=min_ay, min_value=min_ay, max_value=max_ay, key="trend_baslangic_tarihi"
        )
    with dcol2:
        trend_end = st.date_input(
            "Bitiş Ayı", value=max_ay, min_value=min_ay, max_value=max_ay, key="trend_bitis_tarihi"
        )

    if trend_start > trend_end:
        st.warning("⚠️ Başlangıç ayı, bitiş ayından sonra olamaz.")
    else:
        start_period = pd.Timestamp(trend_start).to_period("M")
        end_period = pd.Timestamp(trend_end).to_period("M")
        range_mask = (trend_df["ay_dt"].dt.to_period("M") >= start_period) & (
            trend_df["ay_dt"].dt.to_period("M") <= end_period
        )
        filtered_monthly_df = trend_df[range_mask].reset_index(drop=True)
        filtered_cumulative_df = cumulative_df_full[range_mask].reset_index(drop=True)

        if filtered_monthly_df.empty:
            st.info("Seçilen tarih aralığında veri bulunmuyor.")
        else:
            col_trend1, col_trend2 = st.columns(2)
            with col_trend1:
                st.markdown("**Aylık (Gerçekleşen)**")
                fig_monthly = _build_trend_figure(filtered_monthly_df)
                st.plotly_chart(fig_monthly, use_container_width=True, config={"responsive": True})
            with col_trend2:
                st.markdown("**Kümülatif (Birikimli Toplam)**")
                fig_cumulative = _build_trend_figure(filtered_cumulative_df)
                st.plotly_chart(fig_cumulative, use_container_width=True, config={"responsive": True})

st.markdown("---")
st.subheader("👑 En Çok Sözleşmesi Bulunan VIP Müşteri Analizleri")

top = api_get("/comparison/top-customers") or {"vehicle": [], "housing": [], "cross": []}

m_col1, m_col2 = st.columns(2)
with m_col1:
    st.markdown("##### 🚗 Araç Kiralama (D2) Top 5 Müşteri")
    df_v_m = pd.DataFrame(top["vehicle"])
    if not df_v_m.empty:
        fig = go.Figure(data=[go.Bar(x=df_v_m["customer"], y=df_v_m["total_contracts"], marker_color="#2B6CB0", text=df_v_m["total_contracts"].astype(str) + " Adet", textposition="outside")])
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=20), yaxis_title="Sözleşme Sayısı", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    else:
        st.info("Araç Kiralama için kaydedilmiş müşteri verisi bulunamadı.")

with m_col2:
    st.markdown("##### 🏠 Ev Kiralama (D1) Top 5 Müşteri")
    df_h_m = pd.DataFrame(top["housing"])
    if not df_h_m.empty:
        fig = go.Figure(data=[go.Bar(x=df_h_m["customer"], y=df_h_m["total_contracts"], marker_color="#319795", text=df_h_m["total_contracts"].astype(str) + " Adet", textposition="outside")])
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=20), yaxis_title="Sözleşme Sayısı", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    else:
        st.info("Ev Kiralama için kaydedilmiş müşteri verisi bulunamadı.")

st.markdown("##### 🌟 Her İki Departmanda da Sözleşmesi Bulunan (VIP Çapraz) Top 5 Müşteri")
df_c_m = pd.DataFrame(top["cross"])
if not df_c_m.empty:
    fig_cross = go.Figure()
    fig_cross.add_trace(go.Bar(name="Araç Kiralama (D2)", x=df_c_m["customer"], y=df_c_m["vehicle_contracts"], marker_color="#2B6CB0"))
    fig_cross.add_trace(go.Bar(name="Ev Kiralama (D1)", x=df_c_m["customer"], y=df_c_m["housing_contracts"], marker_color="#319795"))
    fig_cross.update_layout(
        barmode="stack", height=380, margin=dict(l=10, r=10, t=30, b=20), yaxis_title="Toplam Sözleşme Sayısı",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_cross, use_container_width=True, config={"responsive": True})
else:
    st.info("💡 Hem Araç hem de Ev kiralama departmanından sözleşmesi olan ortak bir çapraz müşteri bulunamadı.")

st.markdown("---")
st.subheader("🛡️ Genel Müdür Giriş ve Güvenlik Logları")

gm_logs = api_get("/comparison/gm-logs") or []
if gm_logs:
    st.dataframe(pd.DataFrame(gm_logs), use_container_width=True, hide_index=True)
else:
    st.info("Henüz Genel Müdür seviyesinde kaydedilmiş bir giriş veya güvenlik hareketi bulunmuyor.")