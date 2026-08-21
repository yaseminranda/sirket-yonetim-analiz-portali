"""Streamlit page for housing (ev) department rental analytics and dashboards."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from api_client import api_get_cached as api_get

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"]) and (user_dept in ["D3", "3"])
is_ev_dep_muduru = any(role in user_role for role in ["DEPARTMAN MÜDÜRÜ", "DEPARTMAN MUDURU"]) and (user_dept in ["D1", "1"])

if not (is_genel_mudur or is_ev_dep_muduru):
    st.error("🚫 Bu sayfaya erişim yetkiniz bulunmamaktadır! (Sadece Genel Müdür ve Ev Kiralama Departmanı Müdürü girebilir)")
    st.stop()


def format_currency(amount, symbol="₺"):
    """Format a numeric amount as a compact currency string, abbreviating with K/M suffixes."""
    if pd.isna(amount) or amount == 0:
        return f"0 {symbol}"
    abs_val = abs(amount)
    if abs_val >= 1_000_000:
        return f"{amount / 1_000_000:,.2f} M {symbol}"
    elif abs_val >= 1_000:
        return f"{amount / 1_000:,.1f} K {symbol}"
    return f"{amount:,.2f} {symbol}"


analysis = api_get("/housing/analysis")
if analysis is None:
    st.stop()

st.title("🏠 Daire / Konut Kiralama Analizleri")
st.caption("Konut Kiralama Performansı, Doluluk Oranları ve Operasyonel Gelir Analizi")

df = pd.DataFrame(analysis["rows"])
if df.empty:
    st.warning("Veritabanında analiz edilecek sözleşme verisi bulunamadı.")
    st.stop()

df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce").dt.normalize()
df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce").dt.normalize()
df["year"] = df["start_date"].dt.year
today = pd.to_datetime("today").normalize()

deleted_mask = df["status"].astype(str).str.upper().str.contains("SİLİNDİ|SILINDI", na=False)
df_valid = df[~deleted_mask].copy()

st.markdown("### 🔍 Filtreleme Paneli")
years_raw = df_valid["year"].dropna().unique()
selected_years = []
if len(years_raw) > 0:
    available_years = sorted([int(y) for y in years_raw], reverse=True)
    selected_years = st.multiselect("📅 İncelemek İstediğiniz Yılları Seçin:", options=available_years, default=[], placeholder="Tüm Yıllar")
    df_filtered = df_valid[df_valid["year"].isin(selected_years)].copy() if selected_years else df_valid.copy()
else:
    df_filtered = df_valid.copy()

st.divider()

total_revenue = df_filtered[df_filtered["start_date"] <= today]["total_revenue"].sum()
total_salary_expense = df_filtered.groupby("employee_id")["employee_salary"].first().sum()
total_net_profit = total_revenue - total_salary_expense

if selected_years:
    max_selected_year = max(selected_years)
    as_of_date = date.today() if max_selected_year >= date.today().year else date(max_selected_year, 12, 31)
else:
    as_of_date = date.today()
units_count_info = api_get("/housing/units-count", {"as_of_date": str(as_of_date)}) or {}
apartment_count = units_count_info.get("sayi", analysis["total_apartments"])
occupied_apartment_count = units_count_info.get("dolu", 0)

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    "🏠 Portföydeki Daire Sayısı", f"{apartment_count} Adet",
    delta=f"{occupied_apartment_count} Dolu" if apartment_count else None, delta_color="off",
)
col2.metric("👥 Departman Çalışanı", f"{analysis['total_employees']} Kişi")
col3.metric("💰 Gerçekleşen Ciro", format_currency(total_revenue))
col4.metric("📈 Operasyonel Net Kâr", format_currency(total_net_profit))

st.divider()
st.markdown("### 📈 Günlük Gelir Dağılımı & Aktiflik Oranı")

col_dr1, col_dr2 = st.columns(2)
with col_dr1:
    dr_start = st.date_input("Başlangıç Tarihi:", value=date.today() - timedelta(days=30), key="ev_gunluk_baslangic")
with col_dr2:
    dr_end = st.date_input("Bitiş Tarihi:", value=date.today() + timedelta(days=15), key="ev_gunluk_bitis")

if dr_start > dr_end:
    st.error("⚠️ Başlangıç tarihi bitiş tarihinden sonra olamaz.")
else:
    daily_data = api_get("/housing/daily-revenue", {"start_date": str(dr_start), "end_date": str(dr_end)}) or {"rows": []}
    daily_rows = daily_data["rows"]
    if not daily_rows:
        st.info("Seçilen tarih aralığında veri bulunamadı.")
    else:
        df_daily = pd.DataFrame(daily_rows)
        df_daily["tarih_dt"] = pd.to_datetime(df_daily["tarih"])

        past_df = df_daily[~df_daily["gelecek_mi"]]
        future_df = df_daily[df_daily["gelecek_mi"]]
        if not past_df.empty and not future_df.empty:
            future_df = pd.concat([past_df.tail(1), future_df], ignore_index=True)

        fig_daily = go.Figure()
        if not past_df.empty:
            fig_daily.add_trace(go.Scatter(
                x=past_df["tarih_dt"], y=past_df["gunluk_gelir"], name="Günlük Gelir (Gerçekleşen)",
                mode="lines+markers", line=dict(color="#319795", width=2), marker=dict(size=5, color="#319795"),
                fill="tozeroy", fillcolor="rgba(49,151,149,0.15)",
                customdata=past_df[["dolu_sayisi", "toplam_filo", "aktiflik_orani"]].values,
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Günlük Gelir: ₺%{y:,.2f}<br>Dolu Daire: %{customdata[0]} / %{customdata[1]}<br>Aktiflik Oranı: %{customdata[2]:.1f}%<extra></extra>",
            ))
        if not future_df.empty:
            fig_daily.add_trace(go.Scatter(
                x=future_df["tarih_dt"], y=future_df["gunluk_gelir"], name="Günlük Gelir (Planlanan)",
                mode="lines+markers", line=dict(color="#319795", width=2, dash="dash"), marker=dict(size=5, color="#319795"),
                customdata=future_df[["dolu_sayisi", "toplam_filo", "aktiflik_orani"]].values,
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Günlük Gelir (Planlanan): ₺%{y:,.2f}<br>Dolu Daire: %{customdata[0]} / %{customdata[1]}<br>Aktiflik Oranı: %{customdata[2]:.1f}%<extra></extra>",
            ))
        if not past_df.empty:
            fig_daily.add_trace(go.Scatter(
                x=past_df["tarih_dt"], y=past_df["aktiflik_orani"], name="Aktiflik Oranı (Gerçekleşen)",
                mode="lines", line=dict(color="#DD6B20", width=2), yaxis="y2",
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Aktiflik Oranı: %{y:.1f}%<extra></extra>",
            ))
        if not future_df.empty:
            fig_daily.add_trace(go.Scatter(
                x=future_df["tarih_dt"], y=future_df["aktiflik_orani"], name="Aktiflik Oranı (Planlanan)",
                mode="lines", line=dict(color="#DD6B20", width=2, dash="dash"), yaxis="y2",
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Aktiflik Oranı (Planlanan): %{y:.1f}%<extra></extra>",
            ))
        fig_daily.update_layout(
            height=420, margin=dict(l=10, r=10, t=50, b=20),
            yaxis=dict(title="Günlük Gelir (₺)", showgrid=True, gridcolor="#E2E8F0"),
            yaxis2=dict(title="Aktiflik Oranı (%)", overlaying="y", side="right", range=[0, 100]),
            xaxis_title="Tarih", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        col_chart, col_dolu = st.columns([3, 2])
        with col_chart:
            chart_event = st.plotly_chart(
                fig_daily, use_container_width=True, config={"responsive": True},
                on_select="rerun", key="ev_gunluk_grafik",
            )
        with col_dolu:
            st.markdown("###### 🏠 Dolu Daireler")
            selected_points = (chart_event or {}).get("selection", {}).get("points", [])
            if not selected_points:
                st.info("Dolu daire listesini görmek için grafikte bir noktaya tıklayın.")
            else:
                clicked_date = pd.to_datetime(selected_points[0]["x"]).normalize()
                occupied = df_valid[(df_valid["start_date"] <= clicked_date) & (df_valid["end_date"] >= clicked_date)]
                st.caption(f"📅 {clicked_date.strftime('%d.%m.%Y')}")
                if occupied.empty:
                    st.info("Bu tarihte dolu daire bulunmuyor.")
                else:
                    st.dataframe(
                        occupied[["building_name", "unit_no", "customer_name", "contract_id", "end_date"]].rename(columns={
                            "building_name": "Apartman", "unit_no": "Daire No", "customer_name": "Müşteri",
                            "contract_id": "Sözleşme No", "end_date": "Bitiş Tarihi",
                        }),
                        use_container_width=True, hide_index=True,
                    )

st.divider()
st.markdown("### 📊 En Çok Talep Gören 5 Lokasyon")

loc_df_full = (
    df_filtered.assign(location=df_filtered["city"].astype(str) + " / " + df_filtered["district"].astype(str))
    .groupby("location")
    .agg(contract_count=("contract_id", "count"), total_revenue=("total_revenue", "sum"), avg_rent=("monthly_rent", "mean"))
    .reset_index()
)
total_contracts = loc_df_full["contract_count"].sum()
loc_df_full["share_pct"] = (loc_df_full["contract_count"] / total_contracts) * 100 if total_contracts > 0 else 0

col_loc1, col_loc2 = st.columns(2)
with col_loc1:
    st.markdown("###### 📊 Sözleşme Sayısına Göre")
    loc_df = loc_df_full.sort_values(by="contract_count", ascending=True).tail(5)
    if not loc_df.empty:
        fig_loc = px.bar(
            loc_df, x="contract_count", y="location", orientation="h", text="contract_count",
            custom_data=["total_revenue", "avg_rent", "share_pct"],
        )
        fig_loc.update_traces(
            marker_color="#4A5568", textposition="outside", texttemplate="%{x} ad.",
            hovertemplate="<b>📍</b> %{y}<br><b>📊 Sözleşme:</b> %{x} Adet<br><b>📈 Pay:</b> %{customdata[2]:.1f}%<br><b>💰 Ciro:</b> ₺%{customdata[0]:,.2f}<br><b>📊 Ort. Kira:</b> ₺%{customdata[1]:,.2f}<extra></extra>",
        )
        fig_loc.update_layout(height=360, margin=dict(l=10, r=40, t=50, b=20), xaxis_title="Sözleşme Adedi", yaxis_title=None,
                               plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig_loc, use_container_width=True, config={"responsive": True})

with col_loc2:
    st.markdown("###### 💰 Getirdiği Ciroya Göre")
    loc_df_rev = loc_df_full.sort_values(by="total_revenue", ascending=True).tail(5)
    if not loc_df_rev.empty:
        fig_loc_rev = px.bar(
            loc_df_rev, x="total_revenue", y="location", orientation="h", text="total_revenue",
            custom_data=["contract_count", "avg_rent", "share_pct"],
        )
        fig_loc_rev.update_traces(
            marker_color="#2F855A", textposition="outside", texttemplate="₺%{x:,.0f}",
            hovertemplate="<b>📍</b> %{y}<br><b>💰 Ciro:</b> ₺%{x:,.2f}<br><b>📊 Sözleşme:</b> %{customdata[0]} Adet<br><b>📈 Pay:</b> %{customdata[2]:.1f}%<br><b>📊 Ort. Kira:</b> ₺%{customdata[1]:,.2f}<extra></extra>",
        )
        fig_loc_rev.update_layout(height=360, margin=dict(l=10, r=40, t=50, b=20), xaxis_title="Toplam Ciro (₺)", yaxis_title=None,
                                   plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig_loc_rev, use_container_width=True, config={"responsive": True})

st.divider()
st.markdown("### 💳 Ödeme Yöntemi ve Döviz Cinsi Dağılımı")

PAYMENT_METHOD_LABELS = {"NAKİT": "Nakit", "KREDİ_KARTI": "Kredi Kartı", "EFT_HAVALE": "EFT / Havale"}
CURRENCY_LABELS = {"TRY": "Türk Lirası", "USD": "Amerikan Doları", "EUR": "Euro", "GBP": "İngiliz Sterlini"}

breakdown = api_get("/finance/payment-breakdown", {"category": "EV"}) or {"by_method": [], "by_currency": []}
col_pay1, col_pay2 = st.columns(2)
with col_pay1:
    st.markdown("###### 💳 Ödeme Yöntemine Göre")
    df_method = pd.DataFrame(breakdown["by_method"])
    if not df_method.empty:
        df_method["odeme_yontemi"] = df_method["odeme_yontemi"].map(PAYMENT_METHOD_LABELS).fillna(df_method["odeme_yontemi"])
        fig_method = px.pie(
            df_method, values="toplam_tl", names="odeme_yontemi", hole=0.4,
            custom_data=["odeme_adedi", "sozlesme_adedi"],
        )
        fig_method.update_traces(
            texttemplate="%{label}: %{percent:.2%}", textposition="outside", automargin=True,
            hovertemplate="<b>%{label}</b><br>Toplam Tutar: ₺%{value:,.2f}<br>Ödeme Adedi: %{customdata[0]}<br>Sözleşme Sayısı: %{customdata[1]}<extra></extra>",
        )
        fig_method.update_layout(height=400, margin=dict(l=10, r=10, t=80, b=40), showlegend=False)
        st.plotly_chart(fig_method, use_container_width=True, config={"responsive": True})
    else:
        st.info("Henüz ödeme kaydı bulunmuyor.")

with col_pay2:
    st.markdown("###### 💱 Döviz Cinsine Göre (TL Karşılığı)")
    df_currency = pd.DataFrame(breakdown["by_currency"])
    if not df_currency.empty:
        df_currency["doviz_cinsi"] = df_currency["doviz_cinsi"].map(CURRENCY_LABELS).fillna(df_currency["doviz_cinsi"])
        fig_currency = px.pie(
            df_currency, values="toplam_tl", names="doviz_cinsi", hole=0.4,
            custom_data=["odeme_adedi", "sozlesme_adedi", "toplam_doviz_miktari"],
        )
        fig_currency.update_traces(
            texttemplate="%{label}: %{percent:.2%}", textposition="outside", automargin=True,
            hovertemplate="<b>%{label}</b><br>TL Karşılığı: ₺%{value:,.2f}<br>Döviz Tutarı: %{customdata[2]:,.2f}<br>Ödeme Adedi: %{customdata[0]}<br>Sözleşme Sayısı: %{customdata[1]}<extra></extra>",
        )
        fig_currency.update_layout(height=400, margin=dict(l=10, r=10, t=80, b=40), showlegend=False)
        st.plotly_chart(fig_currency, use_container_width=True, config={"responsive": True})
    else:
        st.info("Henüz ödeme kaydı bulunmuyor.")

st.divider()
st.subheader("👨‍💼 Çalışan Bazlı Performans & Net Kâr Analizi")

df_emp = (
    df_filtered.groupby(["employee_id", "employee_name"])
    .agg(rental_count=("contract_id", "count"), total_revenue=("total_revenue", "sum"), employee_salary=("employee_salary", "first"))
    .reset_index()
)
df_emp["net_profit"] = df_emp["total_revenue"] - df_emp["employee_salary"]
df_emp["avg_revenue_per_tx"] = df_emp["total_revenue"] / df_emp["rental_count"]
df_emp = df_emp.sort_values(by="net_profit", ascending=False)

if not df_emp.empty:
    best = df_emp.iloc[0]
    st.success(f"🏆 **Dönemin En Başarılı Çalışanı:** {best['employee_name']} | **İşlem:** {best['rental_count']} | **Net Kâr:** ₺{best['net_profit']:,.2f}")

    p1, p2 = st.columns([1, 1])
    with p1:
        colors = ["#2E7D32" if v >= 0 else "#C62828" for v in df_emp["net_profit"]]
        fig = go.Figure(data=[go.Bar(
            x=df_emp["employee_name"], y=df_emp["net_profit"], marker_color=colors,
            text=df_emp["net_profit"].apply(lambda x: f"₺{x:,.0f}"), textposition="outside",
            hovertemplate="<b>👨‍💼</b> %{x}<br><b>📈 Net Kâr:</b> ₺%{y:,.2f}<extra></extra>",
        )])
        fig.update_layout(height=380, margin=dict(l=20, r=20, t=60, b=40), xaxis_title="Çalışan", yaxis_title="Net Kâr (₺)",
                           plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    with p2:
        st.markdown("##### 📜 Çalışan Detay Tablosu")
        df_table = df_emp[["employee_name", "rental_count", "total_revenue", "net_profit"]].rename(columns={
            "employee_name": "Çalışan", "rental_count": "Kiralama Sayısı", "total_revenue": "Toplam Ciro (₺)", "net_profit": "Net Kâr (₺)",
        })
        st.dataframe(df_table, use_container_width=True, hide_index=True)

st.divider()
tab_sys, tab_op = st.tabs(["🛡️ Sistem Giriş Logları", "📋 İşlem & Operasyon Logları"])

with tab_sys:
    logs = api_get("/housing/logs/login") or []
    if logs:
        df_logs = pd.DataFrame(logs)
        df_logs["Durum"] = df_logs["is_success"].apply(lambda x: "🟢 Başarılı" if x else "🔴 Hatalı")
        status_filter = st.radio("Filtrele:", ["Tümü", "Sadece Başarılı", "Sadece Hatalı"], key="ev_log_filtre")
        if status_filter == "Sadece Başarılı":
            df_logs = df_logs[df_logs["is_success"] == True]
        elif status_filter == "Sadece Hatalı":
            df_logs = df_logs[df_logs["is_success"] == False]
        st.dataframe(
            df_logs[["log_time", "employee_id", "employee_name", "department_id", "Durum", "error_reason"]].rename(columns={
                "log_time": "Tarih & Saat", "employee_id": "Çalışan ID", "employee_name": "Ad Soyad", "department_id": "Departman", "error_reason": "Açıklama",
            }), use_container_width=True, hide_index=True,
        )
    else:
        st.info("Ev/Emlak departmanına ait henüz kaydedilmiş giriş logu bulunmuyor.")

with tab_op:
    tx_logs = api_get("/housing/logs/transactions") or []
    if tx_logs:
        df_tx = pd.DataFrame(tx_logs)
        st.dataframe(
            df_tx[["log_time", "employee_id", "employee_name", "action_type", "details"]].rename(columns={
                "log_time": "Tarih / Saat", "employee_id": "Çalışan ID", "employee_name": "İşlemi Yapan", "action_type": "İşlem Tipi", "details": "Açıklama",
            }), use_container_width=True, hide_index=True,
        )
    else:
        st.info("Ev / Emlak departmanında henüz yapılmış bir operasyonel işlem logu bulunmuyor.")