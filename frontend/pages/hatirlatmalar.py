import pandas as pd
import streamlit as st

from api_client import api_get, api_post

if not st.session_state.get("logged_in", False):
    st.error("🔒 Bu sayfayı görüntülemek için lütfen önce giriş yapınız!")
    st.stop()

st.title("📲 Ödeme / Taksit Hatırlatmaları")
st.caption(
    "Madde 8 & 5. Aşama: Ödenmemiş borcu olan tüm araç ve ev sözleşmeleri (taksit planı olanlarda en yakın "
    "vadesi gelen taksit esas alınır) burada listelenir. Hatırlatmalar hem bu sayfadan manuel olarak hem de "
    "sistem tarafından her gün otomatik olarak (09:00) gönderilir."
)
st.caption(
    "📧 E-posta ile gönderim gerçekten çalışır (kurulu SMTP altyapısı üzerinden). "
    "📲 SMS ile gönderim, gerçek bir SMS sağlayıcısı sisteme bağlanana kadar bilgilendirici bir mesajla sonuçlanır."
)
st.divider()

if "toast_mesaj" in st.session_state:
    st.toast(st.session_state["toast_mesaj"], icon=st.session_state.get("toast_icon", "✅"))
    del st.session_state["toast_mesaj"]
    st.session_state.pop("toast_icon", None)

reminders = api_get("/finance/reminders") or []

if not reminders:
    st.success("🎉 Şu anda ödenmemiş borcu olan bir sözleşme bulunmuyor.")
    st.stop()

df = pd.DataFrame(reminders)
df["vade_tarihi_dt"] = pd.to_datetime(df["vade_tarihi"], errors="coerce")
today = pd.to_datetime("today").normalize()
df["gecikme_gun"] = (today - df["vade_tarihi_dt"]).dt.days

m1, m2, m3 = st.columns(3)
m1.metric("Toplam Borçlu Sözleşme", f"{len(df)} Adet")
m2.metric("Vadesi Geçmiş", f"{int((df['gecikme_gun'] > 0).sum())} Adet")
m3.metric("Toplam Bekleyen Tutar", f"₺{df['kalan_borc'].astype(float).sum():,.2f}")

st.markdown("---")

bulk_col1, bulk_col2 = st.columns(2)
with bulk_col1:
    if st.button("📧 Tüm Listeye E-posta ile Gönder", type="primary", use_container_width=True):
        result = api_post("/finance/reminders/send-all?method=email")
        if result and result.get("success"):
            st.session_state["toast_mesaj"] = result["message"]
            st.session_state["toast_icon"] = "📧"
            st.rerun()
        elif result:
            st.info(result["message"])
with bulk_col2:
    if st.button("📲 Tüm Listeye SMS ile Gönder", type="secondary", use_container_width=True):
        result = api_post("/finance/reminders/send-all?method=sms")
        if result and result.get("success"):
            st.session_state["toast_mesaj"] = result["message"]
            st.session_state["toast_icon"] = "📲"
            st.rerun()
        elif result:
            st.info(result["message"])

st.markdown("---")
st.markdown("### 📋 Borçlu Sözleşme Listesi")

display_df = df.rename(columns={
    "sozlesme_no": "Sözleşme No", "kategori": "Kategori", "musteri_adi": "Müşteri",
    "musteri_telefon": "Telefon", "vade_tarihi": "Vade Tarihi", "kalan_borc": "Kalan Borç (₺)",
    "gecikme_gun": "Gecikme (Gün)",
})
st.dataframe(
    display_df[["Sözleşme No", "Kategori", "Müşteri", "Telefon", "Vade Tarihi", "Kalan Borç (₺)", "Gecikme (Gün)"]],
    use_container_width=True, hide_index=True,
)

st.markdown("---")
st.markdown("### 📤 Tekil Hatırlatma Gönder")
options = ["--- Seçiniz ---"] + [
    f"#{r['sozlesme_no']} [{r['kategori']}] - {r['musteri_adi']} (Borç: ₺{float(r['kalan_borc']):,.2f})" for r in reminders
]
option_map = {
    f"#{r['sozlesme_no']} [{r['kategori']}] - {r['musteri_adi']} (Borç: ₺{float(r['kalan_borc']):,.2f})": (r["sozlesme_no"], r["kategori"])
    for r in reminders
}
selected = st.selectbox("Sözleşme Seçiniz:", options, key="tekil_hatirlatma_sec")
if selected != "--- Seçiniz ---":
    sozlesme_no, kategori = option_map[selected]
    tekil_col1, tekil_col2 = st.columns(2)
    with tekil_col1:
        if st.button("📧 E-posta ile Gönder", key="tekil_hatirlatma_email_btn", use_container_width=True):
            result = api_post(f"/finance/reminders/send/{sozlesme_no}?category={kategori}&method=email")
            if result and result.get("success"):
                st.session_state["toast_mesaj"] = result["message"]
                st.session_state["toast_icon"] = "📧"
                st.rerun()
            elif result:
                st.info(result["message"])
    with tekil_col2:
        if st.button("📲 SMS ile Gönder", key="tekil_hatirlatma_sms_btn", use_container_width=True):
            result = api_post(f"/finance/reminders/send/{sozlesme_no}?category={kategori}&method=sms")
            if result and result.get("success"):
                st.session_state["toast_mesaj"] = result["message"]
                st.session_state["toast_icon"] = "📲"
                st.rerun()
            elif result:
                st.info(result["message"])
