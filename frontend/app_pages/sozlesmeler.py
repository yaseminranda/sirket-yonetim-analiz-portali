"""Streamlit page for viewing contracts, extending end dates, and downloading receipts/documents."""

import pandas as pd
import streamlit as st

from api_client import api_get, api_get_raw, api_post, clear_cache

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

user_role = str(st.session_state.get("yetki", "")).upper().strip()
user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
is_genel_mudur = any(role in user_role for role in ["GENEL MÜDÜR", "GENEL MUDUR"])
can_extend_arac = is_genel_mudur or user_dept in ["D2", "2"]
can_extend_ev = is_genel_mudur or user_dept in ["D1", "1"]

is_company_gm = is_genel_mudur and (user_dept in ["D3", "3"])
if is_company_gm:
    locked_category = None
elif user_dept in ["D1", "1"]:
    locked_category = "EV"
elif user_dept in ["D2", "2"]:
    locked_category = "ARAC"
else:
    locked_category = None

st.title("📄 Sözleşmeler")
st.caption("Araç ve ev sözleşmelerini tek yerden görüntüleyin, ödeme detaylarını inceleyin ve makbuz indirin.")
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)

preselected_no = st.session_state.get("secili_sozlesme_no")
preselected_category = st.session_state.get("secili_sozlesme_kategori")

if preselected_no is not None:
    st.session_state["sozlesme_kategori_filtre"] = preselected_category if preselected_category in ("ARAC", "EV") else "Tümü"
    st.session_state["sozlesme_arama"] = ""
    st.session_state["sozlesme_durum_filtre"] = ""

f1, f2, f3 = st.columns(3)
with f1:
    search = st.text_input("🔍 Sözleşme No / Müşteri Adı Ara:", key="sozlesme_arama").strip()
with f2:
    if locked_category:
        st.session_state["sozlesme_kategori_filtre"] = locked_category
        st.selectbox(
            "Kategori:",
            [locked_category],
            key="sozlesme_kategori_filtre",
            disabled=True,
            help="Departmanınıza göre sabit -- değiştirilemez.",
        )
        category_filter = locked_category
    else:
        category_filter = st.selectbox("Kategori:", ["Tümü", "ARAC", "EV"], key="sozlesme_kategori_filtre")
with f3:
    status_filter = st.text_input("Durum İçeriyor (opsiyonel, örn. AKTİF):", key="sozlesme_durum_filtre").strip()

params: dict = {}
if search:
    params["search"] = search
if category_filter != "Tümü":
    params["category"] = category_filter
if status_filter:
    params["status"] = status_filter

contracts = api_get("/contracts", params or None) or []
if not contracts:
    st.info("Aramanızla eşleşen sözleşme bulunamadı.")
    st.stop()

df = pd.DataFrame(contracts)
options = ["--- Seçiniz ---"] + [
    f"#{r['sozlesme_no']} [{r['kategori']}] - {r['musteri_adi']} ({r['sozlesme_durumu']})" for _, r in df.iterrows()
]
option_map = {
    f"#{r['sozlesme_no']} [{r['kategori']}] - {r['musteri_adi']} ({r['sozlesme_durumu']})": (r["sozlesme_no"], r["kategori"])
    for _, r in df.iterrows()
}

default_index = 0
if preselected_no is not None:
    target_no = str(preselected_no).strip()
    target_category = str(preselected_category).strip() if preselected_category else ""
    matched_idx = None
    fallback_idx = None
    for idx, opt in enumerate(options):
        mapped = option_map.get(opt)
        if not mapped:
            continue
        mapped_no, mapped_category = str(mapped[0]).strip(), str(mapped[1]).strip()
        if mapped_no == target_no and mapped_category == target_category:
            matched_idx = idx
            break
        if mapped_no == target_no and fallback_idx is None:
            fallback_idx = idx
    default_index = matched_idx if matched_idx is not None else (fallback_idx if fallback_idx is not None else 0)

selected = st.selectbox("Sözleşme Seçiniz:", options, index=default_index)

if default_index > 0:
    st.session_state.pop("secili_sozlesme_no", None)
    st.session_state.pop("secili_sozlesme_kategori", None)

