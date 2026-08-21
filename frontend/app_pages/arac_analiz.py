"""Streamlit page with the vehicle rental department's fleet, revenue, and employee performance analytics."""

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
is_arac_dep_muduru = any(role in user_role for role in ["DEPARTMAN MÜDÜRÜ", "DEPARTMAN MUDURU"]) and (user_dept in ["D2", "2"])

if not (is_genel_mudur or is_arac_dep_muduru):
    st.error("🚫 Bu sayfaya erişim yetkiniz bulunmamaktadır! (Sadece Genel Müdür ve Araç Departmanı Müdürü girebilir)")
    st.stop()

analysis = api_get("/vehicles/analysis")
if analysis is None:
    st.stop()

df_rental = pd.DataFrame(analysis["rows"])
st.title("🚗 Araç Departmanı Analiz Paneli")
st.caption("Filo Performansı, Çalışan Katkısı ve Operasyonel Sözleşme Takibi")
st.divider()

if df_rental.empty:
    st.warning("Veritabanında henüz kiralama kaydı bulunamadı.")
    st.stop()

df_rental["start_date"] = pd.to_datetime(df_rental["start_date"]).dt.normalize()
df_rental["end_date"] = pd.to_datetime(df_rental["end_date"]).dt.normalize()
df_rental["year"] = df_rental["start_date"].dt.year
df_rental["rental_duration"] = pd.to_numeric(df_rental["rental_duration"], errors="coerce").fillna(0)
df_rental["total_price"] = pd.to_numeric(df_rental["total_price"], errors="coerce").fillna(0)
df_rental["employee_salary"] = pd.to_numeric(df_rental["employee_salary"], errors="coerce").fillna(0)

status_upper = df_rental["contract_status"].astype(str).str.upper()
cancelled_mask = status_upper.str.contains("İPTAL|IPTAL|SİLİNDİ|SILINDI", na=False)
df_valid = df_rental[~cancelled_mask].copy()

available_years = sorted(df_valid["year"].dropna().unique().astype(int).tolist(), reverse=True)
selected_year = st.selectbox("📅 Analiz Edilecek Dönem / Yıl:", ["Tüm Yıllar"] + available_years)
df_filtered = df_valid[df_valid["year"] == int(selected_year)].copy() if selected_year != "Tüm Yıllar" else df_valid.copy()

today = pd.to_datetime("today").normalize()
total_revenue = df_filtered[df_filtered["start_date"] <= today]["total_price"].sum()
total_salary_expense = df_filtered.groupby("employee_id")["employee_salary"].first().sum()
total_net_profit = total_revenue - total_salary_expense

if selected_year == "Tüm Yıllar":
    as_of_date = date.today()
else:
    as_of_year = int(selected_year)
    as_of_date = date.today() if as_of_year >= date.today().year else date(as_of_year, 12, 31)
fleet_count_info = api_get("/vehicles/fleet-count", {"as_of_date": str(as_of_date)}) or {}
vehicle_count = fleet_count_info.get("sayi", analysis["total_vehicles"])
occupied_vehicle_count = fleet_count_info.get("dolu", 0)

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    f"🚗 Filodaki Araç Sayısı ({selected_year})", f"{vehicle_count} Adet",
    delta=f"{occupied_vehicle_count} Dolu" if vehicle_count else None, delta_color="off",
)
col2.metric("👥 Departman Çalışanı", f"{analysis['total_employees']} Kişi")
col3.metric(f"💰 Gerçekleşen Ciro ({selected_year})", f"₺{total_revenue:,.2f}")
col4.metric(
    f"📈 Operasyonel Net Kâr ({selected_year})", f"₺{total_net_profit:,.2f}",
    delta=f"{(total_net_profit / total_revenue * 100):.1f}% Kâr Marjı" if total_revenue > 0 else None,
)

st.divider()
st.markdown("### 📈 Günlük Gelir Dağılımı & Aktiflik Oranı")

