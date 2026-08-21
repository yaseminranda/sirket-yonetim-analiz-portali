from datetime import date

import pandas as pd
import streamlit as st

from api_client import api_get, api_get_cached, api_post

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

st.title("🚗➕ Araç Filosu Yönetimi")
st.caption("Madde 5: Sisteme yeni araç ekleme ve mevcut filodan araç çıkarma (pasife alma) işlemleri.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)

tab_add, tab_retire, tab_list = st.tabs(["➕ Yeni Araç Ekle", "🚫 Filodan Çıkar", "📋 Filo Listesi"])

# ============ YENİ ARAÇ EKLE ============
with tab_add:
    st.subheader("Yeni Araç Ekle")
    brands = api_get_cached("/vehicles/brands") or []
    brand_options = ["➕ [Yeni Marka Ekle]"] + [b["marka_adi"] for b in brands]
    brand_map = {b["marka_adi"]: b["marka_id"] for b in brands}

    col1, col2 = st.columns(2)
    with col1:
        selected_brand = st.selectbox("Marka Seçiniz:", brand_options, key="ekle_marka_sec")
        is_new_brand = selected_brand == "➕ [Yeni Marka Ekle]"
        new_brand_name = st.text_input("Yeni Marka Adı:", key="ekle_yeni_marka") if is_new_brand else ""

    model_options, model_map = ["➕ [Yeni Model Ekle]"], {}
    if not is_new_brand and selected_brand:
        models = api_get("/vehicles/models", {"marka_id": brand_map[selected_brand]}) or []
        model_options = ["➕ [Yeni Model Ekle]"] + [m["model_adi"] for m in models]
        model_map = {m["model_adi"]: m["model_id"] for m in models}

    with col2:
        selected_model = st.selectbox("Model Seçiniz:", model_options, key="ekle_model_sec", disabled=is_new_brand)
        is_new_model = is_new_brand or selected_model == "➕ [Yeni Model Ekle]"
        new_model_name = st.text_input("Yeni Model Adı:", key="ekle_yeni_model") if is_new_model else ""

    col3, col4 = st.columns(2)
    with col3:
        plaka = st.text_input("Plaka:", key="ekle_plaka").strip().upper()
    with col4:
        gunluk_ucret = st.number_input("Günlük Kiralama Ücreti (₺):", min_value=0.0, step=50.0, key="ekle_ucret")

    if st.button("✅ Aracı Filoya Ekle", type="primary"):
        payload = {
            "marka_id": None if is_new_brand else brand_map.get(selected_brand),
            "new_marka_adi": new_brand_name or None,
            "model_id": None if is_new_model else model_map.get(selected_model),
            "new_model_adi": new_model_name or None,
            "plaka": plaka,
            "gunluk_ucret": gunluk_ucret,
        }
        result = api_post("/vehicles/fleet", payload)
        if result and result.get("success"):
            st.session_state["toast_mesaj"] = result["message"]
            st.session_state["toast_icon"] = "🚗"
            st.rerun()
        elif result:
            st.error(result["message"])

# ============ FİLODAN ÇIKAR ============
with tab_retire:
    st.subheader("Aracı Filodan Çıkar (Pasife Al)")
    fleet = api_get("/vehicles/fleet") or []
    df_fleet = pd.DataFrame(fleet)
    if df_fleet.empty:
        st.info("Sistemde kayıtlı araç bulunamadı.")
    else:
        active_fleet = df_fleet[df_fleet["pasif_tarihi"].isin(["None", "NaT", "nan"]) | df_fleet["pasif_tarihi"].isna()]
        if active_fleet.empty:
            st.info("Filoda aktif (pasife alınmamış) araç bulunmuyor.")
        else:
            options = ["--- Seçiniz ---"] + [
                f"{r['marka']} {r['model']} - {r['plaka']} ({r['musaitlik_durumu']})" for _, r in active_fleet.iterrows()
            ]
            option_map = {
                f"{r['marka']} {r['model']} - {r['plaka']} ({r['musaitlik_durumu']})": r["arac_id"] for _, r in active_fleet.iterrows()
            }
            selected = st.selectbox("Filodan Çıkarılacak Aracı Seçiniz:", options, key="cikar_arac_sec")
            retire_date = st.date_input("Filodan Çıkış Tarihi:", value=date.today(), key="cikar_arac_tarih")

            if selected != "--- Seçiniz ---":
                if st.button("🚫 Aracı Filodan Çıkar", type="secondary"):
                    result = api_post(f"/vehicles/fleet/{option_map[selected]}/retire", {"retire_date": str(retire_date)})
                    if result and result.get("success"):
                        st.session_state["toast_mesaj"] = result["message"]
                        st.session_state["toast_icon"] = "🚫"
                        st.rerun()
                    elif result:
                        st.error(result["message"])

# ============ FİLO LİSTESİ ============
with tab_list:
    st.subheader("📋 Mevcut Filo (Aktif + Pasif)")
    fleet = api_get("/vehicles/fleet") or []
    if fleet:
        df_show = pd.DataFrame(fleet)
        df_show["Durum"] = df_show["pasif_tarihi"].apply(lambda x: "🚫 Pasif" if x and str(x) not in ("None", "NaT", "nan") else "🟢 Aktif")
        st.dataframe(
            df_show[["arac_id", "plaka", "marka", "model", "gunluk_ucret", "musaitlik_durumu", "sisteme_ekleme_tarihi", "pasif_tarihi", "Durum"]].rename(
                columns={
                    "arac_id": "Araç ID", "plaka": "Plaka", "marka": "Marka", "model": "Model",
                    "gunluk_ucret": "Günlük Ücret (₺)", "musaitlik_durumu": "Müsaitlik",
                    "sisteme_ekleme_tarihi": "Eklenme Tarihi", "pasif_tarihi": "Pasife Alınma Tarihi",
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sistemde kayıtlı araç bulunamadı.")
