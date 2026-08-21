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
is_ev_departmani = any(dep in all_session_values for dep in ["D1"])

if not (is_genel_mudur or is_ev_departmani):
    st.error("🚫 **Bu sayfaya erişim yetkiniz bulunmamaktadır!**")
    st.stop()

st.title("🏠➕ Konut Portföyü Yönetimi")
st.caption("Madde 5: Sisteme yeni daire ekleme ve mevcut portföyden daire çıkarma (pasife alma) işlemleri.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)

tab_add, tab_retire, tab_list = st.tabs(["➕ Yeni Daire Ekle", "🚫 Portföyden Çıkar", "📋 Daire Listesi"])

# ============ YENİ DAİRE EKLE ============
with tab_add:
    st.subheader("Yeni Daire Ekle")
    buildings = api_get_cached("/housing/buildings") or []
    building_options = ["➕ [Yeni Apartman/Bina Ekle]"] + [b["apartman_adi"] for b in buildings]
    building_map = {b["apartman_adi"]: b["apartman_id"] for b in buildings}

    selected_building = st.selectbox("Apartman/Bina Seçiniz:", building_options, key="ekle_apartman_sec")
    is_new_building = selected_building == "➕ [Yeni Apartman/Bina Ekle]"

    new_apartman_adi = new_il = new_ilce = new_mahalle = ""
    if is_new_building:
        col_b1, col_b2, col_b3, col_b4 = st.columns(4)
        with col_b1:
            new_apartman_adi = st.text_input("Yeni Apartman/Bina Adı:", key="ekle_yeni_apartman")
        with col_b2:
            new_il = st.text_input("İl:", key="ekle_yeni_il")
        with col_b3:
            new_ilce = st.text_input("İlçe:", key="ekle_yeni_ilce")
        with col_b4:
            new_mahalle = st.text_input("Mahalle (İsteğe Bağlı):", key="ekle_yeni_mahalle")

    col1, col2, col3 = st.columns(3)
    with col1:
        daire_no = st.text_input("Daire No:", key="ekle_daire_no")
    with col2:
        oda_sayisi = st.text_input("Oda Sayısı (Örn: 2+1):", key="ekle_oda_sayisi")
    with col3:
        aylik_kira = st.number_input("Aylık Kira Bedeli (₺):", min_value=0.0, step=500.0, key="ekle_aylik_kira")

    if st.button("✅ Daireyi Portföye Ekle", type="primary"):
        payload = {
            "apartman_id": None if is_new_building else building_map.get(selected_building),
            "new_apartman_adi": new_apartman_adi or None,
            "new_il": new_il or None,
            "new_ilce": new_ilce or None,
            "new_mahalle": new_mahalle or None,
            "daire_no": daire_no,
            "oda_sayisi": oda_sayisi,
            "aylik_kira": aylik_kira,
        }
        result = api_post("/housing/units", payload)
        if result and result.get("success"):
            st.session_state["toast_mesaj"] = result["message"]
            st.session_state["toast_icon"] = "🏠"
            st.rerun()
        elif result:
            st.error(result["message"])

# ============ PORTFÖYDEN ÇIKAR ============
with tab_retire:
    st.subheader("Daireyi Portföyden Çıkar (Pasife Al)")
    units = api_get("/housing/units") or []
    df_units = pd.DataFrame(units)
    if df_units.empty:
        st.info("Sistemde kayıtlı daire bulunamadı.")
    else:
        active_units = df_units[df_units["pasif_tarihi"].isin(["None", "NaT", "nan"]) | df_units["pasif_tarihi"].isna()]
        if active_units.empty:
            st.info("Portföyde aktif (pasife alınmamış) daire bulunmuyor.")
        else:
            options = ["--- Seçiniz ---"] + [
                f"{r['apartman_adi']} No:{r['daire_no']} ({r['musaitlik_durumu']})" for _, r in active_units.iterrows()
            ]
            option_map = {
                f"{r['apartman_adi']} No:{r['daire_no']} ({r['musaitlik_durumu']})": r["daire_id"] for _, r in active_units.iterrows()
            }
            selected = st.selectbox("Portföyden Çıkarılacak Daireyi Seçiniz:", options, key="cikar_daire_sec")
            retire_date = st.date_input("Portföyden Çıkış Tarihi:", value=date.today(), key="cikar_daire_tarih")

            if selected != "--- Seçiniz ---":
                if st.button("🚫 Daireyi Portföyden Çıkar", type="secondary"):
                    result = api_post(f"/housing/units/{option_map[selected]}/retire", {"retire_date": str(retire_date)})
                    if result and result.get("success"):
                        st.session_state["toast_mesaj"] = result["message"]
                        st.session_state["toast_icon"] = "🚫"
                        st.rerun()
                    elif result:
                        st.error(result["message"])

# ============ DAİRE LİSTESİ ============
with tab_list:
    st.subheader("📋 Mevcut Portföy (Aktif + Pasif)")
    units = api_get("/housing/units") or []
    if units:
        df_show = pd.DataFrame(units)
        df_show["Durum"] = df_show["pasif_tarihi"].apply(lambda x: "🚫 Pasif" if x and str(x) not in ("None", "NaT", "nan") else "🟢 Aktif")
        st.dataframe(
            df_show[["daire_id", "apartman_adi", "il", "ilce", "daire_no", "oda_sayisi", "aylik_kira", "musaitlik_durumu", "sisteme_ekleme_tarihi", "pasif_tarihi", "Durum"]].rename(
                columns={
                    "daire_id": "Daire ID", "apartman_adi": "Apartman", "il": "İl", "ilce": "İlçe",
                    "daire_no": "Daire No", "oda_sayisi": "Oda Sayısı", "aylik_kira": "Aylık Kira (₺)",
                    "musaitlik_durumu": "Müsaitlik", "sisteme_ekleme_tarihi": "Eklenme Tarihi", "pasif_tarihi": "Pasife Alınma Tarihi",
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sistemde kayıtlı daire bulunamadı.")