col_dr1, col_dr2 = st.columns(2)
with col_dr1:
    dr_start = st.date_input("Başlangıç Tarihi:", value=date.today() - timedelta(days=30), key="arac_gunluk_baslangic")
with col_dr2:
    dr_end = st.date_input("Bitiş Tarihi:", value=date.today() + timedelta(days=15), key="arac_gunluk_bitis")

if dr_start > dr_end:
    st.error("⚠️ Başlangıç tarihi bitiş tarihinden sonra olamaz.")
else:
    daily_data = api_get("/vehicles/daily-revenue", {"start_date": str(dr_start), "end_date": str(dr_end)}) or {"rows": []}
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
                mode="lines+markers", line=dict(color="#2B6CB0", width=2), marker=dict(size=5, color="#2B6CB0"),
                fill="tozeroy", fillcolor="rgba(43,108,176,0.15)",
                customdata=past_df[["dolu_sayisi", "toplam_filo", "aktiflik_orani"]].values,
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Günlük Gelir: ₺%{y:,.2f}<br>Dolu Araç: %{customdata[0]} / %{customdata[1]}<br>Aktiflik Oranı: %{customdata[2]:.1f}%<extra></extra>",
            ))
        if not future_df.empty:
            fig_daily.add_trace(go.Scatter(
                x=future_df["tarih_dt"], y=future_df["gunluk_gelir"], name="Günlük Gelir (Planlanan)",
                mode="lines+markers", line=dict(color="#2B6CB0", width=2, dash="dash"), marker=dict(size=5, color="#2B6CB0"),
                customdata=future_df[["dolu_sayisi", "toplam_filo", "aktiflik_orani"]].values,
                hovertemplate="<b>%{x|%d.%m.%Y}</b><br>Günlük Gelir (Planlanan): ₺%{y:,.2f}<br>Dolu Araç: %{customdata[0]} / %{customdata[1]}<br>Aktiflik Oranı: %{customdata[2]:.1f}%<extra></extra>",
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
                on_select="rerun", key="arac_gunluk_grafik",
            )
        with col_dolu:
            st.markdown("###### 📋 Dolu Araçlar")
            selected_points = (chart_event or {}).get("selection", {}).get("points", [])
            if not selected_points:
                st.info("Dolu araç listesini görmek için grafikte bir noktaya tıklayın.")
            else:
                clicked_date = pd.to_datetime(selected_points[0]["x"]).normalize()
                occupied = df_valid[(df_valid["start_date"] <= clicked_date) & (df_valid["end_date"] >= clicked_date)]
                st.caption(f"📅 {clicked_date.strftime('%d.%m.%Y')}")
                if occupied.empty:
                    st.info("Bu tarihte dolu araç bulunmuyor.")
                else:
                    st.dataframe(
                        occupied[["plate", "brand", "model", "customer_name", "contract_id", "end_date"]].rename(columns={
                            "plate": "Plaka", "brand": "Marka", "model": "Model", "customer_name": "Müşteri",
                            "contract_id": "Sözleşme No", "end_date": "Bitiş Tarihi",
                        }),
                        use_container_width=True, hide_index=True,
                    )

st.divider()
st.markdown("### 📊 En Çok Tercih Edilen 5 Araç Modeli")

brand_df_full = (
    df_filtered.groupby(["brand", "model"])
    .agg(rental_count=("contract_id", "count"), total_revenue=("total_price", "sum"), avg_days=("rental_duration", "mean"))
    .reset_index()
)
total_rentals = brand_df_full["rental_count"].sum()
brand_df_full["share_pct"] = (brand_df_full["rental_count"] / total_rentals) * 100 if total_rentals > 0 else 0
brand_df_full["brand_model"] = brand_df_full["brand"] + " " + brand_df_full["model"]

col_pref1, col_pref2 = st.columns(2)
with col_pref1:
    st.markdown("###### 📊 Sözleşme Sayısına Göre")
    brand_df = brand_df_full.sort_values(by="rental_count", ascending=True).tail(5)
    if not brand_df.empty:
        fig = px.bar(
            brand_df, x="rental_count", y="brand_model", orientation="h", text="rental_count",
            custom_data=["total_revenue", "share_pct", "avg_days"],
        )
        fig.update_traces(
            marker_color="#4A5568", textposition="outside", texttemplate="%{x} ad.",
            hovertemplate="<b>🚗 Model:</b> %{y}<br><b>📊 Kiralama:</b> %{x} Adet<br><b>💰 Ciro:</b> ₺%{customdata[0]:,.2f}<br><b>📈 Pay:</b> %{customdata[1]:.1f}%<br><b>⏱️ Ort. Süre:</b> %{customdata[2]:.1f} Gün<extra></extra>",
        )
        fig.update_layout(height=360, margin=dict(l=10, r=40, t=50, b=20), xaxis_title="Kiralama Adedi", yaxis_title=None,
                           plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=True, gridcolor="#E2E8F0"))
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True})

