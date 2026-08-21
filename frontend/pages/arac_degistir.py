from datetime import date

import pandas as pd
import streamlit as st

from api_client import api_get, api_post

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()
user_role = str(st.session_state.get("yetki", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"])
is_arac_departmani = any(dep in all_session_values for dep in ["D2"])

if not (is_genel_mudur or is_arac_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()

st.title("🔄 Araç Değişimi (Arıza / Sorun Durumunda)")
st.caption(
    "Madde 7: Kiralanmış bir araç kullanım sırasında arızalanır veya sorun çıkarırsa, müşteri "
    "başka bir araca aktarılabilir. Eski sözleşmenin bitiş tarihi değişim gününe çekilir; yeni "
    "sözleşme, değişim gününden eski sözleşmenin asıl bitiş tarihine kadar (tutarı 0₺, çünkü ödeme "
    "zaten alınmıştı) oluşturulur."
)
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)


def get_contracts_df() -> pd.DataFrame:
    data = api_get("/vehicles/contracts")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["baslangic_tarihi"] = pd.to_datetime(df["baslangic_tarihi"], errors="coerce").dt.normalize()
    df["bitis_tarihi"] = pd.to_datetime(df["bitis_tarihi"], errors="coerce").dt.normalize()
    return df


st.subheader("1️⃣ Değiştirilecek Sözleşmeyi Seçin")
search_term = st.text_input("🔍 Sözleşme No / Plaka / Müşteri Adına Göre Ara:", key="degistir_arama").strip().lower()

df_all = get_contracts_df()
if df_all.empty:
    st.info("Sistemde kayıtlı araç sözleşmesi bulunamadı.")
    st.stop()

active = df_all[df_all["sozlesme_durumu"].astype(str).str.upper().isin(["DEVAM EDIYOR", "BEKLEMEDE", "AKTİF", "AKTIF"])]
if search_term:
    active = active[
        active["sozlesme_no"].astype(str).str.lower().str.contains(search_term, na=False)
        | active["plaka"].astype(str).str.lower().str.contains(search_term, na=False)
        | active["musteri_adi"].astype(str).str.lower().str.contains(search_term, na=False)
    ]

if active.empty:
    st.warning("Aramanızla eşleşen aktif/beklemedeki bir sözleşme bulunamadı.")
    st.stop()

options = ["--- Seçiniz ---"] + [
    f"#{r['sozlesme_no']} | {r['plaka']} ({r['marka']} {r['model']}) | {r['musteri_adi']} | Bitiş: {r['bitis_tarihi'].strftime('%Y-%m-%d')}"
    for _, r in active.iterrows()
]
option_map = {
    f"#{r['sozlesme_no']} | {r['plaka']} ({r['marka']} {r['model']}) | {r['musteri_adi']} | Bitiş: {r['bitis_tarihi'].strftime('%Y-%m-%d')}": r["sozlesme_no"]
    for _, r in active.iterrows()
}
selected = st.selectbox("Sözleşme Seçiniz:", options, key="degistir_sozlesme_sec")

if selected != "--- Seçiniz ---":
    contract_no = option_map[selected]
    row = active[active["sozlesme_no"] == contract_no].iloc[0]
    old_start = row["baslangic_tarihi"].date()
    old_end = row["bitis_tarihi"].date()

    st.info(
        f"📌 **Mevcut Araç:** {row['marka']} {row['model']} ({row['plaka']}) | "
        f"**Sözleşme Aralığı:** {old_start} → {old_end} | **Müşteri:** {row['musteri_adi']}"
    )

    st.markdown("---")
    st.subheader("2️⃣ Değişim Bilgilerini Girin")

    col1, col2 = st.columns(2)
    with col1:
        change_date = st.date_input(
            "Değişim (Arıza) Tarihi:", value=max(date.today(), old_start), min_value=old_start, max_value=old_end,
            key="degistir_tarih",
        )
    with col2:
        reason = st.text_input("Değişim Nedeni (Örn: Arıza, Kaza vb.):", key="degistir_neden")

    available_vehicles = api_get("/vehicles/available", {"start_date": str(change_date), "end_date": str(old_end)}) or []
    available_vehicles = [v for v in available_vehicles if v["arac_id"] != row["arac_id"]]

    if not available_vehicles:
        st.warning("⚠️ Seçilen tarih aralığında müsait başka bir araç bulunamadı.")
    else:
        df_avail = pd.DataFrame(available_vehicles)
        vehicle_options = ["--- Seçiniz ---"] + [
            f"{v['marka']} {v['model']} - {v['plaka']} (₺{v['gunluk_ucret']:,.0f}/gün)" for _, v in df_avail.iterrows()
        ]
        vehicle_map = {
            f"{v['marka']} {v['model']} - {v['plaka']} (₺{v['gunluk_ucret']:,.0f}/gün)": v["arac_id"] for _, v in df_avail.iterrows()
        }
        selected_new_vehicle = st.selectbox("Yeni Araç Seçiniz:", vehicle_options, key="degistir_yeni_arac")

        if selected_new_vehicle != "--- Seçiniz ---":
            st.markdown("---")
            st.warning(
                f"⚠️ Onayladığınızda: eski sözleşmenin bitiş tarihi **{change_date}** olarak güncellenecek, "
                f"**{change_date} → {old_end}** tarihleri arasında yeni bir sözleşme (tutar ₺0,00, "
                "çünkü ödeme zaten alınmıştı) oluşturulacak ve ek şoförler yeni sözleşmeye otomatik taşınacaktır."
            )
            if st.button("✅ Araç Değişimini Onayla ve Uygula", type="primary"):
                result = api_post(
                    f"/vehicles/contracts/{contract_no}/change-vehicle",
                    {
                        "new_vehicle_id": vehicle_map[selected_new_vehicle],
                        "change_date": str(change_date),
                        "reason": reason,
                    },
                )
                if result and result.get("success"):
                    st.session_state["toast_mesaj"] = result["message"]
                    st.session_state["toast_icon"] = "🔄"
                    st.rerun()
                elif result:
                    st.error(result["message"])