if selected != "--- Seçiniz ---":
    sozlesme_no, kategori = option_map[selected]
    detail = api_get(f"/contracts/{sozlesme_no}", {"category": kategori})

    if detail:
        st.markdown("### 👤 Müşteri Bilgileri")
        c1, c2, c3 = st.columns(3)
        c1.write(f"**Ad Soyad:** {detail.get('musteri_adi', '-')}")
        c2.write(f"**Telefon:** {detail.get('musteri_telefon', '-')}")
        c3.write(f"**Kimlik No (TC/Pasaport):** {detail.get('musteri_tc', '-') or '-'}")
        if st.button("👥 Müşteri Sayfasında Detayları Gör"):
            st.session_state["secili_musteri_id"] = int(detail.get("musteri_id"))
            st.switch_page("app_pages/musteriler.py")

        st.markdown("### 🏷️ Sözleşme Özeti")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Toplam Tutar", f"₺{float(detail.get('total_kira') or 0):,.2f}")
        m2.metric("Ödenen", f"₺{float(detail.get('odenen_toplam_tutar') or 0):,.2f}")
        m3.metric("Kalan Borç", f"₺{float(detail.get('kalan_borc') or 0):,.2f}")
        m4.metric("Durum", detail.get("sozlesme_durumu", "-"))

        if kategori == "ARAC":
            st.info(
                f"🚗 **Araç:** {detail.get('marka_adi', '-')} {detail.get('model_adi', '-')} "
                f"— Plaka: {detail.get('plaka', '-')}"
            )
        else:
            st.info(
                f"🏠 **Konut:** {detail.get('apartman_adi', '-')} No:{detail.get('daire_no', '-')} "
                f"— {detail.get('il', '-')}/{detail.get('ilce', '-')}"
            )

        st.caption(
            f"📅 {detail.get('baslangic_tarihi', '-')} → {detail.get('bitis_tarihi', '-')}  |  "
            f"İşlemi Yapan: {detail.get('calisan_adi', '-')}"
        )

        can_extend_this = can_extend_arac if kategori == "ARAC" else can_extend_ev
        durum_str = str(detail.get("sozlesme_durumu") or "").upper()
        kapali = any(k in durum_str for k in ["TAMAMLA", "BITTI", "BİTTİ", "İPTAL", "IPTAL", "SİLİNDİ", "SILINDI"])
        if can_extend_this and not kapali:
            with st.expander("🗓️ Sözleşmeyi Uzat"):
                current_end = pd.to_datetime(detail.get("bitis_tarihi"), errors="coerce")
                if pd.isna(current_end):
                    st.warning("Mevcut bitiş tarihi okunamadı.")
                else:
                    current_end = current_end.date()
                    varlik = "araç" if kategori == "ARAC" else "daire"
                    st.caption(
                        f"Mevcut bitiş tarihi: **{current_end}**. Aynı {varlik} için bu tarihten sonrasına ait "
                        "başka bir sözleşme varsa uzatma engellenir."
                    )
                    new_end_date = st.date_input(
                        "Yeni Bitiş Tarihi:", value=current_end, min_value=current_end, key="sozlesmeler_uzatma_tarih"
                    )
                    if st.button("✅ Sözleşmeyi Uzat", key="sozlesmeler_uzatma_btn"):
                        if new_end_date <= current_end:
                            st.error("⚠️ Yeni bitiş tarihi mevcut bitiş tarihinden sonra olmalıdır.")
                        else:
                            endpoint = (
                                f"/vehicles/contracts/{sozlesme_no}/extend" if kategori == "ARAC"
                                else f"/housing/contracts/{sozlesme_no}/extend"
                            )
                            result = api_post(endpoint, {"new_end_date": str(new_end_date)})
                            if result and result.get("success"):
                                clear_cache()
                                st.session_state["toast_mesaj"] = result["message"]
                                st.session_state["toast_icon"] = "🗓️"
                                st.rerun()
                            elif result:
                                st.error(result["message"])

        st.markdown("---")

        @st.fragment
        def _docx_indirme_fragmani():
            if st.button("📝 Sözleşme Metnini Word (.docx) Olarak İndir"):
                doc_bytes = api_get_raw(f"/contracts/{sozlesme_no}/document", {"category": kategori})
                if doc_bytes:
                    st.session_state["sozlesme_docx"] = doc_bytes
            if st.session_state.get("sozlesme_docx"):
                st.download_button(
                    "⬇️ İndirilen Sözleşme Metnini Kaydet", data=st.session_state["sozlesme_docx"],
                    file_name=f"Sozlesme_{sozlesme_no}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="sozlesme_docx_indir",
                )

        _docx_indirme_fragmani()

        if kategori == "ARAC" and detail.get("soforler"):
            st.markdown("### 🧑‍✈️ Ek Şoförler")
            df_sofor = pd.DataFrame(detail["soforler"]).rename(
                columns={
                    "ad_soyad": "Ad Soyad", "telefon": "Telefon", "email": "E-posta",
                    "tc_kimlik_no": "Kimlik No (TC/Pasaport)", "sira": "Sıra",
                }
            )
            st.dataframe(df_sofor, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### 💳 Ödeme Geçmişi")
        odemeler = detail.get("odemeler", [])
        if not odemeler:
            st.info("Bu sözleşmeye ait henüz bir ödeme kaydı bulunmuyor.")
        else:
            df_odeme = pd.DataFrame(odemeler)
            display = df_odeme.rename(
                columns={
                    "odeme_id": "Ödeme ID", "odenen_tutar": "Tutar (₺)", "doviz_cinsi": "Döviz",
                    "odenen_tutar_doviz": "Döviz Tutarı", "kur": "Kur", "odeme_yontemi": "Ödeme Yöntemi",
                    "odeme_tarihi": "Tarih", "odeme_tipi": "İşlem Tipi", "aciklama": "Açıklama",
                }
            )
            st.dataframe(display, use_container_width=True, hide_index=True)

            st.markdown("#### 🧾 Makbuz")
            colA, colB = st.columns(2)

            with colA:
                st.markdown("**İstenilen tek bir ödemenin makbuzu**")
                odeme_secenekleri = [f"#{o['odeme_id']} - ₺{o['odenen_tutar']:,.2f} ({o['odeme_tarihi']})" for o in odemeler]
                odeme_id_map = {label: o["odeme_id"] for label, o in zip(odeme_secenekleri, odemeler)}
                tekil_secim = st.selectbox("Ödeme seçin:", odeme_secenekleri, key="tekil_makbuz_secim")

                @st.fragment
                def _tekil_makbuz_fragmani():
                    if st.button("📄 Seçili Ödemenin Makbuzunu İndir"):
                        odeme_id = odeme_id_map[tekil_secim]
                        invoice_bytes = api_get_raw(f"/contracts/payments/{odeme_id}/invoice")
                        if invoice_bytes:
                            st.session_state["tekil_makbuz"] = invoice_bytes
                            st.session_state["tekil_makbuz_id"] = odeme_id
                    if st.session_state.get("tekil_makbuz"):
                        st.download_button(
                            "⬇️ İndirilen Tekil Makbuzu Kaydet", data=st.session_state["tekil_makbuz"],
                            file_name=f"Makbuz_{st.session_state.get('tekil_makbuz_id')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="tekil_makbuz_indir",
                        )

                _tekil_makbuz_fragmani()

            with colB:
                st.markdown("**Tüm ödemelerin toplu makbuzu**")
                st.caption(" ")

                @st.fragment
                def _toplu_makbuz_fragmani():
                    if st.button("📚 Tüm Ödemelerin Makbuzunu Oluştur"):
                        invoice_bytes = api_get_raw(f"/contracts/{sozlesme_no}/bulk-invoice", {"category": kategori})
                        if invoice_bytes:
                            st.session_state["toplu_makbuz"] = invoice_bytes
                    if st.session_state.get("toplu_makbuz"):
                        st.download_button(
                            "⬇️ İndirilen Toplu Makbuzu Kaydet", data=st.session_state["toplu_makbuz"],
                            file_name=f"Toplu_Makbuz_{sozlesme_no}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="toplu_makbuz_indir",
                        )

                _toplu_makbuz_fragmani()
