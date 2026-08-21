"""HTTP client helpers for talking to the backend API from the Streamlit frontend."""
from typing import Any, Optional

import requests
import streamlit as st

from config import BACKEND_URL


def _headers() -> dict:
    """Build the Authorization header from the logged-in user's session token, if any."""
    token = st.session_state.get("access_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def verify_token(token: str) -> Optional[dict]:
    """Validate a token against the backend and return the user info, or None if invalid/expired."""
    try:
        resp = requests.get(
            f"{BACKEND_URL}/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if not resp.ok:
            return None
        return resp.json()
    except requests.exceptions.RequestException:
        return None


def api_get(path: str, params: Optional[dict] = None) -> Optional[Any]:
    """Issue a GET request to the backend, handling auth errors and displaying failures via Streamlit."""
    try:
        resp = requests.get(f"{BACKEND_URL}{path}", params=params, headers=_headers(), timeout=15)
        if resp.status_code == 401:
            st.session_state.clear()
            st.error("🔒 Oturumunuz sona erdi. Lütfen tekrar giriş yapın.")
            st.rerun()
        if resp.status_code == 403:
            detail = resp.json().get("detail", "Bu sayfayı görüntüleme yetkiniz bulunmamaktadır.") if resp.content else "Bu sayfayı görüntüleme yetkiniz bulunmamaktadır."
            st.error(f"🔒 {detail}")
            return None
        if not resp.ok:
            detail = resp.json().get("detail", "Bilinmeyen hata") if resp.content else "Bilinmeyen hata"
            st.error(f"❌ {detail}")
            return None
        return resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"❌ Sunucuya bağlanılamadı: {exc}")
        return None


@st.cache_data(ttl=300, show_spinner=False)
def _cached_get(path: str, params_tuple: tuple, token: str) -> Optional[Any]:
    """Perform a cached GET request; params are passed as a tuple since dicts aren't hashable for caching."""
    params = dict(params_tuple) if params_tuple else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.get(f"{BACKEND_URL}{path}", params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException:
        return None


def api_get_cached(path: str, params: Optional[dict] = None) -> Optional[Any]:
    """Return a cached GET response, falling back to a live api_get call if the cache yields nothing."""

    token = st.session_state.get("access_token", "")
    params_tuple = tuple(sorted(params.items())) if params else tuple()
    result = _cached_get(path, params_tuple, token)
    if result is None and token:
        return api_get(path, params)
    return result


def clear_cache() -> None:
    """Clear the cached GET responses; call this right after a mutation so subsequent reads are fresh."""
    _cached_get.clear()


def api_get_raw(path: str, params: Optional[dict] = None):
    """Issue a GET request and return the raw response bytes, e.g. for binary file downloads."""
    try:
        resp = requests.get(f"{BACKEND_URL}{path}", params=params, headers=_headers(), timeout=30)
        resp.raise_for_status()
        return resp.content
    except requests.exceptions.RequestException as exc:
        st.error(f"❌ Sunucuya bağlanılamadı: {exc}")
        return None


def api_post(path: str, json: Optional[dict] = None, timeout: int = 60) -> Optional[Any]:
    """Issue a POST request to the backend, handling auth/timeout errors and displaying failures via Streamlit."""
    try:
        resp = requests.post(f"{BACKEND_URL}{path}", json=json, headers=_headers(), timeout=timeout)
        if resp.status_code == 401:
            st.session_state.clear()
            st.error("🔒 Oturumunuz sona erdi. Lütfen tekrar giriş yapın.")
            st.rerun()
        if not resp.ok:
            detail = resp.json().get("detail", "Bilinmeyen hata") if resp.content else "Bilinmeyen hata"
            st.error(f"❌ {detail}")
            return None
        return resp.json()
    except requests.exceptions.Timeout:
        st.error(
            "⏳ Sunucudan beklenen sürede cevap gelmedi (işlem hâlâ arka planda devam ediyor olabilir). "
            "Birkaç saniye bekleyip sayfayı yenileyerek sonucu kontrol edebilirsiniz."
        )
        return None
    except requests.exceptions.RequestException as exc:
        st.error(f"❌ Sunucuya bağlanılamadı: {exc}")
        return None


def api_put(path: str, json: Optional[dict] = None) -> Optional[Any]:
    """Issue a PUT request to the backend, handling auth errors and displaying failures via Streamlit."""
    try:
        resp = requests.put(f"{BACKEND_URL}{path}", json=json, headers=_headers(), timeout=15)
        if resp.status_code == 401:
            st.session_state.clear()
            st.error("🔒 Oturumunuz sona erdi. Lütfen tekrar giriş yapın.")
            st.rerun()
        if not resp.ok:
            detail = resp.json().get("detail", "Bilinmeyen hata") if resp.content else "Bilinmeyen hata"
            st.error(f"❌ {detail}")
            return None
        return resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(f"❌ Sunucuya bağlanılamadı: {exc}")
        return None
