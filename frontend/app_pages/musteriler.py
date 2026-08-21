"""Streamlit page for searching customers and viewing/editing their info and contract history."""
import pandas as pd
import streamlit as st

from api_client import api_get, api_put

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

st.title("👥 Müşteriler")
st.divider()

preselected_id = st.session_state.pop("secili_musteri_id", None)

search = st.text_input("🔍 İsim / Telefon / TC Kimlik No ile Ara:", key="musteri_arama").strip()
customers = api_get("/customers", {"search": search} if search else None) or []

if not customers:
    st.info("Aramanızla eşleşen müşteri bulunamadı." if search else "Kayıtlı müşteri bulunamadı.")
    st.stop()

df = pd.DataFrame(customers)

options = ["--- Seçiniz ---"] + [f"{r['isim']} ({r['telefon']}) - #{r['musteri_id']}" for _, r in df.iterrows()]
option_to_id = {f"{r['isim']} ({r['telefon']}) - #{r['musteri_id']}": r["musteri_id"] for _, r in df.iterrows()}

if preselected_id is not None:
    for opt in options:
        if option_to_id.get(opt) == preselected_id:
            st.session_state["musteri_secim"] = opt
            break

selected = st.selectbox("Müşteri Seçiniz:", options, key="musteri_secim")

if selected != "--- Seçiniz ---":
    customer_id = option_to_id[selected]
    detail = api_get(f"/customers/{customer_id}")

    if detail:
        musteri = detail["musteri"]

        st.markdown("### 📇 Müşteri Bilgileri")
        with st.form("musteri_duzenle_form"):
            col1, col2 = st.columns(2)
            with col1:
                isim = st.text_input("Ad Soyad", value=musteri.get("isim") or "")
                telefon = st.text_input("Telefon", value=musteri.get("telefon") or "")
            with col2:
                email = st.text_input("E-posta", value=musteri.get("email") or "")
                tc_no = st.text_input("TC Kimlik No", value=musteri.get("tc_kimlik_no") or "")
            st.caption(f"Kayıt Tarihi: {musteri.get('kayit_tarihi', '-')}  |  Müşteri ID: #{customer_id}")

            submitted = st.form_submit_button("💾 Bilgileri Kaydet", type="primary")
            if submitted:
                if not isim.strip() or not telefon.strip() or not email.strip() or not tc_no.strip():
                    st.error("❌ Ad Soyad, Telefon, E-posta ve TC Kimlik No alanlarının hiçbiri boş bırakılamaz.")
                else:
                    result = api_put(
                        f"/customers/{customer_id}",
                        {
                            "isim": isim.strip(),
                            "telefon": telefon.strip(),
                            "email": email.strip(),
                            "tc_kimlik_no": tc_no.strip(),
                        },
                    )
                    if result and result.get("success"):
                        st.success(f"✅ {result['message']}")
                    elif result:
                        st.error(result["message"])

        st.markdown("---")
        st.markdown("### 📄 Sözleşme Geçmişi")
        sozlesmeler = detail.get("sozlesmeler", [])
        if not sozlesmeler:
            st.info("Bu müşteriye ait sözleşme bulunmuyor.")
        else:
            st.metric("Bu Müşterinin Toplam Ödemesi", f"₺{detail.get('toplam_odeme', 0):,.2f}")

            df_s = pd.DataFrame(sozlesmeler)
            display = df_s.rename(
                columns={
                    "sozlesme_no": "Sözleşme No", "kategori": "Kategori", "baslangic_tarihi": "Başlangıç",
                    "bitis_tarihi": "Bitiş", "toplam_tutar": "Toplam Tutar (₺)",
                    "odenen_toplam_tutar": "Ödenen (₺)", "kalan_borc": "Kalan Borç (₺)",
                    "sozlesme_durumu": "Durum",
                }
            )
            event = st.dataframe(
                display, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="musteri_sozlesme_tablo",
            )
            selected_rows = event.selection.rows if event and event.selection else []
            if selected_rows:
                row = df_s.iloc[selected_rows[0]]
                if st.button(f"➡️ #{row['sozlesme_no']} Sözleşmesinin Detayına Git", type="primary"):
                    st.session_state["secili_sozlesme_no"] = row["sozlesme_no"]
                    st.session_state["secili_sozlesme_kategori"] = row["kategori"]
                    st.switch_page("app_pages/sozlesmeler.py")
            else:
                st.caption("Detayına gitmek için tablodan bir sözleşme satırı seçin.")
