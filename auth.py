# -*- coding: utf-8 -*-
"""auth.py — Autenticación de TerpsXHunter (extraído de app_streamlit, Paso 3
de la auditoría: desacoplar el monolito).

Contiene SOLO lógica de autenticación:
  - Contraseñas: PBKDF2-SHA256 + salt (nunca en claro).
  - Sesión persistente: cookie firmada HMAC-SHA256 (30 días).
  - Google OAuth 2.0 (Authorization Code + PKCE) contra Google directamente
    (la app NO usa Supabase Auth, solo Postgres).

Dependencias: app_datos (core) + streamlit. NO importa app_streamlit, así que
no hay ciclos de importación. Los handlers de nivel superior que necesitan
`guardar()` (flujo completo de datos) se quedan en app_streamlit.
"""
import os
import json
import time
import base64
import hashlib
import secrets
import urllib.parse
import urllib.request

import streamlit as st

import app_datos as core

# -----------------------------------------------------------------------------
# Contraseñas (PBKDF2 + salt)
# -----------------------------------------------------------------------------


def hash_password(contrasena: str) -> str:
    """Devuelve 'salt$hash' (PBKDF2-SHA256, 100k iteraciones)."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"),
                            salt.encode("utf-8"), 100_000).hex()
    return f"{salt}${h}"


def verificar_password(contrasena: str, hash_guardado: str) -> bool:
    if not hash_guardado or "$" not in hash_guardado:
        return False
    salt, h = hash_guardado.split("$", 1)
    calc = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"),
                               salt.encode("utf-8"), 100_000).hex()
    return secrets.compare_digest(calc, h)


def perfiles_sin_password(datos: dict) -> list:
    """Perfiles antiguos que aún no tienen contraseña (se pueden reclamar)."""
    return [p["nombre"] for p in datos["perfiles"] if not p.get("password_hash")]


# -----------------------------------------------------------------------------
# Sesión persistente (cookie "recordar sesión") + Google OAuth
# -----------------------------------------------------------------------------

COOKIE_SESION = "terpsx_sesion"
COOKIE_DIAS = 30
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Sesión persistente: cookie "recordar sesión" (JS del navegador; la firma
# HMAC del token hace que no se pueda forjar sin el secreto del servidor).
try:
    from streamlit_cookies_controller import CookieController
    _COOKIES = CookieController()
except Exception:  # pragma: no cover
    _COOKIES = None


def _secret_sesion() -> str:
    """Secreto para firmar cookies: st.secrets['session']['secret'] o env."""
    try:
        s = getattr(st, "secrets", None)
        if s is not None:
            bloque = dict(s["session"]) if "session" in s else {}
            if bloque.get("secret"):
                return str(bloque["secret"]).strip()
    except Exception:
        pass
    return os.environ.get("SESSION_SECRET", "").strip()


def crear_token_sesion(perfil_id: str) -> str:
    """Token firmado (HMAC-SHA256) para la cookie 'recordar sesión'."""
    return core.crear_token_sesion(perfil_id, _secret_sesion(), COOKIE_DIAS)


def verificar_token_sesion(token: str):
    """Devuelve el perfil_id si el token es válido (firma + no expirado)."""
    return core.verificar_token_sesion(token, _secret_sesion())


def base_url_app() -> str:
    """URL base pública de la app (para el redirect_uri del OAuth)."""
    try:
        cabeceras = st.context.headers
        host = cabeceras.get("Host") or cabeceras.get("host") or "localhost:8501"
        proto = (cabeceras.get("X-Forwarded-Proto")
                 or cabeceras.get("x-forwarded-proto") or "")
        if not proto:
            proto = "https" if "streamlit.app" in host else "http"
        return f"{proto}://{host}"
    except Exception:
        return "http://localhost:8501"


def leer_cookie() -> str:
    """Lee la cookie de sesión (vacío si no hay / librería no disponible)."""
    if _COOKIES is None:
        return ""
    try:
        valor = _COOKIES.get(COOKIE_SESION)
        return str(valor) if valor else ""
    except Exception:
        return ""


def escribir_cookie(token: str):
    if _COOKIES is None or not token:
        return
    try:
        _COOKIES.set(COOKIE_SESION, token, max_age=COOKIE_DIAS * 86400,
                     same_site="Lax", secure=base_url_app().startswith("https"),
                     path="/")
    except Exception:
        pass


def borrar_cookie():
    if _COOKIES is None:
        return
    try:
        _COOKIES.remove(COOKIE_SESION)
    except Exception:
        try:
            _COOKIES.set(COOKIE_SESION, "", max_age=0, path="/")
        except Exception:
            pass


def auto_login_cookie(datos: dict):
    """Si hay cookie válida y nadie ha entrado, abre sesión automáticamente."""
    if st.session_state.get("usuario"):
        return
    token = leer_cookie()
    perfil_id = verificar_token_sesion(token)
    if not perfil_id:
        if token:  # token corrupto o expirado: limpiar la cookie
            borrar_cookie()
        return
    perfil = next((p for p in datos.get("perfiles", [])
                   if p.get("id") == perfil_id), None)
    if perfil is None:
        borrar_cookie()
        return
    st.session_state["usuario"] = perfil["nombre"]
    st.session_state["pagina"] = "📦 Catálogo"


# ----------------------------- Google OAuth --------------------------------


def credenciales_google() -> dict:
    """Client ID/Secret de Google: st.secrets['google_oauth'] o .env_google_oauth."""
    try:
        s = getattr(st, "secrets", None)
        if s is not None and "google_oauth" in s:
            bloque = dict(s["google_oauth"])
            if bloque.get("client_id") and bloque.get("client_secret"):
                return bloque
    except Exception:
        pass
    try:  # archivo local (mismo patrón que .env_db_password)
        ruta = os.path.join(core.RUTA_DIR, ".env_google_oauth")
        with open(ruta, encoding="utf-8") as f:
            cfg = {}
            for linea in f:
                if "=" in linea:
                    k, v = linea.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
        if cfg.get("client_id") and cfg.get("client_secret"):
            return cfg
    except OSError:
        pass
    return {}


def url_autorizacion_google() -> str:
    """URL de Google con PKCE (S256). El code_verifier vive en session_state."""
    cfg = credenciales_google()
    if not cfg:
        return ""
    verifier = secrets.token_urlsafe(64)
    st.session_state["oauth_verifier"] = verifier
    st.session_state["oauth_state"] = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()).rstrip(b"=").decode()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": base_url_app() + "/",
        "response_type": "code",
        "scope": "openid email profile",
        "state": st.session_state["oauth_state"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def intercambiar_codigo_google(code: str) -> dict:
    """Cambia el code por tokens y devuelve la info del usuario de Google."""
    cfg = credenciales_google()
    verifier = st.session_state.get("oauth_verifier", "")
    if not cfg or not verifier:
        return {}
    cuerpo = urllib.parse.urlencode({
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": base_url_app() + "/",
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL, data=cuerpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tokens = json.loads(r.read().decode())
    access = tokens.get("access_token")
    if not access:
        return {}
    req2 = urllib.request.Request(
        GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req2, timeout=30) as r2:
        return json.loads(r2.read().decode())
