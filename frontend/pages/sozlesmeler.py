import pandas as pd
import streamlit as st

from api_client import api_get, api_get_raw

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

# Not: Bu sayfa SADECE görüntüleme + makbuz amaçlıdır. Sözleşme
# tamamlama/iptal etme/teslim alma gibi işlemler kendi departman
# sayfalarında (Araç Kiralama İşlemleri / Ev Kiralama İşlemleri) kalmaya
# devam ediyor -- proje planlama konuşmasındaki karar.

st.title("📄 Sözleşmeler")
st.caption("Araç ve ev sözleşmelerini tek yerden görüntüleyin, ödeme detaylarını inceleyin ve makbuz indirin.")
st.divider()

# Müşteriler sayfasından ya da kiralama sayfalarındaki "Tüm Sözleşmeler"
# tablosundan yönlendirme ile buraya gelinmişse ilgili sözleşmeyi öne getir.
print(f"[TESHIS][sozlesmeler] SCRIPT CALISTI -- pop ONCESI session_state icinde 'secili_sozlesme_no' var mi: {'secili_sozlesme_no' in st.session_state}, deger={st.session_state.get('secili_sozlesme_no')!r}", flush=True)
preselected_no = st.session_state.pop("secili_sozlesme_no", None)
preselected_category = st.session_state.pop("secili_sozlesme_kategori", None)
print(f"[TESHIS][sozlesmeler] pop SONRASI preselected_no={preselected_no!r} preselected_category={preselected_category!r}", flush=True)

# Madde 1 düzeltmesi: "Sözleşme Detayına Git" ile yönlendirildiğinde,
# aşağıdaki arama/kategori/durum filtreleri bir ÖNCEKİ ziyaretten kalma
# değerlerini (session_state'te key= ile kalıcı tutuluyor) koruyordu -- örn.
# kategori filtresi "EV" bırakılmışken bir ARAÇ sözleşmesine "git" denildiğinde,
# backend'den sadece EV sözleşmeleri istendiği için hedef sözleşme listede hiç
# görünmüyor, bu yüzden seçili gelmiyordu. Çözüm: yönlendirmeyle gelindiğinde
# kategori filtresini hedef sözleşmenin kategorisine göre otomatik ayarlıyoruz
# ve arama/durum filtrelerini temizliyoruz ki hedef sözleşme listeden asla
# düşmesin (widget oluşturulmadan ÖNCE session_state'e yazılıyor, bkz. aşağıdaki
# selectbox'taki index= yerine session_state ataması ile aynı desen).
if preselected_no is not None:
    st.session_state["sozlesme_kategori_filtre"] = preselected_category if preselected_category in ("ARAC", "EV") else "Tümü"
    st.session_state["sozlesme_arama"] = ""
    st.session_state["sozlesme_durum_filtre"] = ""

f1, f2, f3 = st.columns(3)
with f1:
    search = st.text_input("🔍 Sözleşme No / Müşteri Adı Ara:", key="sozlesme_arama").strip()
with f2:
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