with col_pref2:
    st.markdown("###### 💰 Getirdiği Ciroya Göre")
    brand_df_rev = brand_df_full.sort_values(by="total_revenue", ascending=True).tail(5)
    if not brand_df_rev.empty:
        fig_rev = px.bar(
            brand_df_rev, x="total_revenue", y="brand_model", orientation="h", text="total_revenue",
            custom_data=["rental_count", "share_pct", "avg_days"],
        )
        fig_rev.update_traces(
            marker_color="#2F855A", textposition="outside", texttemplate="₺%{x:,.0f}",
            hovertemplate="<b>🚗 Model:</b> %{y}<br><b>💰 Ciro:</b> ₺%{x:,.2f}<br><b>📊 Kiralama:</b> %{customdata[0]} Adet<br><b>📈 Pay:</b> %{customdata[1]:.1f}%<br><b>⏱️ Ort. Süre:</b> %{customdata[2]:.1f} Gün<extra></extra>",
        )
        fig_rev.update_layout(height=360, margin=dict(l=10, r=40, t=50, b=20), xaxis_title="Toplam Ciro (₺)", yaxis_title=None,
                               plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=True, gridcolor="#E2E8F0"))
        st.plotly_chart(fig_rev, use_container_width=True, config={"responsive": True})

st.divider()
st.markdown("### 💳 Ödeme Yöntemi ve Döviz Cinsi Dağılımı")

PAYMENT_METHOD_LABELS = {"NAKİT": "Nakit", "KREDİ_KARTI": "Kredi Kartı", "EFT_HAVALE": "EFT / Havale"}
CURRENCY_LABELS = {"TRY": "Türk Lirası", "USD": "Amerikan Doları", "EUR": "Euro", "GBP": "İngiliz Sterlini"}

breakdown = api_get("/finance/payment-breakdown", {"category": "ARAC"}) or {"by_method": [], "by_currency": []}
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
st.markdown("### 👨‍💼 Çalışan Bazlı Net Kâr ve Operasyonel Performans")

emp_perf = (
    df_filtered.groupby("employee_name")
    .agg(total_transactions=("contract_id", "count"), total_revenue=("total_price", "sum"), employee_salary=("employee_salary", "first"))
    .reset_index()
)
emp_perf["net_profit"] = emp_perf["total_revenue"] - emp_perf["employee_salary"]
emp_perf["avg_revenue_per_tx"] = emp_perf["total_revenue"] / emp_perf["total_transactions"]
emp_perf = emp_perf.sort_values(by="net_profit", ascending=False)

if not emp_perf.empty:
    best = emp_perf.iloc[0]
    st.success(f"🏆 **Dönemin En Başarılı Çalışanı:** {best['employee_name']} | **İşlem:** {best['total_transactions']} | **Net Kâr:** ₺{best['net_profit']:,.2f}")

