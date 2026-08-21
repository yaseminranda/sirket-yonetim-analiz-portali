"""Home.py - main Streamlit entry point: login (with 2FA), remember-me cookie
based persistent sessions, security-question setup and password-reset flows
(both gated behind an emailed one-time code), and the permission-aware home
page / sidebar navigation shown once a user is logged in."""

from datetime import datetime, timedelta, timezone

import extra_streamlit_components as stx
import streamlit as st

from api_client import api_get, api_post, verify_token

st.set_page_config(
    page_title="Şirket Yönetim & Analiz Portalı",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

SECURITY_QUESTIONS = [
    "İlk evcil hayvanınızın adı nedir?",
    "En sevdiğiniz ilkokul öğretmeninizin adı nedir?",
    "Doğduğunuz şehir neresidir?",
    "Annenizin kızlık soyadı nedir?",
    "En sevdiğiniz çocukluk arkadaşınızın adı nedir?",
]

# "Remember me": persistent login backed by a real browser cookie, not
# st.session_state (which only lives for the current browser tab/session and
# resets once the browser is fully closed and reopened). The cookie written
# below via CookieManager survives a browser restart, which is what makes
# "remember me" actually work across sessions.
REMEMBER_ME_COOKIE = "sirket_portal_remember_token"
REMEMBER_ME_DAYS = 30  # Must match settings.remember_me_expire_days in the backend


def get_cookie_manager() -> stx.CookieManager:
    """Create the CookieManager component instance used to read/write the remember-me cookie."""
    return stx.CookieManager(key="cookie_manager")


cookie_manager = get_cookie_manager()


def _clear_remember_cookie(component_key: str) -> None:
    """Delete the remember-me cookie.

    Deliberately does NOT use CookieManager's .delete() - if the cookie isn't
    already present in the component's internal cookie list (e.g. the user
    never checked "Remember Me", or it was already cleared), .delete() raises
    a KeyError and crashes the page (the library does `del self.cookies[cookie]`
    with no existence check). Instead we overwrite the cookie with an
    already-expired date via .set(), which the browser deletes immediately
    and which never errors regardless of whether the cookie existed before.
    """
    cookie_manager.set(
        REMEMBER_ME_COOKIE,
        "",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        key=component_key,
    )


if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False


def _restore_session_from_cookie() -> None:
    """Auto-login from the remember-me cookie on a fresh page load.

    When the page (re)loads - e.g. after the browser was closed and reopened
    - and there's no session in session_state, check whether a remember-me
    token cookie exists; if so, validate it against the backend (/auth/me)
    and restore the session automatically. Note: on the very first render,
    CookieManager may not have read the browser's cookies yet (the component
    runs asynchronously via JS), so `token` can come back None and the normal
    login screen is shown; once the cookie is found the component triggers a
    rerun on its own and the session gets restored then.
    """
    if st.session_state.get("logged_in"):
        return
    if st.session_state.get("_cookie_restore_denendi"):
        return

    token = cookie_manager.get(REMEMBER_ME_COOKIE)
    if not token:
        return

    st.session_state["_cookie_restore_denendi"] = True
    profile = verify_token(token)
    if profile and profile.get("success"):
        st.session_state["logged_in"] = True
        st.session_state["access_token"] = token
        st.session_state["user_id"] = profile["employee_id"]
        st.session_state["user_name"] = profile["user_name"]
        st.session_state["dept_id"] = profile["department_id"]
        st.session_state["yetki"] = profile["role"]
        st.rerun()
    else:
        # Token invalid/expired - clear the stale cookie, otherwise every
        # browser launch would silently retry a doomed validation.
        _clear_remember_cookie("clear_expired_remember_cookie")


_restore_session_from_cookie()


def show_login_page():
    """Render the logged-out screen: Login / Set Security Question / Reset Password tabs."""
    st.title("🏢 Şirket Yönetim & Analiz Portalı")
    st.divider()

    tab_login, tab_set_sec, tab_reset = st.tabs(
        ["🔐 Kullanıcı Girişi", "🛡️ Güvenlik Sorusu Tanımla", "🔑 Şifremi Unuttum / Kilit Aç"]
    )

    # Login (two-factor authentication): ID+password alone isn't enough to
    # log in - once the password is verified, a second step sends a one-time
    # code to the employee's registered email, and the session only opens
    # once that code is also verified. A pending verification is tracked in
    # session_state["_2fa_pending"].
    with tab_login:
        col1, _ = st.columns([1, 1])
        with col1:
            pending = st.session_state.get("_2fa_pending")

            if pending:
                st.subheader("🔑 Doğrulama Kodu")
                st.info(pending.get("info_message") or "E-posta adresinize gönderilen 6 haneli kodu girin.")
                code_input = st.text_input("Doğrulama Kodu", key="login_2fa_code", max_chars=6)

                col_v1, col_v2, col_v3 = st.columns(3)
                with col_v1:
                    if st.button("✅ Doğrula ve Giriş Yap", type="primary", use_container_width=True):
                        if code_input.strip():
                            verify_result = api_post(
                                "/auth/verify-code",
                                {
                                    "employee_id": pending["employee_id"],
                                    "code": code_input.strip(),
                                    "remember_me": pending["remember_me"],
                                },
                            )
                            if verify_result and verify_result.get("success"):
                                st.session_state["logged_in"] = True
                                st.session_state["access_token"] = verify_result["access_token"]
                                st.session_state["user_id"] = verify_result["employee_id"]
                                st.session_state["user_name"] = verify_result["user_name"]
                                st.session_state["dept_id"] = verify_result["department_id"]
                                st.session_state["yetki"] = verify_result["role"]
                                if pending["remember_me"]:
                                    # Deferred write: CookieManager is a browser
                                    # component (iframe), and the JS that actually
                                    # writes the cookie needs one normal render
                                    # cycle to run before it can execute. Calling
                                    # cookie_manager.set() immediately followed by
                                    # st.rerun() never gives the component that
                                    # chance, so the cookie was silently never
                                    # written (this was why "Remember Me" looked
                                    # checked but the session didn't survive closing
                                    # the browser). Fix: use the same "drop a flag,
                                    # do the real work on the NEXT turn" pattern
                                    # already used on logout - stash the cookie
                                    # value here and actually write it on the first
                                    # normal (non-rerun) render after the user lands
                                    # on the main panel (see the "LOGGED IN" block
                                    # below).
                                    st.session_state["_pending_remember_cookie"] = {
                                        "token": verify_result["access_token"],
                                        "expires_at": (
                                            datetime.now(timezone.utc) + timedelta(days=REMEMBER_ME_DAYS)
                                        ).isoformat(),
                                    }
                                st.session_state.pop("_2fa_pending", None)
                                st.success(verify_result["message"])
                                st.rerun()
                            elif verify_result:
                                st.error(verify_result["message"])
                        else:
                            st.warning("⚠️ Lütfen doğrulama kodunu giriniz.")
                with col_v2:
                    # Actually resends a new code (previously this button was
                    # labeled as doing so but only navigated back without
                    # sending anything). We don't re-ask for the password here
                    # because reaching this screen already required one
                    # successful password check; the password is kept only in
                    # this browser session's server-side session_state (inside
                    # `pending`) for the duration of this pending window, never
                    # persisted anywhere, and is discarded along with
                    # `_2fa_pending` on verify/cancel.
                    if st.button("🔁 Yeni Kod Gönder", use_container_width=True):
                        resend_result = api_post(
                            "/auth/login",
                            {
                                "employee_id": pending["employee_id"],
                                "password": pending["_cached_password"],
                                "remember_me": pending["remember_me"],
                            },
                        )
                        if resend_result and resend_result.get("success") and resend_result.get("requires_code"):
                            st.session_state["_2fa_pending"]["info_message"] = resend_result["message"]
                            st.success("✅ Yeni kod gönderildi.")
                            st.rerun()
                        elif resend_result:
                            st.error(resend_result["message"])
                with col_v3:
                    if st.button("◀️ Geri Dön", use_container_width=True):
                        st.session_state.pop("_2fa_pending", None)
                        st.rerun()
            else:
                st.subheader("Giriş Yap")
                employee_id = st.text_input("Çalışan ID (Örn: C1)", key="login_id").strip().upper()
                password = st.text_input("Şifre", type="password", key="login_pass")
                remember_me = st.checkbox(
                    "🕐 Beni Hatırla (tarayıcı kapansa bile oturumum açık kalsın)",
                    key="login_remember_me",
                )

                if st.button("Sisteme Giriş Yap", type="primary", use_container_width=True):
                    if employee_id and password:
                        result = api_post(
                            "/auth/login",
                            {"employee_id": employee_id, "password": password, "remember_me": remember_me},
                        )
                        if result and result.get("success") and result.get("requires_code"):
                            st.session_state["_2fa_pending"] = {
                                "employee_id": result.get("employee_id", employee_id),
                                "remember_me": remember_me,
                                "info_message": result["message"],
                                # Kept only so "Yeni Kod Gönder" (resend) can
                                # work without re-asking for the password - see
                                # the col_v2 note above.
                                "_cached_password": password,
                            }
                            st.rerun()
                        elif result and result.get("success"):
                            # Defensive: if requires_code never comes back, show
                            # an explicit error instead of falling through to a
                            # legacy single-step login flow.
                            st.error("Doğrulama kodu adımı başlatılamadı. Lütfen tekrar deneyin.")
                        elif result:
                            st.error(result["message"])
                    else:
                        st.warning("⚠️ Lütfen Çalışan ID ve Şifre alanlarını doldurun.")

    # Set Security Question: same two-step pattern as login 2FA - 1) the
    # password is verified and a code is emailed, nothing is saved yet;
    # 2) once the code is also verified, the security question is actually
    # saved. Previously this saved directly in a single step with no code.
    with tab_set_sec:
        st.subheader("İlk Kurulum: Güvenlik Sorusu Tanımla")

        secq_pending = st.session_state.get("_secq_pending")

        if secq_pending:
            st.info(secq_pending.get("info_message") or "E-posta adresinize gönderilen 6 haneli kodu girin.")
            secq_code = st.text_input("Doğrulama Kodu", key="secq_code_input", max_chars=6)

            col_s1, col_s2, col_s3 = st.columns(3)
            with col_s1:
                if st.button("✅ Doğrula ve Kaydet", type="primary", use_container_width=True, key="btn_secq_confirm"):
                    if secq_code.strip():
                        confirm_result = api_post(
                            "/auth/security-question/confirm",
                            {
                                "employee_id": secq_pending["employee_id"],
                                "current_password": secq_pending["current_password"],
                                "security_question": secq_pending["security_question"],
                                "security_answer": secq_pending["security_answer"],
                                "code": secq_code.strip(),
                            },
                        )
                        if confirm_result and confirm_result.get("success"):
                            st.session_state.pop("_secq_pending", None)
                            st.success(f"✅ {confirm_result['message']}")
                        elif confirm_result:
                            st.error(confirm_result["message"])
                    else:
                        st.warning("⚠️ Lütfen doğrulama kodunu giriniz.")
            with col_s2:
                if st.button("🔁 Yeni Kod Gönder", use_container_width=True, key="btn_secq_resend"):
                    resend_result = api_post(
                        "/auth/security-question/request-code",
                        {
                            "employee_id": secq_pending["employee_id"],
                            "current_password": secq_pending["current_password"],
                        },
                    )
                    if resend_result and resend_result.get("success"):
                        st.session_state["_secq_pending"]["info_message"] = resend_result["message"]
                        st.success("✅ Yeni kod gönderildi.")
                        st.rerun()
                    elif resend_result:
                        st.error(resend_result["message"])
            with col_s3:
                if st.button("◀️ Geri Dön", use_container_width=True, key="btn_secq_cancel"):
                    st.session_state.pop("_secq_pending", None)
                    st.rerun()
        else:
            sec_cid = st.text_input("Çalışan ID", key="sec_cid").strip().upper()
            sec_pass = st.text_input("Mevcut Şifre", type="password", key="sec_pass")
            sec_question = st.selectbox("Bir Güvenlik Sorusu Seçiniz", SECURITY_QUESTIONS, key="sec_soru_select")
            sec_answer = st.text_input("Cevabınız", type="password", key="sec_ans_input").strip()

            if st.button("📧 Kod Gönder", use_container_width=True, key="btn_secq_send_code"):
                if not sec_cid or not sec_pass or not sec_answer:
                    st.warning("⚠️ Lütfen tüm alanları doldurun.")
                else:
                    result = api_post(
                        "/auth/security-question/request-code",
                        {"employee_id": sec_cid, "current_password": sec_pass},
                    )
                    if result and result.get("success"):
                        st.session_state["_secq_pending"] = {
                            "employee_id": sec_cid,
                            "current_password": sec_pass,
                            "security_question": sec_question,
                            "security_answer": sec_answer,
                            "info_message": result["message"],
                        }
                        st.rerun()
                    elif result:
                        st.error(f"❌ {result['message']}")

    # Password Reset: same two-step pattern - the password is not actually
    # changed until the emailed code is verified.
    with tab_reset:
        st.subheader("Güvenlik Sorusu ile Şifre Sıfırlama & Kilit Açma")

        pwreset_pending = st.session_state.get("_pwreset_pending")

        if pwreset_pending:
            st.info(pwreset_pending.get("info_message") or "E-posta adresinize gönderilen 6 haneli kodu girin.")
            pwreset_code = st.text_input("Doğrulama Kodu", key="pwreset_code_input", max_chars=6)

            col_p1, col_p2, col_p3 = st.columns(3)
            with col_p1:
                if st.button("✅ Doğrula ve Şifreyi Güncelle", type="primary", use_container_width=True, key="btn_pwreset_confirm"):
                    if pwreset_code.strip():
                        confirm_result = api_post(
                            "/auth/reset-password/confirm",
                            {
                                "employee_id": pwreset_pending["employee_id"],
                                "security_answer": pwreset_pending["security_answer"],
                                "new_password": pwreset_pending["new_password"],
                                "code": pwreset_code.strip(),
                            },
                        )
                        if confirm_result and confirm_result.get("success"):
                            st.session_state.pop("_pwreset_pending", None)
                            st.success(f"✅ {confirm_result['message']}")
                        elif confirm_result:
                            st.error(confirm_result["message"])
                    else:
                        st.warning("⚠️ Lütfen doğrulama kodunu giriniz.")
            with col_p2:
                if st.button("🔁 Yeni Kod Gönder", use_container_width=True, key="btn_pwreset_resend"):
                    resend_result = api_post(
                        "/auth/reset-password/request-code",
                        {
                            "employee_id": pwreset_pending["employee_id"],
                            "security_answer": pwreset_pending["security_answer"],
                        },
                    )
                    if resend_result and resend_result.get("success"):
                        st.session_state["_pwreset_pending"]["info_message"] = resend_result["message"]
                        st.success("✅ Yeni kod gönderildi.")
                        st.rerun()
                    elif resend_result:
                        st.error(resend_result["message"])
            with col_p3:
                if st.button("◀️ Geri Dön", use_container_width=True, key="btn_pwreset_cancel"):
                    st.session_state.pop("_pwreset_pending", None)
                    st.rerun()
        else:
            reset_id = st.text_input("Çalışan ID", key="reset_id").strip().upper()

            if reset_id:
                question_result = api_get(f"/auth/security-question/{reset_id}")
                if question_result and question_result.get("found"):
                    st.info(f"❓ **Güvenlik Sorunuz:** {question_result['question']}")
                    entered_ans = st.text_input("Güvenlik Sorusu Cevabı", type="password", key="reset_ans")
                    new_password = st.text_input("Yeni Şifre", type="password", key="reset_new_p1")
                    new_password_confirm = st.text_input("Yeni Şifre (Tekrar)", type="password", key="reset_new_p2")

                    if st.button("📧 Kod Gönder", use_container_width=True, key="btn_pwreset_send_code"):
                        if not entered_ans or not new_password:
                            st.warning("⚠️ Lütfen tüm alanları doldurun.")
                        elif new_password != new_password_confirm:
                            st.error("❌ Yeni şifreler birbiriyle eşleşmiyor!")
                        else:
                            result = api_post(
                                "/auth/reset-password/request-code",
                                {"employee_id": reset_id, "security_answer": entered_ans},
                            )
                            if result and result.get("success"):
                                st.session_state["_pwreset_pending"] = {
                                    "employee_id": reset_id,
                                    "security_answer": entered_ans,
                                    "new_password": new_password,
                                    "info_message": result["message"],
                                }
                                st.rerun()
                            elif result:
                                st.error(f"❌ {result['message']}")
                elif question_result:
                    st.warning(f"⚠️ {question_result.get('message', 'Güvenlik sorusu bulunamadı.')}")


def show_home_page():
    """Render the post-login welcome screen and permission-aware usage guide.

    The bullet list below no longer shows the same fixed text to everyone: it
    reuses the same can_arac_analiz/can_arac_islemleri/can_ev_analiz/
    can_ev_islemleri/can_karsilastirma/can_tahmin variables that decide which
    pages appear in the sidebar (computed further below), so a user never sees
    a module here that they don't actually have access to, keeping this text
    always consistent with the sidebar. (This function is invoked via
    st.navigation() after those variables are computed, so they're accessible
    here as globals.)
    """
    st.title(f"👋 Hoş Geldiniz, {st.session_state.get('user_name', 'Kullanıcı')}")
    st.divider()

    st.markdown("### 📌 Sistem Kullanım Rehberi")
    st.markdown("Sol taraftaki menüyü kullanarak yetkiniz dahilindeki analiz sayfalarına erişebilirsiniz:")

    maddeler = [
        "**👥 Müşteriler & 📄 Sözleşmeler & 📲 Ödeme Hatırlatmaları:** Müşteri kayıtları, sözleşme "
        "detayları ve ödeme hatırlatmaları -- tüm çalışanlara açıktır.",
    ]
    if can_arac_analiz or can_arac_islemleri:
        maddeler.append(
            "**🚗 Araç Analiz & 🚘 Araç Kiralama:** Araç filosu, kiralama geçmişi ve ciro analizleri."
        )
    if can_ev_analiz or can_ev_islemleri:
        maddeler.append(
            "**🏠 Ev Analiz & 🏡 Ev Kiralama:** Gayrimenkul portföyü, kira sözleşmeleri ve doluluk oranları."
        )
    if can_karsilastirma or can_tahmin:
        maddeler.append("**📊 Karşılaştırma & Tahmin:** Departmanlar arası performans karşılaştırmaları.")

    st.markdown("\n".join(f"* {madde}" for madde in maddeler))

    st.markdown(
        "---\n"
        "*🛡️ **Güvenlik Notu:** İşleminiz bittiğinde sol menüdeki **'Güvenli Çıkış'** butonunu "
        "kullanmayı unutmayınız.*"
    )


# LOGGED OUT: minimal navigation containing only the login page.
if not st.session_state["logged_in"]:
    pg = st.navigation({"Giriş": [st.Page(show_login_page, title="Giriş Yap", icon="🔐", default=True)]})
    pg.run()

# LOGGED IN: main panel + full navigation.
else:
    # If "Remember Me" was checked and the 2FA code was verified on the
    # previous turn, a flag was left behind for us to process here, during a
    # normal (non-rerun) render, and actually write the cookie now - see the
    # _pending_remember_cookie explanation above.
    _pending_cookie = st.session_state.pop("_pending_remember_cookie", None)
    if _pending_cookie:
        cookie_manager.set(
            REMEMBER_ME_COOKIE,
            _pending_cookie["token"],
            expires_at=datetime.fromisoformat(_pending_cookie["expires_at"]),
            key="set_remember_cookie_deferred",
        )

    with st.sidebar:
        st.title("👤 Kullanıcı Profili")
        st.write(f"**Ad Soyad:** {st.session_state.get('user_name', '-')}")
        st.write(f"**Çalışan ID:** {st.session_state.get('user_id', '-')}")
        st.write(f"**Departman:** {st.session_state.get('dept_id', '-')}")
        st.write(f"**Yetki:** `{st.session_state.get('yetki', '-')}`")
        st.divider()

        # Logout is deliberately done in TWO steps. Deleting the cookie is
        # processed asynchronously through the CookieManager component, not
        # instantly in the browser. If we cleared the cookie, wiped
        # session_state, and reran the page all at once, the freshly started
        # page would hit `_restore_session_from_cookie()` at the top and still
        # find the cookie present (not yet actually deleted), silently
        # logging the user right back in - i.e. logout would appear to do
        # nothing. To avoid this: 1) on button click, only send the delete
        # command + leave a "logout started" flag + rerun (on this turn the
        # user still counts as logged in, so the auto-login check doesn't
        # kick in yet, giving the browser a full turn to process the delete);
        # 2) on the NEXT turn, seeing the flag, actually tear down the
        # session - by which point the cookie really has been deleted.
        if st.session_state.get("_logout_cikis_basladi"):
            st.session_state.clear()
            # session_state.clear() wipes everything, including the
            # _cookie_restore_denendi flag. Without re-setting it, the next
            # render's top-level _restore_session_from_cookie() would try to
            # read the cookie again - but the delete command sent a moment
            # ago may not have been processed by the browser yet (the same
            # kind of async delay as the remember-me set() above; delete() is
            # async too). If the cookie is still there, the user would be
            # instantly auto-logged back in, making "Güvenli Çıkış" (secure
            # logout) look like it did nothing. Setting this flag again here
            # deliberately prevents a retry within this tab - a real page
            # reload (new session/connection) starts this flag fresh, and by
            # then the cookie really has been deleted.
            st.session_state["_cookie_restore_denendi"] = True
            st.rerun()

        if st.button("🔴 Güvenli Çıkış", key="btn_logout", use_container_width=True):
            _clear_remember_cookie("clear_remember_cookie_on_logout")
            st.session_state["_logout_cikis_basladi"] = True
            st.rerun()

    # Sidebar menu only shows pages the user is permitted to use. These
    # checks intentionally mirror the same permission logic each page also
    # enforces independently on the backend - they're duplicated here purely
    # to decide what to show/hide in the menu; each page's own `st.stop()`
    # guard remains as defense in depth (in case someone navigates straight
    # to a page URL they know).
    user_role = str(st.session_state.get("yetki", "")).upper().strip()
    user_dept = str(st.session_state.get("dept_id", "")).upper().strip()
    all_session_values = " ".join([str(v) for v in st.session_state.values()]).upper()

    is_genel_mudur = any(r in user_role for r in ["GENEL MÜDÜR", "GENEL MUDUR"])
    is_genel_mudur_d3 = is_genel_mudur and (user_dept in ["D3", "3"])
    is_arac_dep_muduru = any(r in user_role for r in ["DEPARTMAN MÜDÜRÜ", "DEPARTMAN MUDURU"]) and (user_dept in ["D2", "2"])
    is_ev_dep_muduru = any(r in user_role for r in ["DEPARTMAN MÜDÜRÜ", "DEPARTMAN MUDURU"]) and (user_dept in ["D1", "1"])
    is_arac_departmani = "D2" in all_session_values
    is_ev_departmani = "D1" in all_session_values

    can_karsilastirma = is_genel_mudur_d3
    # Fix: the three lines below used to use the department-agnostic
    # `is_genel_mudur`, meaning ANY user whose role contained "Genel Müdür"
    # (General Manager) - e.g. the General Manager of the Ev/housing
    # department - could see the Araç (vehicle), Ev (housing) *and* Tahmin
    # (forecast) pages in the menu regardless of their own department. Made
    # consistent with Karşılaştırma (comparison) by using `is_genel_mudur_d3`
    # (role AND department D3/company-wide) instead - only the actual
    # company-wide General Manager sees all of them.
    can_tahmin = is_genel_mudur_d3
    can_arac_analiz = is_genel_mudur_d3 or is_arac_dep_muduru
    can_ev_analiz = is_genel_mudur_d3 or is_ev_dep_muduru
    can_arac_islemleri = is_genel_mudur_d3 or is_arac_departmani
    can_ev_islemleri = is_genel_mudur_d3 or is_ev_departmani

    genel_paneller = [st.Page(show_home_page, title="Ana Sayfa", icon="🏠", default=True)]
    if can_karsilastirma:
        genel_paneller.append(st.Page("app_pages/karsilastirma.py", title="Genel Karşılaştırma", icon="📊"))
    if can_tahmin:
        genel_paneller.append(st.Page("app_pages/tahmin.py", title="Yapay Zekâ & Tahmin", icon="🔮"))

    pages = {
        "Genel Paneller": genel_paneller,
        # Customers / Contracts / Reminders are open to all employees (per
        # project planning decision) - no department/role restriction.
        "Müşteri & Sözleşme": [
            st.Page("app_pages/musteriler.py", title="Müşteriler", icon="👥"),
            st.Page("app_pages/sozlesmeler.py", title="Sözleşmeler", icon="📄"),
            st.Page("app_pages/hatirlatmalar.py", title="Ödeme Hatırlatmaları", icon="📲"),
        ],
    }

    if can_arac_analiz or can_arac_islemleri:
        arac_pages = []
        if can_arac_analiz:
            arac_pages.append(st.Page("app_pages/arac_analiz.py", title="Araç Analiz Paneli", icon="🚗"))
        if can_arac_islemleri:
            arac_pages.extend(
                [
                    st.Page("app_pages/arac_kiralama.py", title="Araç Kiralama İşlemleri", icon="🔑"),
                    st.Page("app_pages/arac_odeme_yonetimi.py", title="Araç Ödeme Yönetimi", icon="💳"),
                    st.Page("app_pages/arac_ekle.py", title="Araç Filosu Yönetimi", icon="➕"),
                    st.Page("app_pages/arac_degistir.py", title="Araç Değişimi", icon="🔄"),
                ]
            )
        pages["Araç İşlemleri"] = arac_pages

    if can_ev_analiz or can_ev_islemleri:
        ev_pages = []
        if can_ev_analiz:
            ev_pages.append(st.Page("app_pages/ev_analiz.py", title="Ev Analiz Paneli", icon="🏠"))
        if can_ev_islemleri:
            ev_pages.extend(
                [
                    st.Page("app_pages/ev_kiralama.py", title="Ev Kiralama İşlemleri", icon="🔑"),
                    st.Page("app_pages/ev_odeme_yonetimi.py", title="Ev Ödeme Yönetimi", icon="💳"),
                    st.Page("app_pages/ev_ekle.py", title="Konut Portföyü Yönetimi", icon="➕"),
                ]
            )
        pages["Emlak / Ev İşlemleri"] = ev_pages

    pg = st.navigation(pages)
    pg.run()