if preselected_no is not None:
    # Not: burada BİLEREK index= parametresi kullanılmıyor. Streamlit'te bir
    # selectbox'a hem key= hem de index= verilirse, widget'ın session_state'te
    # ÖNCEKİ bir çalıştırmadan kalma değeri varsa (örn. sayfa daha önce
    # ziyaret edilip başka bir sözleşme seçilmişse) Streamlit index='i
    # sessizce yok sayıp eski değeri gösteriyordu -- "Sözleşme Detayına Git"
    # butonuyla başka bir sözleşmeye yönlendirildiğinde ekranın hep eski
    # seçili sözleşmede takılı kalmasının sebebi buydu. Çözüm: session_state
    # değerini widget oluşturulmadan ÖNCE doğrudan doğru seçeneğe yazıp
    # index= parametresini hiç kullanmamak (Streamlit'in resmi olarak
    # desteklediği "widget değerini koddan ayarlama" yöntemi).
    #
    # 2. düzeltme: eşleştirme artık str() ile yapılıyor -- kaynak sayfadaki
    # (arac_kiralama.py / ev_kiralama.py) sözleşme no değeri (pandas
    # .tolist() ile native int/str) ile buradaki /contracts uç noktasından
    # gelen değer (JSON üzerinden -- backend'de int, float ya da str olarak
    # serileşebilir) tam olarak AYNI Python tipinde olmayabiliyordu (örn.
    # 123 (int) ile "123" (str) ya da 123.0 (float) birbirine == ile eşit
    # çıkmayabiliyor) -- bu da kategori doğru seçiliyken bile sözleşmenin
    # hiç eşleşmemesine ve dropdown'ın "--- Seçiniz ---" da kalmasına sebep
    # oluyordu. str() dönüşümü bu tip farkını ortadan kaldırır.
    target_no = str(preselected_no).strip()
    target_category = str(preselected_category).strip() if preselected_category else ""
    matched_opt = None
    fallback_opt = None
    for opt in options:
        mapped = option_map.get(opt)
        if not mapped:
            continue
        mapped_no, mapped_category = str(mapped[0]).strip(), str(mapped[1]).strip()
        if mapped_no == target_no and mapped_category == target_category:
            matched_opt = opt
            break
        if mapped_no == target_no and fallback_opt is None:
            # Kategori eşleşmese bile sözleşme no'su eşleşen bir seçenek varsa
            # yedek olarak tutulur (kategori string'inde beklenmeyen bir fark
            # olsa bile kullanıcı yine de doğru sözleşmeye yönlendirilsin diye).
            fallback_opt = opt
    st.session_state["sozlesme_secim"] = matched_opt or fallback_opt or "--- Seçiniz ---"

    # GEÇİCİ TEŞHİS PANELİ -- KALICI (sticky) hale getirildi: Streamlit bu
    # sayfada bazen bu run'dan hemen sonra ek bir rerun tetikleyebiliyor (örn.
    # kategori filtresi widget'ının session_state'i programatik olarak
    # değiştirildiği için), ve preselected_no o ek run'da zaten pop'landığı
    # için None olur -- eski (gated) teşhis paneli o ek run'da kayboluyordu.
    # Bu yüzden teşhis bilgisi artık ayrı, POP EDİLMEYEN bir session_state
    # anahtarına kaydediliyor ve session temizlenene kadar her run'da
    # gösterilmeye devam ediyor.
    st.session_state["_debug_sozlesme_git_sticky"] = {
        "preselected_no": repr(preselected_no),
        "preselected_no_type": type(preselected_no).__name__,
        "preselected_category": repr(preselected_category),
        "target_no": repr(target_no),
        "target_category": repr(target_category),
        "matched_opt": repr(matched_opt),
        "fallback_opt": repr(fallback_opt),
        "assigned_sozlesme_secim": repr(st.session_state["sozlesme_secim"]),
        "toplam_secenek_sayisi": len(option_map),
        "ilk_15_no_kategori": [(str(v[0]), str(v[1])) for v in list(option_map.values())[:15]],
    }

_debug_info = st.session_state.get("_debug_sozlesme_git_sticky")
if _debug_info:
    with st.expander("🔧 Geçici Teşhis Bilgisi (test amaçlı, kalıcı)", expanded=True):
        st.caption(f"Bu bilgi şu an bu run'da preselected_no = {preselected_no!r} (None ise bu run'da yeni bir yönlendirme YOK demektir, aşağıdaki veri ÖNCEKİ bir run'dan kalma).")
        for k, v in _debug_info.items():
            st.write(f"**{k}**:", v)
        if st.button("🗑️ Teşhis Bilgisini Temizle", key="btn_clear_debug_sozlesme"):
            st.session_state.pop("_debug_sozlesme_git_sticky", None)
            st.rerun()

selected = st.selectbox("Sözleşme Seçiniz:", options, key="sozlesme_secim")

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
            st.switch_page("pages/musteriler.py")

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

        st.markdown("---")
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

            with colB:
                st.markdown("**Tüm ödemelerin toplu makbuzu**")
                st.caption(" ")
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