col_c1, col_c2 = st.columns([1, 1])
with col_c1:
    colors = ["#2E7D32" if v >= 0 else "#C62828" for v in emp_perf["net_profit"]]
    fig2 = go.Figure(data=[go.Bar(
        x=emp_perf["employee_name"], y=emp_perf["net_profit"], marker_color=colors,
        text=emp_perf["net_profit"].apply(lambda x: f"₺{x:,.0f}"), textposition="outside",
        customdata=list(zip(emp_perf["total_transactions"], emp_perf["total_revenue"], emp_perf["employee_salary"], emp_perf["avg_revenue_per_tx"])),
        hovertemplate="<b>👨‍💼</b> %{x}<br><b>📈 Net Kâr:</b> ₺%{y:,.2f}<br><b>📜 İşlem:</b> %{customdata[0]} Adet<br><b>💰 Ciro:</b> ₺%{customdata[1]:,.2f}<br><b>💵 Maaş:</b> ₺%{customdata[2]:,.2f}<extra></extra>",
    )])
    fig2.update_layout(height=380, margin=dict(l=20, r=20, t=60, b=40), xaxis_title="Çalışan", yaxis_title="Net Kâr (₺)",
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", yaxis=dict(showgrid=True, gridcolor="#E2E8F0"))
    st.plotly_chart(fig2, use_container_width=True, config={"responsive": True})

with col_c2:
    st.write("📋 **Çalışan Detaylı Analiz Tablosu**")
    st.dataframe(
        emp_perf[["employee_name", "total_transactions", "total_revenue", "employee_salary", "net_profit"]].rename(columns={
            "employee_name": "Çalışan", "total_transactions": "İşlem Sayısı", "total_revenue": "Toplam Ciro (₺)",
            "employee_salary": "Maaş (₺)", "net_profit": "Net Kâr (₺)",
        }), use_container_width=True, hide_index=True,
    )

st.divider()
tab_sys, tab_op = st.tabs(["🛡️ Sistem Giriş Logları", "📋 İşlem & Operasyon Logları"])

with tab_sys:
    logs = api_get("/vehicles/logs/login") or []
    if logs:
        df_logs = pd.DataFrame(logs)
        df_logs["Durum"] = df_logs["is_success"].apply(lambda x: "🟢 Başarılı" if x else "🔴 Hatalı")
        status_filter = st.radio("Filtrele:", ["Tümü", "Sadece Başarılı", "Sadece Hatalı"], key="arac_log_filtre")
        if status_filter == "Sadece Başarılı":
            df_logs = df_logs[df_logs["is_success"] == True]
        elif status_filter == "Sadece Hatalı":
            df_logs = df_logs[df_logs["is_success"] == False]
        st.dataframe(
            df_logs[["log_time", "employee_id", "employee_name", "department_id", "Durum", "error_reason"]].rename(columns={
                "log_time": "Tarih & Saat", "employee_id": "Çalışan ID", "employee_name": "Ad Soyad",
                "department_id": "Departman", "error_reason": "Açıklama",
            }), use_container_width=True, hide_index=True,
        )
    else:
        st.info("Kayıtlı giriş log verisi bulunmuyor.")

with tab_op:
    tx_logs = api_get("/vehicles/logs/transactions") or []
    if tx_logs:
        df_tx = pd.DataFrame(tx_logs)
        st.dataframe(
            df_tx[["log_time", "employee_id", "employee_name", "action_type", "details"]].rename(columns={
                "log_time": "Tarih / Saat", "employee_id": "Çalışan ID", "employee_name": "İşlemi Yapan",
                "action_type": "İşlem Tipi", "details": "Açıklama",
            }), use_container_width=True, hide_index=True,
        )
    else:
        st.info("Henüz yapılmış bir operasyonel işlem logu bulunmuyor.")