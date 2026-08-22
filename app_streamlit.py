# -*- coding: utf-8 -*-
"""
===============================================================================
 SISTEMA DE CATAS — WEB (Streamlit)  ·  v1.1 Web
===============================================================================
 Aplicación web mobile-first (Streamlit) para registro y rankings de catas,
 accesible desde el móvil vía Tailscale o túnel Cloudflare.

 ARQUITECTURA:
   - CAPA DE DATOS  INTACTA: se importa `app_catas` como módulo base (core).
     Persistencia en C:/Users/Yunes/Desktop/Catas/catas.json, fotos en
     ./imagenes/, migración v1->v3, multi-voto, tolerancia a fallos del JSON
     y toda la matemática de ponderación se reutilizan SIN MODIFICARLAS.
     Único ajuste en runtime: el aviso de JSON corrupto (messagebox de
     tkinter) se redirige a la interfaz de Streamlit.
   - CAPA DE VISTAS  NUEVA: frontend Streamlit con login por contraseña,
     rangos (invitado / sesión / gente de confianza / admin), 7 secciones
     (Catálogo, Nueva Cata, Por votar, Productores, Rankings, Evolución,
     Perfiles), comentarios de confianza y valoración profesional.

 Ponderaciones (idénticas al original):
   Visual 25% · Aroma 15% · Sabor 45% · Efectos 15% · Nota sobre 100.

 Ejecutar:  streamlit run app_streamlit.py   (o arrancar_web.py / panel.bat)
 Acceso móvil: http://100.67.184.36:8501 (Tailscale) o túnel Cloudflare.
===============================================================================
"""

import os
import io
import json
import shutil
import hashlib
import secrets
import base64
import textwrap
import urllib.parse
import urllib.request
import html as _html
from datetime import datetime
import time

import pandas as pd
import streamlit as st

# matplotlib para gráficos con degradado (verde→violeta cósmico). Import lazy con
# fallback: si no está disponible los charts usan la vía nativa de Streamlit.
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap as _LSC
    _HAY_MPL = True
except Exception:
    _HAY_MPL = False

# Sesión persistente: cookie "recordar sesión" (JS del navegador; la firma
# HMAC del token hace que no se pueda forjar sin el secreto del servidor).
try:
    from streamlit_cookies_controller import CookieController
    _COOKIES = CookieController()
except Exception:  # pragma: no cover
    _COOKIES = None

# =============================================================================
# CAPA DE DATOS (intacta) — se importa la lógica y persistencia del original
# =============================================================================
# Backend puro (app_datos) sin dependencias de tkinter: funciona en la nube.
try:
    import app_datos as core
except ImportError as e:
    st.error(f"No se pudo importar la capa de datos 'app_datos.py': {e}")
    st.stop()

# En Streamlit no hay tkinter: el aviso de datos corruptos se muestra en la UI.
core._avisar_error = lambda *args, **kwargs: st.error(
    args[0] if args else "Error de datos")

st.set_page_config(
    page_title="TerpsXHunter",
    page_icon="assets/favicon.png",
    layout="wide",
    initial_sidebar_state="collapsed",  # mobile-first: sidebar colapsado al abrir
)

# =============================================================================
# HELPERS
# =============================================================================

# --- Backups automáticos -----------------------------------------------------
BACKUP_DIR = os.path.join(core.RUTA_DIR, "backups")
MAX_BACKUPS = 15  # rotación: conservar solo los 15 más recientes


def _backup_antes_de_guardar():
    """Copia catas.json a backups/catas_backup_YYYYMMDD_HHMMSS.json ANTES de
    sobrescribir, conservando así la versión anterior (permite revertir).
    Nunca debe romper la escritura principal: cualquier fallo se ignora."""
    if not os.path.exists(core.DB_FILE):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = os.path.join(BACKUP_DIR, f"catas_backup_{ts}.json")
        n = 1
        while os.path.exists(destino):  # evita colisión en el mismo segundo
            destino = os.path.join(BACKUP_DIR, f"catas_backup_{ts}_{n}.json")
            n += 1
        shutil.copy2(core.DB_FILE, destino)
        _rotar_backups()
    except OSError:
        pass


def _rotar_backups():
    """Borra los backups más antiguos dejando solo los últimos MAX_BACKUPS."""
    try:
        archivos = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith("catas_backup_") and f.endswith(".json")
        )
        while len(archivos) > MAX_BACKUPS:
            os.remove(os.path.join(BACKUP_DIR, archivos.pop(0)))
    except OSError:
        pass


@st.cache_data(ttl=120, show_spinner=False)
def cargar() -> dict:
    """Lee los datos UNA vez (caché 2 min) desde Supabase o catas.json.
    - TTL 120s: refresca automáticamente los datos escritos por OTROS usuarios
      sin esperar a que alguien guarde (multi-usuario, más frescura).
    - Se invalida al instante en cada guardar() (nunca datos stale tras escribir).
    - st.cache_data devuelve una COPIA por sesión: mutar el dict no contamina
      la caché ni a otras sesiones (verificado en laboratorio)."""
    return core.cargar_datos()


def guardar(datos: dict) -> bool:
    """Persiste los datos: backup del estado previo + escritura + invalida caché.

    Respaldo/robustez (mejora backend):
      - Reintenta UNA vez ante errores transitorios (pooler/red) y solo limpia
        la caché en éxito, así la UI nunca se queda con datos "fantasma".
      - Devuelve True/False y, si falla, muestra un toast de error para que el
        usuario sepa que debe reintentar (antes el fallo pasaba callado)."""
    _backup_antes_de_guardar()
    intentos = 0
    while True:
        try:
            core.guardar_datos(datos)
            break
        except core.ErrorVersionAntigua:
            # Otro usuario escribió desde que cargamos: NO sobrescribir.
            # Recargar datos frescos; la persona reintenta su acción.
            st.toast("🔄 Otro usuario guardó cambios más recientes. "
                     "Recargando datos actualizados…", icon="🔄")
            cargar.clear()
            st.rerun()
            return False
        except Exception as e:
            intentos += 1
            if intentos >= 2:
                st.toast(f"No se pudo guardar: {str(e)[:64]}", icon="⚠️")
                return False
            time.sleep(0.8)
    cargar.clear()  # el próximo rerun relee del disco (nunca datos stale)
    return True


def nombre_perfil(datos: dict, perfil_id: str) -> str:
    for p in datos["perfiles"]:
        if p["id"] == perfil_id:
            return p["nombre"]
    return "¿Eliminado?"


def perfil_por_nombre(datos: dict, nombre: str):
    for p in datos["perfiles"]:
        if p["nombre"] == nombre:
            return p
    return None


def guardar_foto_upload(upload, nombre_base: str) -> tuple:
    """Guarda un archivo subido en ./imagenes/<nombre_base><ext>.
    Devuelve (ruta_relativa, b64): en modo nube (Supabase) el b64 viaja a la
    BD porque el filesystem de la nube es efímero; en local b64 = ''.
    El b64 se OPTIMIZA (max 1200px, JPEG q82) para que las fotos de la BD
    no engorden el SELECT ni el HTML de las tarjetas."""
    if upload is None:
        return "", ""
    ext = os.path.splitext(upload.name)[1].lower() or ".jpg"
    os.makedirs(core.RUTA_IMAGENES, exist_ok=True)
    destino = os.path.join(core.RUTA_IMAGENES, f"{nombre_base}{ext}")
    with open(destino, "wb") as f:
        f.write(upload.getbuffer())
    ruta = f"imagenes/{nombre_base}{ext}"
    b64 = ""
    try:
        import db_supabase as _db
        if _db.activo():
            b64 = _optimizar_b64_upload(upload.getbuffer())
    except Exception:
        b64 = ""
    return ruta, b64


def _optimizar_b64_upload(datos_bytes: bytes, max_lado: int = 1200,
                          calidad: int = 82) -> str:
    """Redimensiona y re-codifica los bytes de una foto subida (JPEG).
    Devuelve '' si no se puede procesar (subida corrupta)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(datos_bytes))
        if img.mode == "RGBA":  # fondo oscuro para transparencias
            fondo = Image.new("RGB", img.size, "#161A20")
            fondo.paste(img, mask=img.getchannel("A"))
            img = fondo
        else:
            img = img.convert("RGB")
        w, h = img.size
        escala = min(1.0, max_lado / max(w, h))
        if escala < 1.0:
            img = img.resize((max(1, int(w * escala)),
                              max(1, int(h * escala))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=calidad)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        try:
            return base64.b64encode(datos_bytes).decode("ascii")
        except Exception:
            return ""


def inyectar_css():
    """UI mobile-first: compacta paddings, tarjetas oscuras, botones ≥46px,
    sliders táctiles y apilado automático de columnas en pantallas pequeñas.
    Solo afecta a la capa de vistas; la lógica de datos no se toca."""
    st.markdown("""<style>
/* ════════════════════════════════════════════════════════════════
   TERPSXHUNTER · DESIGN SYSTEM v3
   Un único lenguaje visual: oscuro profundo + verde neón #4ADE80.
   Aplica a TODAS las secciones. Solo capa de vista; no toca lógica.
   ════════════════════════════════════════════════════════════════ */

/* ---------- 1 · FUENTE Y BASE ---------- */
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

html, body, [data-testid="stAppViewContainer"], [data-testid="stMainBlockContainer"] {
  font-family: 'Plus Jakarta Sans', -apple-system, 'Segoe UI', Roboto, sans-serif !important;
  color: #E7EDF4;
}
/* Aplicar Plus Jakarta a los encabezados y markdown (Streamlit usaba Source Sans) */
[data-testid="stMarkdown"] h1, [data-testid="stMarkdown"] h2, [data-testid="stMarkdown"] h3,
[data-testid="stMarkdown"] p, [data-testid="stMarkdownContainer"] *,
[data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3, [data-testid="stMarkdownContainer"] p,
[data-testid="stCaptionContainer"] p, [data-testid="stText"],
button, input, textarea, select, label {
  font-family: 'Plus Jakarta Sans', -apple-system, 'Segoe UI', Roboto, sans-serif !important;
}

/* Fondo inspirado en la referencia: degradado radial difuso muy sutil —
   glow verde en la esquina SUPERIOR-IZQUIERDA, negro profundo al centro,
   glow violeta/morado en la esquina INFERIOR-DERECHA. Tonos tenues que no
   molestan (los muestreos de la imagen: verde rgb(14,53,35), negro #020514,
   violeta #26103f). */
[data-testid="stAppViewContainer"] {
  background:
    /* verde tenue arriba-izquierda (borde superior) */
    radial-gradient(55% 55% at 12% -6%, rgba(20,72,52,0.32), transparent 62%),
    /* violeta tenue abajo-derecha */
    radial-gradient(60% 60% at 96% 104%, rgba(64,32,108,0.30), transparent 64%),
    /* toque verde muy tenue en la zona superior */
    radial-gradient(40% 40% at 45% 0%, rgba(16,60,44,0.14), transparent 60%),
    /* base negro profundo */
    linear-gradient(180deg, #04060f 0%, #030510 55%, #05060f 100%);
}

[data-testid="stAppViewContainer"] > .main { background: transparent; }

/* Selección y scrollbar */
::selection { background: rgba(74,222,128,0.32); color: #fff; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: #2A313C; border-radius: 8px; border: 2px solid #0A0E14; }
::-webkit-scrollbar-track { background: transparent; }

/* Footer de Streamlit oculto */
footer { visibility: hidden; }

/* ---------- 2 · SIDEBAR ---------- */
[data-testid="stSidebar"] {
  background:
    radial-gradient(600px 400px at 0% 0%, rgba(126,90,224,0.14), transparent 60%),
    linear-gradient(180deg, #10141D 0%, #0A0D16 100%) !important;
  border-right: 1px solid #232C37 !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
  color: #F2F5F9; letter-spacing: -0.02em; font-weight: 800;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: #A5AEB8; }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p strong { color: #C9D2DC; }

/* Navegación (radio) en filas limpias con hover + activo */
[data-testid="stSidebar"] label[data-testid="stRadioOption"] {
  border-radius: 10px; padding: 0.5rem 0.65rem; margin: 2px 0;
  transition: background .15s ease, color .15s ease, box-shadow .15s ease;
  font-weight: 500;
}
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover { background: #141A24; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) {
  background: rgba(74,222,128,0.13) !important;
  color: #CFF5DE !important;
  box-shadow: inset 0 0 0 1px rgba(74,222,128,0.30);
  font-weight: 700;
}
[data-testid="stSidebar"] [role="radiogroup"] { gap: 2px; }

/* ---------- 3 · ENCABEZADOS DE SECCIÓN ---------- */
[data-testid="stMarkdown"] h1, [data-testid="stMarkdown"] h2 {
  font-weight: 800 !important; letter-spacing: -0.02em; color: #F1F5F9;
}
[data-testid="stMarkdown"] h1 { font-size: 1.62rem; }
[data-testid="stMarkdown"] h2 {
  font-size: 1.42rem;
  padding-left: 0.72rem; border-left: 4px solid #4ADE80;
  /* acento violeta sutil (referencia del personaje) sin tocar el estilo */
  text-shadow: 0 0 22px rgba(126,90,224,0.25), 0 0 2px rgba(126,90,224,0.10);
}
[data-testid="stMarkdown"] h3 { font-weight: 700; color: #E7EDF4; letter-spacing: -0.01em; }

/* ---------- 4 · BOTONES ---------- */
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
  min-height: 46px; border-radius: 10px; font-weight: 600; font-size: 15px;
  transition: transform .12s ease, box-shadow .12s ease, filter .12s ease, border-color .12s ease;
}
[data-testid="stButton"] button:hover, [data-testid="stFormSubmitButton"] button:hover {
  transform: translateY(-1px); filter: brightness(1.05);
}
/* Primario: degradado verde neón */
[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"],
[data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, #16A34A, #4ADE80) !important;
  border: none !important; color: #06140B !important;
  box-shadow: 0 4px 18px rgba(34,197,94,0.28);
}
[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button[kind="primary"]:hover { box-shadow: 0 6px 24px rgba(34,197,94,0.4); }

/* ---------- 5 · INPUTS CON FOCUS RING VERDE ---------- */
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
  background: #171D27 !important;
  border: 1px solid #333D4B !important; color: #EAF0F6 !important;
  border-radius: 10px;
  transition: border-color .15s ease, box-shadow .15s ease;
}
[data-testid="stTextInput"] input::placeholder, [data-testid="stTextArea"] textarea::placeholder { color: #8B93A1; }
[data-testid="stTextInput"] input:focus, [data-testid="stTextArea"] textarea:focus {
  border-color: rgba(74,222,128,0.65) !important;
  box-shadow: 0 0 0 3px rgba(74,222,128,0.15) !important; outline: none;
}

/* Desplegables (selectbox) */
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
  min-height: 44px; background: #171D27 !important; border-color: #333D4B !important;
  border-radius: 10px; color: #EAF0F6 !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] svg { fill: #C9D2DC; }

/* ---------- 3b · CONTENEDORES CON BORDE (cajas border=True) ---------- */
/* Estilo base unificado para TODAS las cajas con borde (votos, catas de
   productor, asociaciones, etc.): nada se queda con el borde gris por
   defecto de Streamlit (que lee como 'post de foro'). */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, #151B24, #131820);
  border: 1px solid #242C37; border-radius: 14px;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
  border-color: rgba(74,222,128,0.35);
}
/* Tarjetas de coffeeshop (asociaciones): estilo coherente con hover verde */
.cs-card {
  background: linear-gradient(180deg, #171D27, #141920);
  border: 1px solid #242C37; border-radius: 16px; padding: 15px 15px 13px;
  display: flex; flex-direction: column; gap: 9px; min-height: 210px;
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.cs-card:hover {
  border-color: rgba(74,222,128,0.5); transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(0,0,0,0.42);
}

/* ---------- 6 · SLIDERS TÁCTILES ---------- */
[data-testid="stSlider"] [role="slider"] { width: 26px !important; height: 26px !important; margin-top: -13px !important; }
[data-testid="stSlider"] [data-baseweb="slider"] > div > div { height: 7px; }
/* La etiqueta del valor (número sobre el slider) con más presencia */
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] ~ div,
[data-testid="stSlider"] [data-testid="stSliderThumbValue"] { font-weight: 700; }
/* Sliders dentro de un form de votación: label en negrita para legibilidad */
[data-testid="stSlider"] label p { font-weight: 600; }

/* ---------- 7 · RADIO TÁCTIL (fuera del sidebar, p. ej. filtros) ---------- */
[data-testid="stRadio"] label { padding: 0.55rem 0.4rem; font-size: 15px; border-radius: 8px; }
[data-testid="stRadio"] label:hover { background: #161C26; }

/* ---------- 8 · PILLS (filtros táctiles) ---------- */
button[data-variant="pills"] {
  min-height: 36px; border-radius: 18px; font-size: 13px; padding: 0 14px;
  border: 1px solid #333D4B !important; background: #171D27 !important;
  color: #C9D2DC !important;
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}
button[data-variant="pills"]:hover { border-color: rgba(74,222,128,0.5) !important; }
button[data-variant="pills"][aria-checked="true"] {
  background: rgba(74,222,128,0.16) !important; border-color: #4ADE80 !important;
  color: #D9FBE6 !important; font-weight: 700;
}
[data-testid="stPills"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }

/* ---------- 9 · PESTAÑAS (tabs) con subrayado verde ---------- */
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: 0.3rem; overflow-x: auto; flex-wrap: nowrap; }
[data-testid="stTabs"] [data-baseweb="tab"] {
  min-height: 46px; padding: 0 1rem; border-radius: 12px 12px 0 0;
  font-weight: 600; font-size: 14px; white-space: nowrap;
  transition: background .15s ease;
}
[data-testid="stTabs"] [data-baseweb="tab"]:hover { background: rgba(255,255,255,0.04); }
[data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] { color: #CFF5DE !important; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background: #4ADE80; border-radius: 12px 12px 0 0; }

/* ---------- 10 · EXPANDERS ---------- */
[data-testid="stExpander"] details {
  border-radius: 12px; background: #141920; border: 1px solid #242C37;
}
[data-testid="stExpander"] summary { min-height: 46px; display: flex; align-items: center; transition: background .15s ease; }
[data-testid="stExpander"] summary:hover { background: rgba(255,255,255,0.03); }

/* ---------- 11 · AVISOS (info/éxito/error) en tarjeta suave ---------- */
[data-testid="stAlert"] {
  border-radius: 14px; border: 1px solid #232C37 !important;
  background: linear-gradient(180deg, #141A23, #121720) !important;
}
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] { color: #C9D4E0; }

/* ---------- 12 · MÉTRICAS en tarjeta de vidrio ---------- */
[data-testid="stMetric"] {
  background: linear-gradient(180deg, #171D27, #141920);
  border: 1px solid #242C37 !important; border-radius: 16px;
  padding: 0.65rem 0.85rem; box-shadow: 0 2px 12px rgba(0,0,0,0.2);
}
[data-testid="stMetricValue"] { font-weight: 800; color: #EAF0F6; }

/* ---------- 13 · CHIPS / BADGES con borde y aire ---------- */
[data-testid="stMarkdownContainer"] div[style*="border-radius:999px"] {
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.14);
  display: inline-block; margin: 2px 5px 2px 0;
}
[data-testid="stMarkdownContainer"] p { overflow-wrap: anywhere; }

/* ---------- 14 · BOTÓN ☰ MENÚ (solo móvil) ---------- */
.st-key-btn_menu { display: none; }
@media (max-width: 769px) {
  .st-key-btn_menu { display: block; }
  .st-key-btn_menu button { font-size: 16px; min-height: 44px; }
}

/* ---------- 15 · GRID CATÁLOGO / POR VOTAR (3-2-1 columnas) ---------- */
[class*="st-key-grid_catalogo"] [data-testid="stHorizontalBlock"],
[class*="st-key-grid_por_votar"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.6rem; }
[class*="st-key-grid_catalogo"] [data-testid="column"],
[class*="st-key-grid_por_votar"] [data-testid="column"] { min-width: 0; }
@media (min-width: 1100px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"],
  [class*="st-key-grid_por_votar"] [data-testid="column"] { flex: 0 0 calc(33.33% - 0.4rem) !important; }
}
@media (max-width: 1099px) and (min-width: 769px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"],
  [class*="st-key-grid_por_votar"] [data-testid="column"] { flex: 0 0 calc(50% - 0.4rem) !important; }
}
@media (max-width: 768px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"],
  [class*="st-key-grid_por_votar"] [data-testid="column"] { flex: 1 0 100% !important; min-width: 100% !important; }
}

/* ---------- 15b · GRID DE INICIO (tarjetas de navegación 3-2-1) ---------- */
[class*="st-key-inicio_grid"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.6rem; }
[class*="st-key-inicio_grid"] [data-testid="column"] { min-width: 0; }
@media (min-width: 1100px) {
  [class*="st-key-inicio_grid"] [data-testid="column"] { flex: 0 0 calc(33.33% - 0.4rem) !important; }
}
@media (max-width: 1099px) and (min-width: 769px) {
  [class*="st-key-inicio_grid"] [data-testid="column"] { flex: 0 0 calc(50% - 0.4rem) !important; }
}
@media (max-width: 768px) {
  [class*="st-key-inicio_grid"] [data-testid="column"] { flex: 1 0 100% !important; min-width: 100% !important; }
}
/* Tarjetas de navegación del Inicio: centradas, con hover verde */
[class*="st-key-inicio_grid"] [class*="st-key-inicio_card_"] {
  background: linear-gradient(180deg, #171D27, #141920);
  border: 1px solid #242C37; border-radius: 16px; min-height: 130px;
  display: flex; flex-direction: column; justify-content: center;
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
[class*="st-key-inicio_grid"] [class*="st-key-inicio_card_"]:hover {
  border-color: rgba(74,222,128,0.5) !important;
  transform: translateY(-3px);
  box-shadow: 0 12px 30px rgba(0,0,0,0.42) !important;
}
[class*="st-key-inicio_grid"] [class*="st-key-inicio_card_"] button { min-height: 38px; }

/* ---------- 16 · TARJETAS (catálogo + por votar): elevación, hover, overlay ---------- */
[class*="st-key-grid_catalogo"] [class*="st-key-card_"],
[class*="st-key-grid_por_votar"] [class*="st-key-card_"] {
  position: relative; cursor: pointer; min-height: 98px;
  background: linear-gradient(180deg, #171D27, #141920);
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
[class*="st-key-grid_catalogo"] [class*="st-key-card_"]:hover,
[class*="st-key-grid_por_votar"] [class*="st-key-card_"]:hover {
  border-color: rgba(74,222,128,0.5) !important;
  transform: translateY(-2px);
  box-shadow: 0 12px 30px rgba(0,0,0,0.42) !important;
}
[class*="st-key-grid_catalogo"] [class*="st-key-card_"]:active,
[class*="st-key-grid_por_votar"] [class*="st-key-card_"]:active { background: #161C26; }
/* Overlay clicable: el botón 'Abrir' cubre toda la tarjeta */
[class*="st-key-grid_catalogo"] [class*="st-key-abrir_"],
[class*="st-key-grid_por_votar"] [class*="st-key-abrir_"] {
  position: absolute; inset: 0; z-index: 1; opacity: 0; cursor: pointer; margin: 0;
}
[class*="st-key-grid_catalogo"] [class*="st-key-abrir_"] button,
[class*="st-key-grid_por_votar"] [class*="st-key-abrir_"] button {
  position: absolute; inset: 0; width: 100%; height: 100%;
}
/* El 🗑 (admin) queda por encima del overlay */
[class*="st-key-grid_catalogo"] [class*="st-key-del_"] { position: relative; z-index: 3; }
/* Texto secundario de tarjeta (productor) más legible */
[class*="st-key-grid_catalogo"] div[style*="font-size:11.5px"],
[class*="st-key-grid_por_votar"] div[style*="font-size:11.5px"] {
  font-size: 12.5px !important; color: #A5AEB8 !important;
}

/* ---------- 17 · TARJETA 'POR VOTAR' (botones apilados, full-width) ---------- */
[class*="st-key-grid_por_votar"] [class*="st-key-pv_votar_"] { margin-top: 2px; }
[class*="st-key-grid_por_votar"] [class*="st-key-pv_no_"] { margin-top: 4px; }

/* ---------- 18 · BLOQUE NOTA + GUARDAR (voto): sticky al fondo en móvil ---------- */
.st-key-pv_nota_guardar { position: sticky; bottom: 0.5rem; z-index: 998; }
@media (max-width: 768px) {
  .st-key-pv_nota_guardar {
    background: rgba(10, 14, 20, 0.94); backdrop-filter: blur(6px);
    padding: 0.5rem 0.6rem; border-radius: 14px;
    border: 1px solid rgba(74,222,128,0.18);
    box-shadow: 0 -6px 18px rgba(0, 0, 0, 0.55);
  }
  .st-key-pv_nota_guardar [data-testid="stVerticalBlock"] { gap: 0.4rem; }
  .st-key-pv_nota_guardar [data-testid="stVerticalBlock"] > [data-testid="stMarkdownContainer"],
  .st-key-pv_nota_guardar [data-testid="stVerticalBlock"] > [data-testid="element-container"] { margin-bottom: 0; }
}
.st-key-btn_guardar_voto { position: sticky; bottom: 0.5rem; z-index: 999; }
.st-key-btn_guardar_voto button { box-shadow: 0 -6px 18px rgba(0,0,0,.55); }
/* Botones del form de votación apilados a full-width en móvil */
@media (max-width: 768px) {
  .st-key-pv_nota_guardar [data-testid="stHorizontalBlock"]:has(button) { flex-wrap: wrap; }
  .st-key-pv_nota_guardar [data-testid="stHorizontalBlock"]:has(button) > [data-testid="column"] { min-width: 100% !important; }
}

/* ---------- 19 · FICHA DE PRODUCTO (2 columnas; colapsa en móvil) ---------- */
[class*="st-key-ficha_detalle"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
@media (max-width: 768px) {
  [class*="st-key-ficha_detalle"] [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    min-width: 100% !important; flex: 1 0 100% !important;
  }
}

/* ---------- 20 · RANKINGS: filas con aire, captions legibles, botón integrado ---------- */
[class*="st-key-rk_lista"] { gap: 0.55rem; }
[class*="st-key-rk_lista"] [data-testid="stVerticalBlockBorderWrapper"],
[class*="st-key-grid_catalogo"] [data-testid="stVerticalBlockBorderWrapper"],
[class*="st-key-grid_por_votar"] [data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, #151B24, #131820); border: 1px solid #242C37;
  border-radius: 14px; padding: 0.5rem 0.65rem;
}
[class*="st-key-rk_lista"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
  border-color: rgba(74,222,128,0.35);
}
[class*="st-key-rk_lista"] [data-testid="stCaptionContainer"] p { color: #A5AEB8 !important; font-size: 12.5px; }
[class*="st-key-rk_lista"] [data-testid="column"] img { border-radius: 10px; }
[class*="st-key-rk_lista"] [data-testid="column"]:last-child { text-align: right; }
/* Botón 'Ver' compacto e integrado (borde verde, radio pill, menos 'gris genérico') */
[class*="st-key-rk_lista"] [class*="st-key-abrir_rk"] button {
  min-height: 38px; border-radius: 10px;
  border: 1px solid rgba(74,222,128,0.35) !important;
  background: rgba(74,222,128,0.08) !important; color: #8BE9B0 !important;
  font-weight: 600; font-size: 13.5px;
}
[class*="st-key-rk_lista"] [class*="st-key-abrir_rk"] button:hover {
  background: rgba(74,222,128,0.16) !important; border-color: #4ADE80 !important;
}
@media (max-width: 768px) {
  [class*="st-key-rk_lista"] [data-testid="stVerticalBlockBorderWrapper"] { padding: 0.35rem 0.5rem; }
  [class*="st-key-rk_lista"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  [class*="st-key-rk_lista"] [data-testid="stHorizontalBlock"] > [data-testid="column"]:last-child {
    flex: 1 0 100% !important; min-width: 100% !important;
  }
  [class*="st-key-rk_lista"] p { font-size: 13px !important; line-height: 1.3; }
  [class*="st-key-rk_lista"] [data-testid="column"] { min-width: 0 !important; }
}

/* ---------- 21 · EVOLUCIÓN: listas agrupadas en tarjeta con gap compacto ---------- */
[class*="st-key-ev_catas_lista"] [data-testid="stVerticalBlock"],
[class*="st-key-ev_rank_epoca"] [data-testid="stVerticalBlock"] { gap: 0.5rem; }

/* ---------- 22 · BOTÓN 'CONTINUAR CON GOOGLE' (estilo Google, táctil) ---------- */
.st-key-btn_google {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  min-height: 46px; border-radius: 10px; font-weight: 600; font-size: 15px;
  background: #FFFFFF; color: #1F1F1F !important; text-decoration: none;
  border: 1px solid #DADCE0; margin: 0.35rem 0 0.75rem; width: 100%;
  box-sizing: border-box; transition: background 0.15s;
}
.st-key-btn_google:hover { background: #F1F3F4; color: #1F1F1F !important; }
.g_logo {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; border-radius: 50%; background: #4285F4;
  color: #FFFFFF; font-weight: 800; font-size: 13px; flex: 0 0 auto;
}

/* ---------- 23 · MÓVIL: compactar, apilar y ocultar header ---------- */
@media (max-width: 480px) {
  [data-testid="stMainBlockContainer"] { padding: 0.4rem 0.78rem 3.5rem; max-width: 100%; }
  [data-testid="stHeader"] { background: transparent; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stSlider"]),
  [data-testid="stHorizontalBlock"]:has([data-testid="stSelectbox"]),
  [data-testid="stHorizontalBlock"]:has(button[data-variant="pills"]) { flex-wrap: wrap; gap: 0.25rem; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stSlider"]) > [data-testid="column"],
  [data-testid="stHorizontalBlock"]:has([data-testid="stSelectbox"]) > [data-testid="column"],
  [data-testid="stHorizontalBlock"]:has(button[data-variant="pills"]) > [data-testid="column"] { min-width: 100% !important; }
}

/* ---------- 24 · HERO (banda de marca) ---------- */
[data-testid="stMarkdownContainer"] div[style*="border-radius:16px"] {
  border: 1px solid rgba(74,222,128,0.34);
}

/* ---------- 25 · FICHA DE PRODUCTO (presentación) ---------- */
/* Título y nota del producto destacados a la izquierda; la imagen en tarjeta
   con altura contenida para que no entierre el título. */
[class*="st-key-ficha_detalle"] [data-testid="stImage"] img,
[class*="st-key-ficha_detalle"] img {
  border-radius: 14px; border: 1px solid #242C37; object-fit: cover;
  max-height: 320px; width: 100%; display: block;
}
/* Cajas de voto: menos "post de foro", más tarjeta pulida */
[class*="st-key-ficha_detalle"] [data-testid="stVerticalBlockBorderWrapper"] {
  background: linear-gradient(180deg, #151B24, #131820) !important;
  border: 1px solid #242C37 !important; border-radius: 14px;
}
[class*="st-key-ficha_detalle"] [data-testid="stVerticalBlockBorderWrapper"] h3,
[class*="st-key-ficha_detalle"] [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stMarkdownContainer"] h3 {
  font-size: 1.2rem; margin: 0;
}
[class*="st-key-ficha_detalle"] [data-testid="stVerticalBlockBorderWrapper"]:hover {
  border-color: rgba(74,222,128,0.45) !important;
}

/* ---------- 26 · MARCA DE AGUA SUTIL (contorno del personaje) ---------- */
/* Fijo detrás del contenido, tenue pero perceptible, sin interceptar clics. */
.marca-agua {
  position: fixed; top: 50%; left: 45%;
  transform: translate(-50%, -50%);
  width: min(640px, 88vw); height: min(640px, 88vw);
  background-size: contain; background-repeat: no-repeat;
  background-position: center; opacity: 0.2;
  z-index: 0; pointer-events: none;
}

    </style>""", unsafe_allow_html=True)


def _inyectar_marca_agua():
    """Marca de agua sutil con el contorno del personaje, fija y detrás del
    contenido. No intercepta clics (pointer-events:none) y se ve tenue."""
    wm_b64 = _asset_b64("watermark_circle.png")
    if not wm_b64:
        return
    st.markdown(
        f'<div class="marca-agua" '
        f'style="background-image:url(&quot;{wm_b64}&quot;)"></div>',
        unsafe_allow_html=True)


def mostrar_foto(foto: str, width: int = 120, emoji: str = "🌿", b64: str = ""):
    """Muestra la foto si existe; si no, un placeholder discreto.
    En modo nube, `b64` trae la imagen desde la BD (filesystem efímero) y se
    REDIMENSIONA en cliente (las tarjetas no incrustan la foto original)."""
    if b64:
        st.markdown(f'<img src="data:image/jpeg;base64,{_foto_b64_redim(b64, max(width * 2, 400))}" '
                    f'style="width:{width}px;border-radius:10px;display:block">',
                    unsafe_allow_html=True)
        return
    ruta = core.resolver_ruta_foto(foto)
    if ruta:
        try:
            st.image(ruta, width=width)
        except Exception:
            st.write("🖼")
    else:
        st.markdown(f"<div style='width:{width}px;height:{width*0.75}px;"
                    f"border:1px dashed #2A3240;border-radius:8px;display:flex;"
                    f"align-items:center;justify-content:center;color:#5A6472'>"
                    f"{emoji}</div>",
                    unsafe_allow_html=True)


def foto_b64_productor(datos: dict, nombre: str) -> str:
    """foto_b64 del productor por nombre ('' si no tiene o no existe)."""
    if not datos:
        return ""
    nombre = str(nombre or "").strip()
    for p in datos["productores"]:
        if str(p.get("nombre", "")).strip() == nombre:
            return p.get("foto_b64", "") or ""
    return ""


def foto_productor(datos: dict, cata: dict) -> str:
    """Foto del productor asociado a la cata ('' si no existe)."""
    if not datos:
        return ""
    nombre = str(cata.get("productor", "")).strip()
    if not nombre:
        return ""
    for p in datos["productores"]:
        if p["nombre"] == nombre:
            return p.get("foto", "")
    return ""


def chip(texto: str, color: str, texto_color: str = "#0e131a") -> str:
    """Badge de color (HTML) para chips de tipo/país."""
    return (f"<span style='background:{color};color:{texto_color};"
            f"padding:2px 10px;border-radius:12px;font-size:12px;font-weight:bold'>"
            f"{texto}</span>")


def foto_base64(ruta: str, px: int = 76, radius: int = 10, b64: str = "") -> str:
    """<img> cuadrada en base64 (recorte central) para HTML custom.
    Devuelve '' si la ruta no existe o no puede leerse. Cacheada por
    (ruta, px, radius, mtime): solo se regenera si el archivo cambia.
    En modo nube se pasa `b64` (foto de la BD) y se genera un THUMBNAIL
    ligero en cliente (px) en vez de incrustar la foto original."""
    if b64:
        return (f'<img src="data:image/jpeg;base64,{_foto_b64_thumb(b64, px, radius)}" '
                f'alt="" style="width:{px}px;height:{px}px;border-radius:{radius}px;'
                f'object-fit:cover;display:block">')
    if not ruta or not os.path.exists(ruta):
        return ""
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return ""
    try:
        return _foto_base64_cached(ruta, px, radius, mtime)
    except Exception:
        return ""


# Memoización de thumbnails b64 (modo nube): (md5, px, radius) -> thumb ligero.
_THUMBS = {}
_THUMBS_MAX = 600  # defensa: si crece demasiado, se vacía (se regenera solo)


def _foto_b64_thumb(b64: str, px: int = 84, radius: int = 10) -> str:
    """Thumbnail cuadrado ligero desde un b64 de la BD (recorte central,
    JPEG q80). Memoizado por (md5, px, radius): se genera una sola vez."""
    if not b64:
        return ""
    clave = (hashlib.md5(b64.encode("utf-8")).hexdigest(), px, radius)
    if clave not in _THUMBS:
        if len(_THUMBS) > _THUMBS_MAX:
            _THUMBS.clear()
        _THUMBS[clave] = _b64_a_jpeg(b64, px, cuadrado=True)
    return _THUMBS[clave]


def _foto_b64_redim(b64: str, max_lado: int = 400) -> str:
    """Redimensiona un b64 a <= max_lado conservando aspecto (JPEG q80)."""
    if not b64:
        return ""
    clave = (hashlib.md5(b64.encode("utf-8")).hexdigest(), max_lado, 0)
    if clave not in _THUMBS:
        if len(_THUMBS) > _THUMBS_MAX:
            _THUMBS.clear()
        _THUMBS[clave] = _b64_a_jpeg(b64, max_lado, cuadrado=False)
    return _THUMBS[clave]


def _b64_a_jpeg(b64: str, px: int, cuadrado: bool) -> str:
    """Abre un b64, lo recorta (opcional) y lo re-codifica a JPEG ligero.
    Devuelve el b64 ORIGINAL si PIL falla (nunca rompe el render)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        if img.mode == "RGBA":  # fondo oscuro para transparencias
            fondo = Image.new("RGB", img.size, "#161A20")
            fondo.paste(img, mask=img.getchannel("A"))
            img = fondo
        else:
            img = img.convert("RGB")
        w, h = img.size
        if cuadrado:
            lado = min(w, h)
            img = img.crop(((w - lado) // 2, (h - lado) // 2,
                            (w + lado) // 2, (h + lado) // 2))
            img = img.resize((px, px), Image.LANCZOS)
        else:
            escala = min(1.0, px / max(w, h))
            if escala < 1.0:
                img = img.resize((max(1, int(w * escala)),
                                  max(1, int(h * escala))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return b64  # fallback seguro: original


def _asset_b64(nombre: str) -> str:
    """Lee un asset de la carpeta assets/ y devuelve su data URI base64.
    Funciona en local y en la NUBE, probando varias rutas de montaje
    (`__file__`, cwd, /mount/src de Streamlit Cloud, raíz del repo)."""
    bases = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
        os.path.join(os.getcwd(), "assets"),
        os.path.join("/mount/src", os.path.basename(os.getcwd()) or "", "assets"),
        "/mount/src/assets",
        "assets",
    ]
    for b in bases:
        r = os.path.join(b, nombre)
        try:
            if os.path.exists(r):
                with open(r, "rb") as f:
                    data = f.read()
                mime = "image/png" if nombre.lower().endswith(".png") else "image/jpeg"
                return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        except Exception:
            continue
    return ""



@st.cache_data(show_spinner=False)
def _foto_base64_cached(ruta: str, px: int, radius: int, mtime: float) -> str:
    """PIL (abrir + recortar + redimensionar + JPEG) cacheado: la operación
    cara se hace una sola vez por foto. `mtime` invalida la caché si la
    imagen cambia en disco (al subir una foto nueva)."""
    from PIL import Image
    img = Image.open(ruta).convert("RGB")
    w, h = img.size
    lado = min(w, h)
    img = img.crop(((w - lado) // 2, (h - lado) // 2,
                    (w + lado) // 2, (h + lado) // 2))
    img = img.resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return (f'<img src="data:image/jpeg;base64,{b64}" alt="" '
            f'style="width:{px}px;height:{px}px;border-radius:{radius}px;'
            f'object-fit:cover;display:block">')


def foto_base64_fluid(ruta: str, radius: int = 12, mtime: float = 0.0,
                      b64: str = "") -> str:
    """<img> base64 con width:100% (se adapta al contenedor del podio).
    Devuelve '' si la ruta no existe. Cacheada por (ruta, radius, mtime).
    En modo nube se pasa `b64` y se omite el archivo."""
    if b64:
        return (f'<img src="data:image/jpeg;base64,{b64}" alt="" '
                f'style="width:100%;aspect-ratio:1/1;object-fit:cover;'
                f'border-radius:{radius}px;display:block">')
    if not ruta or not os.path.exists(ruta):
        return ""
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        return ""
    try:
        return _foto_base64_fluid_cached(ruta, radius, mtime)
    except Exception:
        return ""


@st.cache_data(show_spinner=False)
def _foto_base64_fluid_cached(ruta: str, radius: int, mtime: float) -> str:
    """Igual que _foto_base64_cached pero con width:100%: la imagen ocupa
    TODO el ancho del contenedor (crítico en el podio responsive)."""
    from PIL import Image
    img = Image.open(ruta).convert("RGB")
    w, h = img.size
    lado = min(w, h)
    img = img.crop(((w - lado) // 2, (h - lado) // 2,
                    (w + lado) // 2, (h + lado) // 2))
    img = img.resize((240, 240), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=78)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return (f'<img src="data:image/jpeg;base64,{b64}" alt="" '
            f'style="width:100%;aspect-ratio:1/1;object-fit:cover;'
            f'border-radius:{radius}px;display:block">')


def placeholder_imagen(px: int = 76, emoji: str = "🌿", radius: int = 10) -> str:
    """Placeholder discreto (div con borde punteado) cuando no hay foto."""
    return (f'<div style="width:{px}px;height:{px}px;border:1px dashed #2A3240;'
            f'border-radius:{radius}px;display:flex;align-items:center;'
            f'justify-content:center;font-size:{px // 3}px;color:#5A6472">'
            f"{emoji}</div>")


def placeholder_imagen_fluid(emoji: str = "🌿", radius: int = 12) -> str:
    """Placeholder que ocupa TODO el ancho del contenedor (podio responsive)."""
    return (f'<div style="width:100%;aspect-ratio:1/1;border:1px dashed #2A3240;'
            f'border-radius:{radius}px;display:flex;align-items:center;'
            f'justify-content:center;font-size:44px;color:#5A6472">'
            f"{emoji}</div>")


def extracto(texto, max_chars: int = 110) -> str:
    """Extracto plano (sin saltos de línea) truncado para las tarjetas."""
    if not texto:
        return ""
    t = " ".join(str(texto).split())
    return (t[:max_chars] + "…") if len(t) > max_chars else t


def render_sliders_blocks(voto_precargar=None, prefijo: str = ""):
    """
    Renderiza los 4 bloques de sliders en PESTAÑAS (👁️ Aspecto / 👃 Aroma /
    👅 Sabor / ✨ Efectos) con ETIQUETA visible y escala 1-100 (enteros).
    Devuelve las puntuaciones en escala 1-10 (÷10) para mantener intacta la
    capa de datos (puntuaciones_detalle en 1-10).
    - Una pestaña por bloque: elimina el scroll vertical infinito y los
      clics extra de los expanders anidados (UX móvil más rápida).
    - La nota del bloque (en vivo) se muestra dentro de cada pestaña.
    - voto_precargar: dict de puntuaciones (1-10) a precargar (×10 al slider).
    - prefijo: identifica el conjunto de sliders sin colisión de keys.
    """
    scores = {}
    # Flag de precarga: el pop de las keys solo debe hacerse la PRIMERA vez que
    # se muestra el conjunto (al abrir el editor / cargar la cata). Si se hiciera
    # en cada rerun, los sliders volverían a saltar al valor precargado nada más
    # moverlos (bug: el usuario editaba y se guardaba siempre el valor original).
    flag_pre = f"{prefijo}_precargado"
    tabs = st.tabs(["👁️ Aspecto", "👃 Aroma", "👅 Sabor", "✨ Efectos"])
    for tab, meta in zip(tabs, core.BLOQUES):
        with tab:
            scores_bloque = {}
            # Sliders de 2 en 2 (móvil los apila por CSS)
            pares = list(meta["subs"])
            for i in range(0, len(pares), 2):
                fila = pares[i:i + 2]
                cols = st.columns(len(fila))
                for col, (clave, etiqueta) in zip(cols, fila):
                    with col:
                        key = f"{prefijo}sl_{meta['clave']}_{clave}"
                        valor_inicial = 50
                        if voto_precargar is not None:
                            valor_inicial = round(core._flotante(
                                voto_precargar.get(meta["clave"], {}).get(clave, 5.0)) * 10)
                            if not st.session_state.get(flag_pre):
                                st.session_state.pop(key, None)  # precargar al abrir
                        # Slider 1-100 con su etiqueta visible (la gente ve qué vota)
                        scores_bloque[clave] = st.slider(
                            etiqueta, min_value=1, max_value=100, value=valor_inicial,
                            step=1, key=key) / 10.0
            # Nota del bloque (sobre 100), en vivo — con color semántico (verde/amarillo/rojo)
            nota10 = core.calcular_nota_bloque(scores_bloque, meta["clave"])
            nota100 = nota10 * 10
            color_bloque = core.color_nota(nota10)
            st.markdown(
                f'<div style="display:flex;align-items:center;justify-content:space-between;'
                f'margin:4px 0 8px;padding:10px 14px;border-radius:12px;'
                f'background:linear-gradient(180deg,#171D27,#141920);border:1px solid #242C37">'
                f'<span style="font-size:13px;color:#A5AEB8;font-weight:600">'
                f'{_html.escape(meta["titulo"])} · {int(meta["peso"] * 100)}%</span>'
                f'<span style="font-size:20px;font-weight:800;color:{color_bloque}">'
                f'{nota100:.0f}<span style="font-size:11px;color:#7A8391;font-weight:600">/100</span>'
                f'</span></div>',
                unsafe_allow_html=True)
            scores[meta["clave"]] = scores_bloque
    if voto_precargar is not None:
        st.session_state[flag_pre] = True  # precarga aplicada: no volver a resetear
    return scores


def obtener_scores_actuales(prefijo: str = "") -> dict:
    """Lee los sliders (escala 1-100) y devuelve puntuaciones en escala 1-10."""
    scores = {}
    for meta in core.BLOQUES:
        scores[meta["clave"]] = {}
        for clave, _ in meta["subs"]:
            key = f"{prefijo}sl_{meta['clave']}_{clave}"
            # `in` funciona tanto en Streamlit real como en AppTest
            val = st.session_state[key] if key in st.session_state else 50
            scores[meta["clave"]][clave] = val / 10.0
    return scores


def cata_por_nombre(datos: dict, nombre: str):
    nombre = nombre.strip().lower()
    for c in datos["catas"]:
        if str(c.get("nombre", "")).strip().lower() == nombre:
            return c
    return None


def fila_producto(cata: dict, pos: int = None, datos: dict = None):
    """Tarjeta compacta de producto en HTML/CSS puro (display:flex).
    Foto a la izquierda; nombre, productor, chips y extracto de la descripción
    a la derecha ocupando TODO el ancho disponible; nota grande al final.
    Usada en Catálogo, Por votar y Rankings (funciona en 360-430px)."""
    media = core.nota_media(cata)
    n_votos = len(core.votos_validos(cata))
    nombre = _html.escape(str(cata.get("nombre", "—")))
    productor = _html.escape(str(cata.get("productor", "") or "—"))
    if pos:
        medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
        nombre = f"{medallas.get(pos, '')} {pos}º · {nombre}"

    # Chips: tipo / país / año
    chips_row = [chip(_html.escape(str(cata.get("tipo", "—"))),
                      core.COLOR_TIPO.get(cata.get("tipo", ""), "#444444"))]
    if cata.get("pais"):
        chips_row.append(chip(_html.escape(str(cata["pais"])),
                              core.COLOR_PAIS.get(cata["pais"], "#444444")))
    if cata.get("anio"):
        chips_row.append(chip(f"📅 {cata['anio']}", core.COLOR_ANIO))
    if cata.get("temporada"):
        chips_row.append(chip(f"◈ {cata['temporada']}",
                              core.COLOR_TEMPORADA.get(cata["temporada"], "#444444")))

    # Fotos embebidas en base64 (thumbnails ligeros) o placeholder
    foto_html = foto_base64(core.resolver_ruta_foto(cata.get("foto")), px=76,
                            b64=cata.get("foto_b64", ""))
    if not foto_html:
        foto_html = placeholder_imagen(76, "🌿")
    mini_html = foto_base64(core.resolver_ruta_foto(foto_productor(datos, cata)),
                            px=28, radius=6,
                            b64=foto_b64_productor(datos, cata.get("productor")))
    if not mini_html:
        mini_html = placeholder_imagen(28, "🏭", radius=6)

    # Pequeño extracto de la descripción (si hay comentarios)
    extracto_html = ""
    if cata.get("comentarios"):
        extracto_html = (f"<div style='font-size:12px;color:#6B7480;margin-top:5px;"
                         f"line-height:1.35;overflow:hidden;display:-webkit-box;"
                         f"-webkit-line-clamp:2;-webkit-box-orient:vertical'>"
                         f"{_html.escape(extracto(cata.get('comentarios')))}</div>")

    # Último comentario de un usuario de confianza (si lo hay)
    coment_usuario_html = ""
    comentarios_u = [cu for cu in cata.get("comentarios_usuarios", [])
                     if isinstance(cu, dict) and cu.get("texto")]
    if comentarios_u:
        ultimo = comentarios_u[-1]
        autor = _html.escape(str(ultimo.get("nombre", "—")))
        texto = _html.escape(extracto(ultimo.get("texto", ""), 80))
        coment_usuario_html = (f"<div style='font-size:12px;color:#B8C0CC;margin-top:4px;"
                               f"line-height:1.35;display:-webkit-box;-webkit-line-clamp:1;"
                               f"-webkit-box-orient:vertical;overflow:hidden'>"
                               f"💬 <b>{autor}</b>: {texto}</div>")

    # Nota profesional (gente de confianza + admins), si existe
    prof_html = ""
    nota_prof = core.nota_media_profesional(cata, datos) if datos else None
    if nota_prof is not None:
        prof_html = (f"<div style='font-size:13px;font-weight:700;color:#e8c35a;"
                     f"line-height:1.1;margin-top:2px'>⭐ {nota_prof:.1f}</div>")

    color_nota = core.color_nota(media / 10)
    # HTML en UNA SOLA LÍNEA (concatenación sin \n): el parser de markdown
    # interpreta cualquier línea con indentación desigual como bloque de
    # código y se vería el HTML en crudo (bug MelaVerde).
    html_tarjeta = (
        '<div style="display:flex;gap:12px;align-items:flex-start;width:100%">'
        '<div style="flex:0 0 auto;display:flex;flex-direction:column;align-items:center;gap:5px">'
        + foto_html + mini_html
        + '</div>'
        '<div style="flex:1;min-width:0">'
        f'<div style="font-weight:700;font-size:15px;color:#F2F5F9;line-height:1.25">{nombre}</div>'
        f'<div style="font-size:12px;color:#8B93A1;margin-top:1px">{productor}</div>'
        '<div style="margin-top:5px">' + '  '.join(chips_row) + '</div>'
        + extracto_html + coment_usuario_html
        + '</div>'
        '<div style="flex:0 0 auto;text-align:right;min-width:46px">'
        f'<div style="font-size:22px;font-weight:800;color:{color_nota};line-height:1.1">{media:.1f}</div>'
        + prof_html
        + f'<div style="font-size:11px;color:#6B7480">{n_votos} voto'
        + ('s' if n_votos != 1 else '') + '</div>'
        + '</div></div>'
    )
    st.markdown(html_tarjeta, unsafe_allow_html=True)


def podium_epico(lista, nota_fn=None):
    """Podio del Top 3 en HTML/CSS: tarjetas con la FOTO del producto,
    medalla y nota. Desktop: 3 columnas horizontales. Móvil (≤768px):
    las 3 tarjetas colapsan VERTICALMENTE (flex-direction: column) para
    que la foto (width:100%) y los textos no se aplasten.
    nota_fn: función alternativa para la nota mostrada (p. ej. profesional)."""
    metales = {
        1: ("🥇", "#FFD700", "pod-1"),
        2: ("🥈", "#C0C0C0", "pod-2"),
        3: ("🥉", "#CD7F32", "pod-3"),
    }
    tarjetas = []
    for i, cata in enumerate(lista[:3], start=1):
        emoji, _, cls = metales[i]
        nombre = _html.escape(str(cata.get("nombre", "—")))
        productor = _html.escape(str(cata.get("productor", "") or "—"))
        media = (nota_fn(cata) if nota_fn else core.nota_media(cata))
        n_votos = len(core.votos_validos(cata))
        color_nota = core.color_nota(media / 10)
        # Foto del producto: ocupa todo el ancho de la tarjeta (width:100%)
        foto_html = foto_base64_fluid(core.resolver_ruta_foto(cata.get("foto")),
                                      b64=cata.get("foto_b64", ""))
        if not foto_html:
            foto_html = placeholder_imagen_fluid("🌿")
        # dedent + indent a 4: mismas reglas que el HTML padre para que el
        # parser de markdown no lo trate como código (HTML crudo en pantalla)
        tarjeta = textwrap.dedent(f"""
            <div class="pod-card {cls}">
              <div class="pod-foto">{foto_html}</div>
              <div class="pod-medal">{emoji}</div>
              <div class="pod-name">{nombre}</div>
              <div class="pod-prod">{productor}</div>
              <div class="pod-nota" style="color:{color_nota}">{media:.1f}</div>
              <div class="pod-votos">{n_votos} voto{'s' if n_votos != 1 else ''}</div>
            </div>""")
        tarjetas.append(textwrap.indent(tarjeta, "    "))
    html_podio = f"""
    <style>
    .pod-row {{ display:flex; gap:10px; width:100%; margin:6px 0 2px; }}
    .pod-card {{ flex:1; min-width:0; border-radius:16px; padding:14px 10px 12px; text-align:center;
      background:linear-gradient(180deg,#1B2129 0%,#161A20 100%); position:relative; }}
    .pod-1 {{ border:2px solid #FFD700; box-shadow:0 0 20px rgba(255,215,0,.32), inset 0 0 14px rgba(255,215,0,.07); }}
    .pod-2 {{ border:2px solid #C0C0C0; box-shadow:0 0 16px rgba(192,192,192,.22), inset 0 0 10px rgba(192,192,192,.05); }}
    .pod-3 {{ border:2px solid #CD7F32; box-shadow:0 0 16px rgba(205,127,50,.22), inset 0 0 10px rgba(205,127,50,.05); }}
    .pod-foto {{ width:100%; margin-bottom:8px; }}
    .pod-foto img {{ display:block; border-radius:10px; }}
    .pod-medal {{ font-size:30px; line-height:1; }}
    .pod-name {{ font-weight:700; font-size:13px; color:#F2F5F9; margin-top:6px; line-height:1.25;
      display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
    .pod-prod {{ font-size:11px; color:#8B93A1; margin-top:2px; white-space:nowrap;
      overflow:hidden; text-overflow:ellipsis; }}
    .pod-nota {{ font-size:21px; font-weight:800; margin-top:6px; line-height:1; }}
    .pod-votos {{ font-size:11px; color:#8B93A1; margin-top:3px; }}
    /* ===== MÓVIL: las 3 tarjetas colapsan a UNA COLUMNA (fotos sin aplastarse) ===== */
    @media (max-width:768px) {{
      .pod-row {{ flex-direction:column; align-items:stretch; gap:10px; }}
      .pod-card {{ padding:14px 10px; }}
      .pod-foto {{ max-width:280px; margin:0 auto 8px; }}
      .pod-medal {{ font-size:28px; }}
      .pod-name {{ font-size:14px; margin-top:5px; }}
      .pod-nota {{ font-size:22px; margin-top:5px; }}
    }}
    @media (max-width:430px) {{
      .pod-card {{ padding:12px 6px; }}
      .pod-medal {{ font-size:26px; }}
      .pod-name {{ font-size:13px; }}
      .pod-nota {{ font-size:20px; }}
    }}
    </style>
    <div class="pod-row">{''.join(tarjetas)}</div>
    """
    st.markdown(textwrap.dedent(html_podio), unsafe_allow_html=True)


# =============================================================================
# SIDEBAR — perfil activo + navegación (mobile-first, colapsable)
# =============================================================================

def paginas_para(datos: dict) -> list:
    """Secciones del menú según el rol:
    - Invitado: ve TODAS (al tocar una restringida se le pide iniciar sesión).
    - Admin / gente de confianza: todas (pueden crear productos).
    - Usuario normal: todo menos crear productos (➕ Nueva Cata)."""
    if not st.session_state.get("usuario"):
        return ["🏠 Inicio", "📦 Catálogo", "➕ Nueva Cata", "🎯 Por votar",
                "🏭 Productores", "🏆 Rankings", "📈 Evolución",
                "🏪 Asociaciones", "👥 Perfiles"]
    if es_admin(datos) or es_profesional(datos):
        return ["🏠 Inicio", "📦 Catálogo", "➕ Nueva Cata", "🎯 Por votar",
                "🏭 Productores", "🏆 Rankings", "📈 Evolución",
                "🏪 Asociaciones", "👥 Perfiles"]
    return ["🏠 Inicio", "📦 Catálogo", "🎯 Por votar", "🏭 Productores",
            "🏆 Rankings", "📈 Evolución", "🏪 Asociaciones", "👥 Perfiles"]


def menu_movil(datos: dict):
    """Botón '☰ Menú' (visible solo en móvil por CSS) que despliega la
    navegación en la propia página. Streamlit no permite abrir el sidebar
    nativo con JS (sanitiza scripts), así que es un panel propio, más visual."""
    if st.button("☰ Menú", key="btn_menu", width="stretch"):
        st.session_state["menu_abierto"] = not st.session_state.get("menu_abierto", False)
    if st.session_state.get("menu_abierto"):
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.caption("🗺 Navegación")
            with c2:
                if st.button("✕", key="menu_cerrar", width="stretch"):
                    st.session_state["menu_abierto"] = False
                    st.rerun()
            for p in paginas_para(datos):
                if st.button(p, key=f"menu_{p}", width="stretch"):
                    st.session_state["pagina"] = p
                    st.session_state["menu_abierto"] = False
                    st.rerun()


def hero(datos: dict, logueado: bool):
    """Banda de marca compacta arriba del contenido: identidad + estado, sin
    texto técnico. Da un acabado más cuidado y despeja el aspecto de la app."""
    n = len(datos.get("catas", []))
    if core._db_nube() is not None:
        chip = "<span style='color:#8BE9B0;font-weight:600'>🟢 Conectado</span>"
    else:
        chip = "<span style='color:#E8C35A;font-weight:600'>💾 Modo local</span>"
    if logueado:
        chip += (f"&nbsp;&nbsp;<span style='color:#8B93A1'>·&nbsp;👤 "
                 f"{_html.escape(str(st.session_state.get('usuario', '')))}</span>")
    st.markdown(
        f'<div style="border-radius:16px;padding:0.8rem 1.1rem;margin:0 0 0.7rem;'
        f'background:linear-gradient(135deg,rgba(126,90,224,0.22),rgba(74,222,128,0.16));'
        f'border:1px solid rgba(126,90,224,0.35);box-shadow:0 6px 24px rgba(0,0,0,0.3);">'
        f'<div style="display:flex;align-items:center;gap:0.6rem;flex-wrap:wrap;">'
        f'<span style="font-size:1.3rem;font-weight:800;color:#F2F5F9;'
        f'letter-spacing:-0.02em;">🌿 TerpsXHunter</span>'
        f'<span style="font-size:0.8rem;color:#A5AEB8;">· {n} cata'
        f'{"s" if n != 1 else ""}</span>'
        f'<span style="margin-left:auto;font-size:0.8rem;">{chip}</span></div></div>',
        unsafe_allow_html=True)


def sidebar(datos: dict):
    with st.sidebar:
        # Cabecera de marca: logo del personaje como identidad (sin emoji hoja)
        logo_b64 = _asset_b64("inicio_avatar.jpg")
        if logo_b64:
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:0.6rem;'
                f'margin:0.2rem 0 0.35rem">'
                f'<img src="{logo_b64}" alt="" style="width:40px;height:40px;'
                f'border-radius:12px;object-fit:cover;border:2px solid '
                f'rgba(126,90,224,0.55);box-shadow:0 3px 10px rgba(0,0,0,0.4)">'
                f'<span style="font-size:1.15rem;font-weight:800;color:#F2F5F9;'
                f'letter-spacing:-0.02em;">TerpsXHunter</span>'
                f'</div>',
                unsafe_allow_html=True)
        else:
            st.markdown("## TerpsXHunter")

        # ---- Estado de datos (diagnóstico de conexión) ----
        if core._db_nube() is not None:
            st.caption(f"🗄 **Conectado a Supabase** · {len(datos['catas'])} catas")
        else:
            st.caption(f"🗄 Modo local (catas.json) · {len(datos['catas'])} catas")
            # Diagnóstico: ¿llegan los secrets del Settings de Streamlit Cloud?
            try:
                import streamlit as _st
                import re as _re
                _s = getattr(_st, "secrets", None)
                if _s is None:
                    st.caption("🔍 secrets: no disponible")
                else:
                    _tiene = "supabase" in _s
                    st.caption(f"🔍 secrets en runtime: "
                               f"{'SÍ [supabase]' if _tiene else 'NO — vacío'} "
                               f"({list(_s.keys()) if not _tiene else ''})")
                    if _tiene:
                        _bloque = dict(_s["supabase"])
                        _url = str(_bloque.get("url", ""))
                        _url_masc = _re.sub(r"://([^:]+):([^@]+)@",
                                            r"://\1:***@", _url)
                        st.caption(f"🔍 claves [supabase]: {list(_bloque.keys())} | "
                                   f"url: {_url_masc[:90]}")
            except Exception as _e:
                st.caption(f"🔍 error leyendo secrets: {_e}")
        if core._db_nube() is not None and len(datos["catas"]) == 0:
            # Diagnóstico puntual: se lee la BD directa UNA sola vez (no en cada
            # rerun) para no alargar cada interacción cuando la caché vino vacía.
            if not st.session_state.get("_diag_supabase"):
                st.session_state["_diag_supabase"] = True
                try:
                    import db_supabase as _db
                    with st.spinner("Comprobando Supabase…"):
                        _fresh = _db.cargar_datos()  # carga DIRECTA, sin caché
                    st.caption(f"🔍 carga directa (sin caché): "
                               f"{len(_fresh['catas'])} catas")
                    _err = getattr(_db, "_ULTIMO_ERROR", "")
                    if _err:
                        st.caption(f"⚠️ {str(_err)[:130]}")
                except Exception:
                    pass

        # ---- Sesión (usuario logueado o modo invitado) ----
        st.divider()
        usuario = st.session_state.get("usuario", "")
        if usuario:
            perfil = perfil_por_nombre(datos, usuario)
            badge = "  👑" if es_admin(datos) else (
                "  🤝" if perfil is not None and perfil.get("es_confianza") else "")
            st.markdown(f"**👤 {usuario}**{badge}")
            if st.button("🚪 Cerrar sesión", width="stretch"):
                _borrar_cookie()  # olvidar el dispositivo
                st.session_state.pop("usuario", None)
                st.session_state["pagina"] = "📦 Catálogo"
                st.rerun()
        else:
            st.markdown("**👤 Invitado** *(solo lectura)*")
            if st.button("🔑 Iniciar sesión", width="stretch"):
                st.session_state["pagina"] = "🔐 Acceso"
                st.rerun()

        # ---- Navegación ----
        st.divider()
        # Invitado ve TODAS las secciones (las restringidas piden login)
        paginas = paginas_para(datos)
        pagina_actual = st.session_state.get("pagina", paginas[0])
        if pagina_actual not in paginas:  # p. ej. "🔐 Acceso" desde el botón
            pagina_actual = paginas[0]
        st.session_state["pagina"] = pagina_actual
        # Radio SIN key persistente: el valor se controla solo con
        # session_state["pagina"] (el index). Así "Volver como invitado" u
        # otras navegaciones no chocan con un widget que recuerda su valor.
        eleccion = st.radio("Sección", paginas, label_visibility="collapsed",
                            index=paginas.index(pagina_actual))
        st.session_state["pagina"] = eleccion

        total = len(datos["catas"])
        votos = sum(len(core.votos_validos(c)) for c in datos["catas"])
        st.divider()
        st.caption(f"{total} catas · {votos} votos")


def perfil_activo(datos: dict):
    """El perfil logueado (la identidad real de la sesión actual)."""
    nombre = st.session_state.get("usuario")
    return perfil_por_nombre(datos, nombre) if nombre else None


def es_admin(datos: dict) -> bool:
    """
    ¿El perfil logueado es admin? El perfil por defecto (dueño) y los perfiles
    marcados como admin en la sección Perfiles pueden eliminar productos,
    gestionar perfiles y productores.
    """
    perfil = perfil_activo(datos)
    if perfil is None:
        return False
    return bool(perfil.get("es_admin")) or perfil.get("id") == "p_default"


def es_profesional(datos: dict) -> bool:
    """Rango 'gente de confianza' (+ admins): puede comentar productos y su
    voto alimenta la valoración profesional (evaluación objetiva)."""
    perfil = perfil_activo(datos)
    if perfil is None:
        return False
    return es_admin(datos) or bool(perfil.get("es_confianza"))


# -----------------------------------------------------------------------------
# Autenticación (contraseñas con hash PBKDF2 + salt; nunca en claro)
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


# =============================================================================
# SESIÓN PERSISTENTE (cookie "recordar sesión") + GOOGLE OAUTH
# Complementa al login tradicional (usuario/contraseña) SIN sustituirlo:
#   - Cookie firmada HMAC-SHA256 con el secreto del servidor: un token no se
#     puede forjar ni reutilizar tras su expiración (30 días).
#   - Google OAuth 2.0 (Authorization Code + PKCE) contra Google directamente;
#     la app NO usa Supabase Auth (solo Postgres), por eso el flujo es OAuth puro.
# =============================================================================

COOKIE_SESION = "terpsx_sesion"
COOKIE_DIAS = 30
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


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


def _crear_token_sesion(perfil_id: str) -> str:
    """Token firmado (HMAC-SHA256) para la cookie 'recordar sesión'."""
    return core.crear_token_sesion(perfil_id, _secret_sesion(), COOKIE_DIAS)


def _verificar_token_sesion(token: str):
    """Devuelve el perfil_id si el token es válido (firma + no expirado)."""
    return core.verificar_token_sesion(token, _secret_sesion())


def _base_url_app() -> str:
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


def _leer_cookie() -> str:
    """Lee la cookie de sesión (vacío si no hay / librería no disponible)."""
    if _COOKIES is None:
        return ""
    try:
        valor = _COOKIES.get(COOKIE_SESION)
        return str(valor) if valor else ""
    except Exception:
        return ""


def _escribir_cookie(token: str):
    if _COOKIES is None or not token:
        return
    try:
        _COOKIES.set(COOKIE_SESION, token, max_age=COOKIE_DIAS * 86400,
                     same_site="Lax", secure=_base_url_app().startswith("https"),
                     path="/")
    except Exception:
        pass


def _borrar_cookie():
    if _COOKIES is None:
        return
    try:
        _COOKIES.remove(COOKIE_SESION)
    except Exception:
        try:
            _COOKIES.set(COOKIE_SESION, "", max_age=0, path="/")
        except Exception:
            pass


def _auto_login_cookie(datos: dict):
    """Si hay cookie válida y nadie ha entrado, abre sesión automáticamente."""
    if st.session_state.get("usuario"):
        return
    token = _leer_cookie()
    perfil_id = _verificar_token_sesion(token)
    if not perfil_id:
        if token:  # token corrupto o expirado: limpiar la cookie
            _borrar_cookie()
        return
    perfil = next((p for p in datos.get("perfiles", [])
                   if p.get("id") == perfil_id), None)
    if perfil is None:
        _borrar_cookie()
        return
    st.session_state["usuario"] = perfil["nombre"]
    st.session_state["pagina"] = "📦 Catálogo"


# ----------------------------- Google OAuth --------------------------------

def _credenciales_google() -> dict:
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


def _url_autorizacion_google() -> str:
    """URL de Google con PKCE (S256). El code_verifier vive en session_state."""
    cfg = _credenciales_google()
    if not cfg:
        return ""
    verifier = secrets.token_urlsafe(64)
    st.session_state["oauth_verifier"] = verifier
    st.session_state["oauth_state"] = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()).rstrip(b"=").decode()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": _base_url_app() + "/",
        "response_type": "code",
        "scope": "openid email profile",
        "state": st.session_state["oauth_state"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def _intercambiar_codigo_google(code: str) -> dict:
    """Cambia el code por tokens y devuelve la info del usuario de Google."""
    cfg = _credenciales_google()
    verifier = st.session_state.get("oauth_verifier", "")
    if not cfg or not verifier:
        return {}
    cuerpo = urllib.parse.urlencode({
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": _base_url_app() + "/",
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


def _manejar_retorno_oauth(datos: dict):
    """Captura el ?code= de la URL al volver de Google y abre sesión.
    También cubre fragmentos (#...) por compatibilidad con otros proveedores."""
    qp = st.query_params
    code = qp.get("code", "")
    estado = qp.get("state", "")
    if not code:  # también buscar en fragmento de URL (si el proveedor lo usa)
        return
    try:
        qp.clear()  # limpiar SIEMPRE, pase lo que pase
    except Exception:
        pass
    if not estado or estado != st.session_state.get("oauth_state", ""):
        st.error("⚠️ Error de seguridad al volver de Google (state no coincide). "
                 "Inténtalo de nuevo.")
        return
    try:
        info = _intercambiar_codigo_google(str(code))
    except Exception as e:
        st.error(f"⚠️ No se pudo completar el inicio con Google: {e}")
        return
    email = str(info.get("email", "") or "").strip()
    sub = str(info.get("sub", "") or "").strip()
    nombre_google = str(info.get("name", "") or "").strip()
    if not sub:
        st.error("⚠️ Google no devolvió una identidad válida.")
        return

    perfil = core.perfil_por_identidad(datos, "google", sub)
    if perfil is None and email:
        perfil = core.perfil_por_email(datos, email)
    creado = False
    if perfil is None:  # primer inicio con Google: crear perfil automáticamente
        base = nombre_google or (email.split("@")[0] if email else "google_user")
        nombre, n = base, 2
        while perfil_por_nombre(datos, nombre) is not None:
            nombre = f"{base}{n}"
            n += 1
        perfil = {"id": core.generar_id({p["id"] for p in datos["perfiles"]},
                                        prefijo="p_"),
                  "nombre": nombre, "password_hash": "", "es_confianza": False,
                  "es_admin": False}
        datos["perfiles"].append(perfil)
        creado = True
    core.vincular_identidad(datos, "google", sub, email, perfil["id"])
    guardar(datos)
    st.session_state["usuario"] = perfil["nombre"]
    st.session_state["pagina"] = "📦 Catálogo"
    _escribir_cookie(_crear_token_sesion(perfil["id"]))
    if creado:
        st.toast(f"👋 ¡Bienvenido, {perfil['nombre']}! "
                 "Perfil creado con tu cuenta de Google.")
    else:
        st.toast(f"👋 Sesión iniciada con Google — {perfil['nombre']}")


# =============================================================================
# PANTALLA DE LOGIN / REGISTRO
# =============================================================================

def pantalla_login(datos: dict):
    """Muestra login y registro; solo se llega al resto de la app con sesión."""
    st.markdown("## 🌿 TerpsXHunter")
    st.caption("Inicia sesión con tu perfil para votar. ¿No tienes cuenta? "
               "Crea una abajo (solo nombre y contraseña).")

    # ---- Login (con autocompletado nativo del navegador) ----
    with st.form("login"):
        l_nombre = st.text_input("Nombre de usuario", autocomplete="username")
        l_pw = st.text_input("Contraseña", type="password",
                             autocomplete="current-password")
        l_recordar = st.checkbox("🔒 Recordarme en este dispositivo", value=True)
        entrar = st.form_submit_button("🔓 Entrar", type="primary",
                                       width="stretch")
    if entrar:
        nombre = l_nombre.strip()
        perfil = perfil_por_nombre(datos, nombre)
        if perfil is None:
            st.error(f"'{nombre}' no existe. Regístrate abajo.")
        elif perfil.get("password_hash"):
            if verificar_password(l_pw, perfil["password_hash"]):
                st.session_state["usuario"] = perfil["nombre"]
                if l_recordar:
                    _escribir_cookie(_crear_token_sesion(perfil["id"]))
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        else:
            # Cuenta antigua sin contraseña: la primera persona que ponga una
            # la reclama (sistema de amigos; el admin puede resetearla después).
            if l_pw:
                perfil["password_hash"] = hash_password(l_pw)
                guardar(datos)
                st.session_state["usuario"] = perfil["nombre"]
                if l_recordar:
                    _escribir_cookie(_crear_token_sesion(perfil["id"]))
                st.success(f"✅ Contraseña asignada a '{nombre}'. ¡Bienvenido!")
                st.rerun()
            else:
                st.warning(f"'{nombre}' todavía no tiene contraseña: escríbela "
                           "aquí para reclamar la cuenta.")

    # ---- Continuar con Google (OAuth; complementa al login tradicional) ----
    cfg_google = _credenciales_google()
    if cfg_google:
        url_google = _url_autorizacion_google()
        if url_google:
            st.markdown("**o continúa con**")
            st.markdown(
                '<a href="' + _html.escape(url_google) + '" target="_self" '
                'class="st-key-btn_google" rel="nofollow">'
                '<span class="g_logo">G</span> Continuar con Google</a>',
                unsafe_allow_html=True)

    st.divider()

    # ---- Registro (autocompletado 'new-password' evita el autofill viejo) ----
    st.markdown("### ✨ ¿Nuevo? Crea tu perfil")
    with st.form("registro"):
        r_nombre = st.text_input("Nombre de usuario", key="reg_nombre",
                                 autocomplete="username")
        r_pw = st.text_input("Contraseña (mínimo 4 caracteres)", type="password",
                             key="reg_pw", autocomplete="new-password")
        r_pw2 = st.text_input("Repite la contraseña", type="password", key="reg_pw2",
                              autocomplete="new-password")
        crear = st.form_submit_button("➕ Crear cuenta", width="stretch")
    if crear:
        nombre = r_nombre.strip()
        if not nombre:
            st.error("Escribe un nombre de usuario.")
        elif len(r_pw) < 4:
            st.error("La contraseña debe tener al menos 4 caracteres.")
        elif r_pw != r_pw2:
            st.error("Las contraseñas no coinciden.")
        elif perfil_por_nombre(datos, nombre) is not None:
            st.error("Ya existe un perfil con ese nombre.")
        else:
            nuevo_id = core.generar_id({p["id"] for p in datos["perfiles"]},
                                       prefijo="p_")
            datos["perfiles"].append({
                "id": nuevo_id,
                "nombre": nombre,
                "password_hash": hash_password(r_pw),
            })
            guardar(datos)
            st.session_state["usuario"] = nombre
            _escribir_cookie(_crear_token_sesion(nuevo_id))
            st.rerun()

    sin_pw = perfiles_sin_password(datos)
    if sin_pw:
        st.caption(f"Cuentas sin contraseña (reclámalas desde el login): "
                   f"{', '.join(sin_pw)}")

    st.divider()
    if st.button("← Volver como invitado", width="stretch"):
        st.session_state["pagina"] = "📦 Catálogo"
        st.rerun()


# =============================================================================
# SECCIÓN 1 — NUEVA CATA (crear producto + votar; solo admin)
# =============================================================================

def seccion_nueva_cata(datos: dict):
    st.markdown("## ➕ Nueva Cata")
    # Crear productos: administradores y gente de confianza (los demás votan)
    if not es_profesional(datos):
        st.warning("🔒 Solo los **administradores** y la **gente de confianza** "
                   "pueden crear productos nuevos. Tú puedes votar desde "
                   "**🎯 Por votar** o abriendo la ficha de cualquier producto en "
                   "**📦 Catálogo**.")
        return
    perfil = perfil_activo(datos)
    if perfil is None:
        st.info("Primero crea tu perfil en la sección **👥 Perfiles** "
                "(o elige uno en el menú lateral).")
        return

    st.caption(f"Votando como **{perfil['nombre']}** — si la muestra ya existe, "
               "se añadirá o actualizará tu voto.")

    nombre = st.text_input("Nombre de la muestra *", key="f_nombre")
    cata_existente = cata_por_nombre(datos, nombre) if nombre else None

    if cata_existente is not None:
        n_votos = len(core.votos_validos(cata_existente))
        voto_previo = core.voto_de_perfil(cata_existente, perfil["id"])
        if voto_previo is not None:
            st.info(f"✅ Muestra existente ({n_votos} voto"
                    f"{'s' if n_votos != 1 else ''}) — **se ACTUALIZARÁ tu voto** "
                    "(los sliders se precargaron con tu voto anterior).")
        else:
            st.success(f"✓ Muestra existente ({n_votos} voto"
                       f"{'s' if n_votos != 1 else ''}) — **se AÑADIRÁ tu voto**.")
    else:
        st.caption("Si el nombre ya existe en el catálogo, se reutilizará la muestra.")

    # ---- Precarga de metadatos (ANTES de instanciar los widgets) ----
    # Si la cata existe, se reinician las keys de los widgets para que tomen
    # los valores de la cata; si el usuario ya los tocó, se respetan.
    nombres_prod = [p["nombre"] for p in datos["productores"]]
    valores_prod = [""] + nombres_prod
    idx_prod = idx_pais = idx_tipo = idx_anio = idx_temp = 0
    coment_inicial = ""
    if cata_existente is not None:
        flag = f"f_precargado_{nombre.strip().lower()}"
        if not st.session_state.get(flag):
            for k in ("f_productor", "f_pais", "f_anio", "f_temp",
                      "f_tipo", "f_comentarios"):
                st.session_state.pop(k, None)  # reiniciar widgets con la precarga
            st.session_state.pop("nc__precargado", None)  # re-precargar sliders
            st.session_state[flag] = True
        if cata_existente.get("pais") in core.PAISES_VALIDOS:
            idx_pais = core.PAISES_VALIDOS.index(cata_existente["pais"])
        if cata_existente.get("tipo") in core.TIPOS_VALIDOS:
            idx_tipo = core.TIPOS_VALIDOS.index(cata_existente["tipo"])
        anio_existente = str(cata_existente.get("anio", "") or "")
        if anio_existente in core.anios_produccion():
            idx_anio = core.anios_produccion().index(anio_existente)
        temp_existente = str(cata_existente.get("temporada", "") or "")
        if temp_existente in core.TEMPORADAS_VALIDAS:
            idx_temp = core.TEMPORADAS_VALIDAS.index(temp_existente)
        prod = str(cata_existente.get("productor", "")).strip()
        if prod in nombres_prod:
            idx_prod = nombres_prod.index(prod) + 1
        coment_inicial = cata_existente.get("comentarios", "")
    else:
        for k in [k for k in list(st.session_state) if k.startswith("f_precargado_")]:
            st.session_state.pop(k, None)

    # ---- Metadatos ----
    c1, c2 = st.columns(2)
    with c1:
        productor = st.selectbox("Productor", valores_prod, index=idx_prod,
                                 key="f_productor")
    with c2:
        pais = st.selectbox("País de origen", core.PAISES_VALIDOS, index=idx_pais,
                            key="f_pais")
    c3, c4, c5 = st.columns(3)
    with c3:
        tipo = st.selectbox("Tipo", core.TIPOS_VALIDOS, index=idx_tipo, key="f_tipo")
    with c4:
        anio = st.selectbox("Año de producción", core.anios_produccion(),
                            index=idx_anio, key="f_anio")
    with c5:
        temp_opciones = [""] + core.TEMPORADAS_VALIDAS
        temporada = st.selectbox("Temporada", temp_opciones, index=idx_temp,
                                 key="f_temp",
                                 format_func=lambda x: "Sin temporada" if x == "" else x)

    # ---- Foto del material ----
    upload = st.file_uploader("📷 Foto del material (opcional)",
                              type=["png", "jpg", "jpeg", "webp", "bmp", "gif"],
                              key="f_foto")
    if upload is not None:
        st.image(upload, width=160)

    # ---- Bloques de sliders (desplegables; el 1º abierto) ----
    st.divider()
    voto_precargar = None
    if cata_existente is not None:
        voto_precargar = core.voto_de_perfil(cata_existente, perfil["id"])
    voto_scores = render_sliders_blocks(voto_precargar=voto_precargar,
                                        prefijo="nc_")

    # ---- Nota en vivo (se recalcula con cada rerun; sin scroll infinito) ----
    notas_bloques10, final10 = core.calcular_notas(voto_scores)
    with st.container(border=True):
        st.metric("💛 Tu nota", f"{final10 * 10:.1f} / 100",
                  help="En vivo: se actualiza al mover los sliders")
        st.caption(" | ".join(
            f"{m['titulo']} {notas_bloques10[m['clave']] * 10:.1f}"
            for m in core.BLOQUES))

    # ---- Comentarios ----
    comentarios = st.text_area("Comentarios o notas adicionales",
                               value=coment_inicial, key="f_comentarios")

    # ---- Guardar (CTA principal; sticky al fondo en móvil por CSS) ----
    if st.button("💾 Guardar voto", type="primary", width="stretch",
                 key="btn_guardar_voto"):
        if not nombre.strip():
            st.error("El nombre de la muestra es obligatorio.")
            st.stop()
        guardar_voto(datos, perfil, nombre, productor, pais, tipo, anio,
                     temporada, comentarios, upload, voto_scores)
        st.rerun()


def guardar_voto(datos, perfil, nombre, productor, pais, tipo, anio, temporada,
                 comentarios, upload, scores):
    """Upsert: crea la cata si no existe y añade/actualiza el voto del perfil."""
    cata = cata_por_nombre(datos, nombre)
    if cata is None:
        rid = core.generar_id({c.get("id") for c in datos["catas"]})
        foto, foto_b64 = guardar_foto_upload(upload, rid)
        cata = {"id": rid, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "nombre": nombre.strip(), "productor": productor,
                "pais": pais, "anio": anio, "temporada": temporada,
                "tipo": tipo, "comentarios": comentarios,
                "foto": foto, "foto_b64": foto_b64, "votos": []}
        datos["catas"].append(cata)
        accion = "nueva"
    else:
        cata["productor"] = productor or cata.get("productor", "")
        cata["pais"] = pais
        cata["anio"] = anio
        cata["temporada"] = temporada
        cata["tipo"] = tipo
        cata["comentarios"] = comentarios
        if upload is not None:
            cata["foto"], cata["foto_b64"] = guardar_foto_upload(upload, cata["id"])
        accion = "existente"

    resultado = core.upsert_voto(cata, perfil["id"], scores)
    guardar(datos)
    for k in list(st.session_state.keys()):
        if k.startswith(("f_", "nc_")):
            del st.session_state[k]
    st.success(f"✅ Voto de {perfil['nombre']} "
               f"{'ACTUALIZADO' if resultado == 'actualizado' else 'guardado'} — "
               f"{cata['nombre']} · {core._flotante(core.voto_de_perfil(cata, perfil['id']).get('nota_final')):.1f}/100")


# =============================================================================
# SECCIÓN 1b — POR VOTAR (pendientes del perfil activo)
# =============================================================================

# Caducidad visual: los productos con más de DIAS_CADUCIDAD desde su alta
# (campo 'fecha') salen de "Por votar" y se archivan en el Catálogo.
DIAS_CADUCIDAD = 30


@st.fragment
def lista_por_votar(datos: dict, perfil: dict, perfil_id: str):
    """Grid de pendientes como FRAGMENTO: al descartar/deshacer se re-renderiza
    SOLO esta lista (recarga parcial ultrarrápida, sin SELECT a Supabase ni
    recarga del resto de la página). El cálculo de pendientes vive DENTRO del
    fragmento para que la tarjeta descartada desaparezca al instante."""
    descartados_ids = core.ids_descartados_por(datos, perfil_id)
    todos_pend = [c for c in datos["catas"]
                  if core.voto_de_perfil(c, perfil_id) is None]
    pendientes = [c for c in todos_pend
                  if c["id"] not in descartados_ids
                  and core.es_reciente(c, DIAS_CADUCIDAD)]
    caducados = [c for c in todos_pend
                 if c["id"] not in descartados_ids
                 and not core.es_reciente(c, DIAS_CADUCIDAD)]
    n_descartados = len([c for c in todos_pend if c["id"] in descartados_ids])

    st.caption(f"Votando como **{perfil['nombre']}** — {len(pendientes)} producto"
               f"{'s' if len(pendientes) != 1 else ''} por votar.")
    if caducados or n_descartados:
        partes = []
        if caducados:
            partes.append(f"⏳ {len(caducados)} con más de {DIAS_CADUCIDAD} días "
                          f"(disponibles en el Catálogo)")
        if n_descartados:
            partes.append(f"🙈 {n_descartados} descartado"
                          f"{'s' if n_descartados != 1 else ''}")
        st.caption("Ocultos: " + " · ".join(partes) + ".")

    if not pendientes:
        if todos_pend:
            st.success("🎉 ¡Ya has votado o descartado todo lo reciente! "
                       "Busca productos antiguos en el 📦 Catálogo.")
        else:
            st.success("🎉 ¡Ya has votado todos los productos del catálogo!")
        return

    # ---- Orden de la lista (recarga parcial: el selectbox vive en el fragmento)
    orden = st.selectbox("Ordenar por", ["⭐ Mejor nota primero",
                                         "🆕 Más reciente", "🔤 Alfabético"],
                         key="pv_orden")
    if orden == "⭐ Mejor nota primero":
        pendientes.sort(key=lambda c: (-core.nota_media(c),
                                       str(c.get("nombre", "")).lower()))
    elif orden == "🆕 Más reciente":
        pendientes.sort(key=lambda c: (core.dias_edad(c) or 0,
                                       str(c.get("nombre", "")).lower()))
    else:
        pendientes.sort(key=lambda c: str(c.get("nombre", "")).lower())

    # Paginación: arranca en 12 tarjetas; 'Mostrar más' amplía (fragmento)
    n_max = st.session_state.get("pv_n", 12)
    mostrar = pendientes[:n_max]

    # Grid responsive 3-2-1 (misma regla CSS que el Catálogo)
    with st.container(key="grid_por_votar"):
        for i in range(0, len(mostrar), 3):
            fila = mostrar[i:i + 3]
            cols = st.columns(3)
            for col, cata in zip(cols, fila):
                with col:
                    with st.container(border=True, key=f"card_{cata['id']}"):
                        tarjeta_por_votar(cata, datos, perfil_id)
    if len(pendientes) > n_max:
        if st.button(f"⬇️ Mostrar más ({len(pendientes) - n_max} restantes)",
                     key="pv_mas", width="stretch"):
            st.session_state["pv_n"] = n_max + 12

    # ---- Descartados: recuperar si fue accidental (recarga parcial) ----
    descartadas = [c for c in todos_pend if c["id"] in descartados_ids]
    if descartadas:
        with st.expander(f"🙈 Descartadas ({len(descartadas)}) — recuperar"):
            for c in descartadas:
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.markdown(f"**{_html.escape(str(c.get('nombre', '—')))}**")
                with c2:
                    if st.button("↩ Recuperar", key=f"pv_undo_{c['id']}",
                                 width="stretch"):
                        core.quitar_descarte(datos, c["id"], perfil_id)
                        guardar(datos)
                        st.toast(f"↩ '{c.get('nombre', '')}' de vuelta en tu lista")
                        try:
                            st.rerun(scope="fragment")
                        except Exception:
                            pass


def tarjeta_por_votar(cata: dict, datos: dict, perfil_id: str):
    """Tarjeta del grid 'Por votar': foto, nombre, productor, chips, días
    restantes y botones '🗳 Votar' + '🙈 No lo probé' (grid 3-2-1 columnas).
    Vive DENTRO del fragmento lista_por_votar: al descartar, el rerun con
    scope='fragment' recalcula la lista y esta tarjeta desaparece."""
    nombre_txt = str(cata.get("nombre", "—"))
    media = core.nota_media(cata)
    n_votos = len(core.votos_validos(cata))
    nombre = _html.escape(str(cata.get("nombre", "—")))
    productor = _html.escape(str(cata.get("productor", "") or "—"))
    color_nota = core.color_nota(media / 10)

    foto_html = foto_base64(core.resolver_ruta_foto(cata.get("foto")), px=84,
                            b64=cata.get("foto_b64", ""))
    if not foto_html:
        foto_html = placeholder_imagen(84, "🌿")

    chips_row = [chip(_html.escape(str(cata.get("tipo", "—"))),
                      core.COLOR_TIPO.get(cata.get("tipo", ""), "#444444"))]
    if cata.get("pais"):
        chips_row.append(chip(_html.escape(str(cata["pais"])),
                              core.COLOR_PAIS.get(cata["pais"], "#444444")))
    if cata.get("anio"):
        chips_row.append(chip(f"📅 {cata['anio']}", core.COLOR_ANIO))

    # Badge de caducidad visual: días que quedan para salir de "Por votar"
    badge_html = ""
    edad = core.dias_edad(cata)
    if edad is not None:
        restantes = DIAS_CADUCIDAD - edad
        if restantes <= 0:
            badge_html = ("<div style='font-size:11px;font-weight:700;"
                          "color:#e05c5c;margin-top:3px'>⏳ caducado</div>")
        else:
            color_badge = "#34d17b" if restantes > 7 else (
                "#e8c35a" if restantes > 3 else "#e05c5c")
            badge_html = (f"<div style='font-size:11px;font-weight:700;"
                          f"color:{color_badge};margin-top:3px'>"
                          f"⏳ {restantes} día{'s' if restantes != 1 else ''}</div>")

    # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
    html_tarjeta = (
        '<div style="display:flex;gap:10px;align-items:flex-start;width:100%">'
        '<div style="flex:0 0 auto">' + foto_html + '</div>'
        '<div style="flex:1;min-width:0">'
        f'<div style="font-weight:700;font-size:15px;color:#F2F5F9;line-height:1.25">{nombre}</div>'
        f'<div style="font-size:11.5px;color:#8B93A1;margin-top:1px">{productor}</div>'
        '<div style="margin-top:4px">' + '  '.join(chips_row) + '</div>'
        + badge_html
        + '</div>'
        '<div style="flex:0 0 auto;text-align:right;min-width:42px">'
        f'<div style="font-size:20px;font-weight:800;color:{color_nota};line-height:1.1">{media:.1f}</div>'
        + f'<div style="font-size:10px;color:#6B7480">{n_votos} voto'
        + ('s' if n_votos != 1 else '') + '</div>'
        + '</div></div>'
    )
    st.markdown(html_tarjeta, unsafe_allow_html=True)

    # Botones apilados a ancho completo: Votar (CTA) y 'No lo probé'
    if st.button("🗳 Votar", type="primary", key=f"pv_votar_{cata['id']}",
                 width="stretch"):
        st.session_state["votar_id"] = cata["id"]
        st.rerun(scope="app")  # cambio de vista: formulario a ancho completo
    if st.button("🙈 No lo probé", key=f"pv_no_{cata['id']}",
                 width="stretch"):
        if core.descartar_cata(datos, cata["id"], perfil_id) == "nuevo":
            guardar(datos)  # solo persiste si no estaba ya descartada
        st.toast(f"🙈 '{nombre_txt}' marcado como no probado")
        try:
            # Segundo pase del fragmento: recalcula la lista y esta tarjeta
            # desaparece SIN recargar la app (recarga parcial ultrarrápida).
            st.rerun(scope="fragment")
        except Exception:
            # Solo ocurre en contextos de testing (AppTest) donde el click
            # no dispara un fragment rerun aislado; en el navegador nunca.
            pass


def formulario_voto(datos: dict, cata: dict, perfil: dict, perfil_id: str):
    """Formulario de votación a ancho completo dentro de UN st.form:
    mover los sliders NO recarga la página (envían sus valores solo al
    pulsar un botón del form); la nota se recalcula al pulsar 'Ver mi nota'."""
    nombre = str(cata.get("nombre", "—"))
    st.markdown(f"### 🗳 Votar **{nombre}**")
    meta_txt = " · ".join(x for x in [
        str(cata.get("tipo", "")), str(cata.get("productor", "")),
        str(cata.get("pais", "")),
        f"📅 {cata['anio']}" if cata.get("anio") else ""] if x)
    st.caption(f"{meta_txt}  —  votando como **{perfil['nombre']}**")
    st.caption("Puntúa cada bloque en su pestaña (1-100).")

    prefijo_pv = f"pv_{cata['id']}_"
    with st.form(key=f"form_voto_{cata['id']}"):
        render_sliders_blocks(prefijo=prefijo_pv)  # pestañas, sin expanders
        coment = st.text_area("Comentarios (opcional)",
                              key=f"pv_coment_{cata['id']}")
        # Nota: se calcula con los valores ACTUALES del form (los del último
        # submit). Mover sliders no dispara recargas: eso es lo que ahorra.
        scores = obtener_scores_actuales(prefijo_pv)
        _, nota_final10 = core.calcular_notas(scores)
        nota_final = nota_final10 * 10
        color_nota = core.color_nota(nota_final10)
        with st.container(key="pv_nota_guardar"):
            # Nota final con color semántico + barra de progreso (más intuitiva)
            st.markdown(
                f'<div style="padding:14px 16px;border-radius:14px;'
                f'background:linear-gradient(180deg,#171D27,#141920);'
                f'border:1px solid {color_nota}55;margin-bottom:10px">'
                f'<div style="display:flex;align-items:baseline;justify-content:space-between;'
                f'margin-bottom:8px">'
                f'<span style="font-size:12px;color:#A5AEB8;font-weight:700;'
                f'letter-spacing:0.4px">💛 TU NOTA FINAL</span>'
                f'<span style="font-size:30px;font-weight:800;color:{color_nota};'
                f'line-height:1">{nota_final:.1f}'
                f'<span style="font-size:12px;color:#7A8391;font-weight:600">/100</span>'
                f'</span></div>'
                f'<div style="height:6px;border-radius:99px;background:#1B2129;overflow:hidden">'
                f'<div style="height:100%;width:{nota_final:.0f}%;'
                f'background:linear-gradient(90deg,{color_nota},{color_nota});'
                f'border-radius:99px;transition:width .3s ease"></div></div></div>',
                unsafe_allow_html=True)
            c1, c2 = st.columns([2, 1])
            with c1:
                ver_nota = st.form_submit_button("👁 Ver mi nota",
                                                 width="stretch")
            with c2:
                guardar_voto = st.form_submit_button("💾 Guardar mi voto",
                                                     type="primary",
                                                     width="stretch")
            no_probe = st.form_submit_button("🙈 No lo probé",
                                             width="stretch")

    if guardar_voto:
        scores = obtener_scores_actuales(prefijo_pv)
        resultado = core.upsert_voto(cata, perfil_id, scores)
        if coment.strip():
            cata["comentarios"] = (cata.get("comentarios", "") +
                                   "\n\n" + coment.strip()).strip()
        core.quitar_descarte(datos, cata["id"], perfil_id)
        guardar(datos)
        for k in list(st.session_state.keys()):
            if k.startswith(prefijo_pv):
                del st.session_state[k]
        st.session_state.pop("votar_id", None)
        nota = core._flotante(
            core.voto_de_perfil(cata, perfil_id).get("nota_final"))
        st.toast(f"✅ Voto de {perfil['nombre']} "
                 f"{'ACTUALIZADO' if resultado == 'actualizado' else 'guardado'} — "
                 f"{nombre} · {nota:.1f}/100")
        st.rerun()
    if no_probe:
        core.descartar_cata(datos, cata["id"], perfil_id)
        guardar(datos)
        st.session_state.pop("votar_id", None)
        st.toast(f"🙈 '{nombre}' marcado como no probado")
        st.rerun()


def seccion_por_votar(datos: dict):
    st.markdown("## 🎯 Por votar")
    perfil = perfil_activo(datos)
    if perfil is None:
        st.info("Primero crea o elige tu perfil en el menú lateral (👤 Votando como).")
        return
    perfil_id = perfil["id"]

    # ---- Formulario de votación abierto (a ancho completo, sin grid) ----
    votar_id = st.session_state.get("votar_id")
    if votar_id:
        cata = next((c for c in datos["catas"] if c.get("id") == votar_id), None)
        if cata is not None:
            st.divider()
            formulario_voto(datos, cata, perfil, perfil_id)
            if st.button("← Volver a la lista", key="pv_volver"):
                st.session_state.pop("votar_id", None)
                st.rerun()
            return
        st.session_state.pop("votar_id", None)  # cata inexistente: limpiar

    # ---- Grid de pendientes como FRAGMENTO (descarte con recarga parcial) ----
    lista_por_votar(datos, perfil, perfil_id)


# =============================================================================
# SECCIÓN 2 — CATÁLOGO (Productos + ficha editable)
# =============================================================================

@st.dialog("🗑 Eliminar producto")
def dialogo_eliminar_producto(datos: dict, cata: dict):
    """Confirmación modal para eliminar un producto y TODOS sus votos (solo admin)."""
    st.write(f"¿Eliminar **'{cata.get('nombre', '')}**' y sus "
             f"{len(core.votos_validos(cata))} voto"
             f"{'s' if len(core.votos_validos(cata)) != 1 else ''}?")
    st.warning("Esta acción no se puede deshacer.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Sí, eliminar", type="primary", width="stretch"):
            datos["catas"] = [c for c in datos["catas"] if c.get("id") != cata.get("id")]
            guardar(datos)
            st.success("Producto eliminado.")
            st.rerun()
    with c2:
        if st.button("Cancelar", width="stretch"):
            st.rerun()


def tarjeta_catalogo(cata: dict, datos: dict, admin: bool):
    """Tarjeta compacta del catálogo (grid 3-2-1 columnas):
    foto, nombre, productor, chips, nota y botón 'Abrir ficha' compacto."""
    media = core.nota_media(cata)
    n_votos = len(core.votos_validos(cata))
    nombre = _html.escape(str(cata.get("nombre", "—")))
    productor = _html.escape(str(cata.get("productor", "") or "—"))
    color_nota = core.color_nota(media / 10)

    # Foto del material (thumbnail)
    foto_html = foto_base64(core.resolver_ruta_foto(cata.get("foto")), px=84,
                            b64=cata.get("foto_b64", ""))
    if not foto_html:
        foto_html = placeholder_imagen(84, "🌿")

    # Chips: tipo / país / año
    chips_row = [chip(_html.escape(str(cata.get("tipo", "—"))),
                      core.COLOR_TIPO.get(cata.get("tipo", ""), "#444444"))]
    if cata.get("pais"):
        chips_row.append(chip(_html.escape(str(cata["pais"])),
                              core.COLOR_PAIS.get(cata["pais"], "#444444")))
    if cata.get("anio"):
        chips_row.append(chip(f"📅 {cata['anio']}", core.COLOR_ANIO))

    # Nota profesional (si existe)
    prof_html = ""
    nota_prof = core.nota_media_profesional(cata, datos) if datos else None
    if nota_prof is not None:
        prof_html = (f"<div style='font-size:12px;font-weight:700;color:#e8c35a;"
                     f"line-height:1.1;margin-top:2px'>⭐ {nota_prof:.1f}</div>")

    # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
    html_tarjeta = (
        '<div style="display:flex;gap:10px;align-items:flex-start;width:100%">'
        '<div style="flex:0 0 auto">' + foto_html + '</div>'
        '<div style="flex:1;min-width:0">'
        f'<div style="font-weight:700;font-size:15px;color:#F2F5F9;line-height:1.25">{nombre}</div>'
        f'<div style="font-size:11.5px;color:#8B93A1;margin-top:1px">{productor}</div>'
        '<div style="margin-top:4px">' + '  '.join(chips_row) + '</div>'
        '</div>'
        '<div style="flex:0 0 auto;text-align:right;min-width:42px">'
        f'<div style="font-size:20px;font-weight:800;color:{color_nota};line-height:1.1">{media:.1f}</div>'
        + prof_html
        + f'<div style="font-size:10px;color:#6B7480">{n_votos} voto'
        + ('s' if n_votos != 1 else '') + '</div>'
        + '</div></div>'
    )
    st.markdown(html_tarjeta, unsafe_allow_html=True)

    # 'Abrir ficha' se convierte (por CSS) en un overlay invisible que cubre
    # TODA la tarjeta → tocar foto, nombre, chips o el hueco abre la ficha,
    # sin zona muerta. El 🗑 del admin queda por encima y sigue pulsable.
    if st.button("📂 Abrir ficha", key=f"abrir_{cata['id']}",
                 width="stretch"):
        st.session_state["ficha_id"] = cata["id"]
        st.rerun()
    if admin:
        if st.button("🗑 Eliminar", key=f"del_{cata['id']}",
                     width="stretch"):
            dialogo_eliminar_producto(datos, cata)


def seccion_catalogo(datos: dict):
    st.markdown("## 📦 Catálogo")
    st.caption("Busca cualquier producto, filtra y abre su ficha para editar "
               "especificaciones y votaciones.")
    admin = es_admin(datos)
    if admin:
        st.caption("👑 **Modo admin:** puedes eliminar productos con el botón 🗑.")

    # ---- Ficha abierta (persistente entre reruns; se cierra con el botón) ----
    ficha_id = st.session_state.get("ficha_id")
    if ficha_id:
        cata = next((c for c in datos["catas"] if c.get("id") == ficha_id), None)
        if cata is not None:
            ficha_producto(datos, cata)
            st.divider()
            if st.button("← Volver al listado"):
                st.session_state.pop("ficha_id", None)
                st.rerun()
            return

    # Filtros + grid como FRAGMENTO: cambiar un filtro re-renderiza solo este
    # bloque (sin recargar sidebar ni releer datos).
    _catalogo_grid(datos, admin)


@st.fragment
def _catalogo_grid(datos: dict, admin: bool):
    """Buscador + filtros + grid del catálogo (recarga parcial al filtrar)."""
    texto = st.text_input("🔍 Buscar por nombre o productor", key="cat_buscar")
    c1, c2 = st.columns(2)
    with c1:
        filtro_tipo = st.pills("Tipo", ["Todos"] + core.TIPOS_VALIDOS,
                               key="cat_tipo", selection_mode="single",
                               default="Todos")
    with c2:
        filtro_pais = st.pills("País", ["Todos"] + core.PAISES_VALIDOS,
                               key="cat_pais", selection_mode="single",
                               default="Todos")
    filtro_anio = st.selectbox("Año", ["Todos"] + core.anios_produccion(),
                               key="cat_anio")
    perfil = perfil_activo(datos)
    filtro_voto = "Todos"
    if perfil is not None:
        filtro_voto = st.pills("Mi voto", ["Todos", "Sin votar", "Ya votado"],
                               key="cat_voto", selection_mode="single",
                               default="Todos")

    lista = []
    for c in datos["catas"]:
        if filtro_tipo != "Todos" and c.get("tipo") != filtro_tipo:
            continue
        if filtro_pais != "Todos" and c.get("pais") != filtro_pais:
            continue
        if filtro_anio != "Todos" and str(c.get("anio", "")) != filtro_anio:
            continue
        if texto and texto.lower() not in (c.get("nombre", "") + " " +
                                           c.get("productor", "")).lower():
            continue
        if perfil is not None and filtro_voto != "Todos":
            tiene_voto = core.voto_de_perfil(c, perfil["id"]) is not None
            if filtro_voto == "Sin votar" and tiene_voto:
                continue
            if filtro_voto == "Ya votado" and not tiene_voto:
                continue
        lista.append(c)
    lista.sort(key=lambda c: (-core.nota_media(c), str(c.get("nombre", "")).lower()))

    st.caption(f"{len(lista)} producto{'s' if len(lista) != 1 else ''}")
    if not lista:
        st.info("Sin productos con esos criterios.")
        return

    # Paginación: el grid arranca en 12 tarjetas; 'Mostrar más' amplía
    # (el botón vive en el fragmento → solo se re-renderiza este bloque).
    n_max = st.session_state.get("cat_n", 12)
    mostrar = lista[:n_max]

    # Grid de tarjetas: 3 por fila en desktop (CSS: 2 en tablets, 1 en móvil)
    with st.container(key="grid_catalogo"):
        for i in range(0, len(mostrar), 3):
            fila = mostrar[i:i + 3]
            cols = st.columns(3)
            for col, cata in zip(cols, fila):
                with col:
                    with st.container(border=True, key=f"card_{cata['id']}"):
                        tarjeta_catalogo(cata, datos, admin)
    if len(lista) > n_max:
        if st.button(f"⬇️ Mostrar más ({len(lista) - n_max} restantes)",
                     key="cat_mas", width="stretch"):
            st.session_state["cat_n"] = n_max + 12


@st.fragment
def seccion_comentarios(datos: dict, cata: dict):
    """Comentarios de usuarios (visibles para todos; publican la gente de
    confianza y los admins). Se usa en la ficha premium y en la de edición.
    FRAGMENTO: publicar un comentario solo re-renderiza este bloque."""
    perfil = perfil_activo(datos)
    st.divider()
    st.markdown("### 💬 Comentarios")
    comentarios_u = [cu for cu in cata.get("comentarios_usuarios", [])
                     if isinstance(cu, dict) and cu.get("texto")]
    if not comentarios_u:
        st.caption("Todavía no hay comentarios de la gente de confianza.")
    for cu in reversed(comentarios_u[-10:]):
        autor = _html.escape(str(cu.get("nombre", "—")))
        fecha = str(cu.get("fecha", ""))
        texto = _html.escape(str(cu.get("texto", "")))
        with st.container(border=True):
            st.markdown(f"**💬 {autor}** · {fecha}")
            st.markdown(texto)
    if es_profesional(datos) and perfil is not None:
        # Limpieza del input tras publicar: el flag se pone al enviar y aquí
        # (ANTES de instanciar el widget) se retira la key para que el
        # text_area se cree vacío en el siguiente pase del fragmento.
        if st.session_state.get(f"pub_ok_{cata['id']}"):
            st.session_state.pop(f"comentario_{cata['id']}", None)
            st.session_state.pop(f"pub_ok_{cata['id']}", None)
        st.markdown("---")
        nuevo_comentario = st.text_area("Tu comentario (como gente de confianza)",
                                        key=f"comentario_{cata['id']}")
        if st.button("📝 Publicar comentario", key=f"pub_com_{cata['id']}",
                     width="stretch"):
            if nuevo_comentario.strip():
                cata.setdefault("comentarios_usuarios", []).append({
                    "perfil_id": perfil["id"],
                    "nombre": perfil["nombre"],
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "texto": nuevo_comentario.strip()})
                guardar(datos)
                st.session_state[f"pub_ok_{cata['id']}"] = True  # limpiar input
                st.toast("✅ Comentario publicado.")
                try:
                    # Segundo pase del fragmento: el comentario nuevo aparece
                    # sin recargar toda la ficha (recarga parcial).
                    st.rerun(scope="fragment")
                except Exception:
                    # Solo ocurre en contextos de testing (AppTest); en el
                    # navegador el click ya dispara un fragment rerun válido.
                    pass
    elif perfil is None:
        st.caption("👥 Inicia sesión para comentar (solo gente de confianza y admins).")
    else:
        st.caption("👥 Solo la **gente de confianza** y los **admins** pueden comentar.")


def ficha_producto(datos: dict, cata: dict):
    """Ficha según el rol del usuario:
    - Admin -> vista de edición (formulario completo).
    - Invitado / usuarios sin permisos -> vista PREMIUM de presentación
      (HTML/CSS puro, sin inputs ni botones de gestión) + sus votaciones."""
    if es_admin(datos):
        ficha_edicion(datos, cata)
    else:
        ficha_premium(datos, cata)


def ficha_premium(datos: dict, cata: dict):
    """Ficha de PRESENTACIÓN para invitados y usuarios sin permisos de edición.
    Diseño responsive en 2 columnas:
      - Izquierda: imagen (use_container_width), nombre, productor, país, año,
        tipo y puntuación general (+ profesional).
      - Derecha: Votaciones y Comentarios.
    En móvil las columnas colapsan verticalmente (info arriba, interacciones
    debajo) vía CSS. Cero componentes de formulario para el producto; las
    votaciones están disponibles para quien tenga sesión (solo su voto)."""
    perfil = perfil_activo(datos)
    puede_votar = perfil is not None
    media = core.nota_media(cata)
    n_votos = len(core.votos_validos(cata))
    nombre = _html.escape(str(cata.get("nombre", "—")))
    productor = _html.escape(str(cata.get("productor", "") or "—"))
    color_nota = core.color_nota(media / 10)

    # Insignias: tipo / país / año / temporada
    chips_row = [chip(_html.escape(str(cata.get("tipo", "—"))),
                      core.COLOR_TIPO.get(cata.get("tipo", ""), "#444444"))]
    if cata.get("pais"):
        chips_row.append(chip(_html.escape(str(cata["pais"])),
                              core.COLOR_PAIS.get(cata["pais"], "#444444")))
    if cata.get("anio"):
        chips_row.append(chip(f"📅 {cata['anio']}", core.COLOR_ANIO))
    if cata.get("temporada"):
        chips_row.append(chip(f"◈ {cata['temporada']}",
                              core.COLOR_TEMPORADA.get(cata["temporada"], "#444444")))

    # Descripción en tarjeta (si hay comentarios). En UNA SOLA LÍNEA: si el
    # HTML interpolado lleva saltos de línea con indentación desigual, el
    # parser de markdown lo interpreta como código y se ve el HTML en crudo.
    coment_html = ""
    if cata.get("comentarios"):
        coment_html = (
            '<div style="background:#14181F;border:1px solid #262B33;'
            'border-radius:14px;padding:14px 16px;margin-top:14px">'
            '<div style="font-size:11px;color:#8B93A1;font-weight:700;'
            'letter-spacing:1px;margin-bottom:6px">📝 DESCRIPCIÓN</div>'
            '<div style="font-size:14px;color:#D8DEE8;line-height:1.6;'
            'white-space:pre-line">'
            + _html.escape(str(cata.get("comentarios", "")))
            + "</div></div>")

    # Nota profesional (gente de confianza + admins), si existe
    prof_txt = ""
    nota_prof = core.nota_media_profesional(cata, datos)
    if nota_prof is not None:
        n_prof = core.n_votos_profesionales(cata, datos)
        prof_txt = (f" · ⭐ profesional **{nota_prof:.1f}** "
                    f"({n_prof} voto{'s' if n_prof != 1 else ''})")

    with st.container(key="ficha_detalle"):
        col_izq, col_der = st.columns([1, 1.5])

        # ================= COLUMNA IZQUIERDA: título, puntuación e imagen =================
        with col_izq:
            # Título del producto PRIMERO (destacado, no enterrado bajo la foto)
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
                f'<div style="font-size:1.5rem;font-weight:800;color:#F2F5F9;'
                f'letter-spacing:-0.02em;line-height:1.15">{nombre}</div>'
                f'<div style="font-size:2.4rem;font-weight:800;color:{color_nota};'
                f'line-height:1;margin-left:auto">{media:.1f}</div></div>',
                unsafe_allow_html=True)
            st.markdown(f"🏭 **{productor}**")
            st.markdown(" ".join(chips_row), unsafe_allow_html=True)
            st.caption(f"{n_votos} voto{'s' if n_votos != 1 else ''}{prof_txt}")

            # Imagen del producto en tarjeta con altura contenida (no entierra el
            # título). En modo nube la foto es un b64 de la BD → se usa
            # foto_base64_fluid (que omite el archivo local); en local usa la ruta.
            foto_html = foto_base64_fluid(core.resolver_ruta_foto(cata.get("foto")),
                                          b64=cata.get("foto_b64", ""))
            if foto_html:
                st.markdown(f"<div style='max-width:420px'>{foto_html}</div>",
                            unsafe_allow_html=True)
            else:
                st.markdown(placeholder_imagen_fluid("🌿"), unsafe_allow_html=True)

            # Descripción en tarjeta (si existe)
            if coment_html:
                st.markdown(coment_html, unsafe_allow_html=True)

        # ================= COLUMNA DERECHA: votaciones + comentarios =================
        with col_der:
            st.markdown("### 🗳 Votaciones")
            if puede_votar and core.voto_de_perfil(cata, perfil["id"]) is None:
                with st.expander("🗳 Votar este producto", expanded=True):
                    prefijo_pv = f"prem_{cata['id']}_"
                    render_sliders_blocks(prefijo=prefijo_pv)
                    coment = st.text_area("Comentarios (opcional)",
                                          key=f"prem_coment_{cata['id']}")
                    if st.button("💾 Guardar mi voto", type="primary",
                                 key=f"prem_guardar_{cata['id']}"):
                        core.upsert_voto(cata, perfil["id"],
                                         obtener_scores_actuales(prefijo_pv))
                        if coment.strip():
                            cata["comentarios"] = (cata.get("comentarios", "") +
                                                   "\n\n" + coment.strip()).strip()
                        guardar(datos)
                        for k in list(st.session_state.keys()):
                            if k.startswith(prefijo_pv):
                                del st.session_state[k]
                        st.success(f"✅ Voto de {perfil['nombre']} guardado.")
                        st.rerun()

            votos = core.votos_validos(cata)
            if not votos:
                st.info("Este producto todavía no tiene votos.")
            else:
                for voto in votos:
                    nombre_votante = nombre_perfil(datos, voto.get("perfil_id"))
                    nota = core._flotante(voto.get("nota_final"))
                    es_mio = perfil is not None and voto.get("perfil_id") == perfil["id"]
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 1, 1])
                        with c1:
                            tag = " · **← tú**" if es_mio else ""
                            st.markdown(f"**👤 {nombre_votante}**{tag} · {voto.get('fecha', '')}")
                        with c2:
                            st.markdown(f"### {nota:.1f}")
                        if es_mio:
                            with c3:
                                if st.button("✏️ Editar", key=f"edit_btn_{cata['id']}_{voto['perfil_id']}"):
                                    st.session_state[f"edit_voto_{cata['id']}_{voto['perfil_id']}"] = True
                                    st.session_state.pop(
                                        f"ev_{cata['id']}_{voto['perfil_id']}_precargado", None)
                                    st.rerun()
                        # Desglose en expander
                        with st.expander("Ver desglose"):
                            det = voto.get("puntuaciones_detalle", {})
                            for meta in core.BLOQUES:
                                scores_b = det.get(meta["clave"], {})
                                linea = " · ".join(
                                    f"{etiqueta}: {core._flotante(scores_b.get(clave)):.1f}"
                                    for clave, etiqueta in meta["subs"])
                                st.markdown(f"**{meta['titulo']}** — {linea}")
                            st.caption("Bloques: " + " | ".join(
                                f"{m['titulo']} {core._flotante(voto.get('notas_bloques', {}).get(m['clave'])):.1f}"
                                for m in core.BLOQUES))
                        # Editor del voto propio (si ya existe)
                        if es_mio and st.session_state.get(
                                f"edit_voto_{cata['id']}_{voto['perfil_id']}"):
                            st.markdown("---")
                            st.markdown("**✏️ Editando tu voto**")
                            prefijo_ev = f"ev_{cata['id']}_{voto['perfil_id']}_"
                            render_sliders_blocks(
                                voto_precargar=voto.get("puntuaciones_detalle", {}),
                                prefijo=prefijo_ev)
                            if st.button("💾 Guardar mi voto", type="primary",
                                         key=f"save_edit_{cata['id']}_{voto['perfil_id']}"):
                                core.upsert_voto(cata, voto["perfil_id"],
                                                 obtener_scores_actuales(prefijo_ev))
                                guardar(datos)
                                del st.session_state[f"edit_voto_{cata['id']}_{voto['perfil_id']}"]
                                st.success("✅ Voto actualizado.")
                                st.rerun()
                        # Eliminar el voto propio
                        if es_mio:
                            c_del, _ = st.columns([1, 3])
                            with c_del:
                                if st.button("🗑 Eliminar mi voto",
                                             key=f"del_voto_{cata['id']}_{voto['perfil_id']}"):
                                    if core.quitar_voto(cata, voto["perfil_id"]):
                                        guardar(datos)
                                        st.success("Voto eliminado.")
                                        st.rerun()

            # Comentarios de la gente de confianza (visibles y publicables aquí)
            seccion_comentarios(datos, cata)


def ficha_edicion(datos: dict, cata: dict):
    """Vista ADMIN: formulario completo de edición del producto (especificaciones,
    foto) y gestión de TODAS las votaciones (cualquier perfil)."""
    es_admin_user = True
    perfil = perfil_activo(datos)
    editable = True                     # admin: siempre puede editar el producto
    puede_votar = True
    media = core.nota_media(cata)
    n_votos = len(core.votos_validos(cata))
    st.markdown(f"### 🗂 {cata.get('nombre', '—')}")
    prof_txt = ""
    nota_prof = core.nota_media_profesional(cata, datos)
    if nota_prof is not None:
        prof_txt = f" · ⭐ profesional **{nota_prof:.1f}**"
    st.caption(f"Nota media **{media:.1f} / 100**{prof_txt} · {n_votos} voto"
               f"{'s' if n_votos != 1 else ''} · {cata.get('productor', '—')}")

    tab_specs, tab_votos = st.tabs(["📋 Especificaciones", "🗳 Votaciones"])

    # ---------------- Especificaciones ----------------
    with tab_specs:
        col_foto, col_campos = st.columns([1, 2])
        with col_foto:
            mostrar_foto(cata.get("foto"), width=160, b64=cata.get("foto_b64", ""))
            upload = None
            if editable:
                upload = st.file_uploader("Cambiar foto",
                                          type=["png", "jpg", "jpeg", "webp", "bmp", "gif"],
                                          key=f"ficha_foto_{cata['id']}")
        with col_campos:
            nombres_prod = [p["nombre"] for p in datos["productores"]]
            e1, e2 = st.columns(2)
            with e1:
                nuevo_nombre = st.text_input("Nombre", cata.get("nombre", ""),
                                             key=f"ficha_nombre_{cata['id']}",
                                             disabled=not editable)
            with e2:
                nuevo_productor = st.selectbox(
                    "Productor", nombres_prod,
                    index=nombres_prod.index(cata.get("productor"))
                    if cata.get("productor") in nombres_prod else 0,
                    key=f"ficha_prod_{cata['id']}", disabled=not editable)
            e3, e4 = st.columns(2)
            with e3:
                nuevo_pais = st.selectbox("País", core.PAISES_VALIDOS,
                                          index=core.PAISES_VALIDOS.index(cata.get("pais"))
                                          if cata.get("pais") in core.PAISES_VALIDOS else 0,
                                          key=f"ficha_pais_{cata['id']}",
                                          disabled=not editable)
            with e4:
                nuevo_tipo = st.selectbox("Tipo", core.TIPOS_VALIDOS,
                                          index=core.TIPOS_VALIDOS.index(cata.get("tipo"))
                                          if cata.get("tipo") in core.TIPOS_VALIDOS else 0,
                                          key=f"ficha_tipo_{cata['id']}",
                                          disabled=not editable)
        anio_opciones = core.anios_produccion()
        anio_cata = str(cata.get("anio", "") or "")
        nuevo_anio = st.selectbox("Año de producción", anio_opciones,
                                  index=anio_opciones.index(anio_cata)
                                  if anio_cata in anio_opciones else 0,
                                  key=f"ficha_anio_{cata['id']}",
                                  disabled=not editable)
        temp_opciones = [""] + core.TEMPORADAS_VALIDAS
        temp_cata = str(cata.get("temporada", "") or "")
        nueva_temp = st.selectbox("Temporada (tirada del año)", temp_opciones,
                                  index=temp_opciones.index(temp_cata)
                                  if temp_cata in temp_opciones else 0,
                                  key=f"ficha_temp_{cata['id']}",
                                  format_func=lambda x: "Sin temporada" if x == "" else x,
                                  disabled=not editable)
        nuevos_comentarios = st.text_area("Comentarios", cata.get("comentarios", ""),
                                          key=f"ficha_coment_{cata['id']}",
                                          disabled=not editable)

        if editable and st.button("💾 Guardar especificaciones", type="primary",
                                  key=f"ficha_guardar_{cata['id']}"):
            if not nuevo_nombre.strip():
                st.error("El nombre no puede estar vacío.")
            else:
                cata["nombre"] = nuevo_nombre.strip()
                cata["productor"] = nuevo_productor
                cata["pais"] = nuevo_pais
                cata["anio"] = nuevo_anio
                cata["temporada"] = nueva_temp
                cata["tipo"] = nuevo_tipo
                cata["comentarios"] = nuevos_comentarios
                if upload is not None:
                    cata["foto"], cata["foto_b64"] = guardar_foto_upload(upload, cata["id"])
                guardar(datos)
                st.success("✅ Especificaciones guardadas.")
                st.rerun()

        # Eliminar producto (solo admin)
        if es_admin(datos):
            st.divider()
            if st.button("🗑 Eliminar este producto", key=f"ficha_del_{cata['id']}"):
                dialogo_eliminar_producto(datos, cata)

    # ---------------- Votaciones ----------------
    with tab_votos:
        # Añadir voto de un perfil pendiente (editor directo, sin flags rotos)
        pendientes = [p for p in datos["perfiles"]
                      if core.voto_de_perfil(cata, p["id"]) is None]
        if pendientes:
            c1, c2 = st.columns([1, 1])
            with c1:
                nuevo_perfil = st.selectbox("Añadir voto de",
                                            [p["nombre"] for p in pendientes],
                                            key=f"nuevo_voto_{cata['id']}")
            with c2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("➕ Añadir voto", key=f"btn_nuevo_voto_{cata['id']}",
                             width="stretch"):
                    p = perfil_por_nombre(datos, nuevo_perfil)
                    st.session_state[f"nv_{cata['id']}_{p['id']}"] = True
                    st.rerun()
        else:
            st.caption("✓ Todos los perfiles han votado este producto.")

        # Editor del voto NUEVO (para el perfil pendiente elegido)
        for p in pendientes:
            if st.session_state.get(f"nv_{cata['id']}_{p['id']}"):
                st.markdown(f"**🗳 Votar como {p['nombre']}**")
                with st.container(border=True):
                    prefijo_nv = f"nv_{cata['id']}_{p['id']}_"
                    render_sliders_blocks(prefijo=prefijo_nv)
                    if st.button("💾 Guardar voto", type="primary",
                                 key=f"save_nv_{cata['id']}_{p['id']}"):
                        core.upsert_voto(cata, p["id"],
                                         obtener_scores_actuales(prefijo_nv))
                        guardar(datos)
                        del st.session_state[f"nv_{cata['id']}_{p['id']}"]
                        st.success(f"✅ Voto de {p['nombre']} guardado.")
                        st.rerun()

        votos = core.votos_validos(cata)
        if not votos:
            st.info("Este producto todavía no tiene votos.")
            return

        for voto in votos:
            nombre_votante = nombre_perfil(datos, voto.get("perfil_id"))
            nota = core._flotante(voto.get("nota_final"))
            # Cada usuario gestiona su propio voto; el admin, todos
            puede_gestionar = es_admin_user or (
                perfil is not None and voto.get("perfil_id") == perfil["id"])
            with st.container(border=True):
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.markdown(f"**👤 {nombre_votante}**  ·  {voto.get('fecha', '')}")
                with c2:
                    st.markdown(f"### {nota:.1f}")
                if puede_gestionar:
                    with c3:
                        if st.button("✏️ Editar", key=f"edit_btn_{cata['id']}_{voto['perfil_id']}"):
                            st.session_state[f"edit_voto_{cata['id']}_{voto['perfil_id']}"] = True
                            # Re-precargar los sliders del editor al abrirlo
                            st.session_state.pop(
                                f"ev_{cata['id']}_{voto['perfil_id']}_precargado", None)
                            st.rerun()
                # Desglose en expander
                with st.expander("Ver desglose"):
                    det = voto.get("puntuaciones_detalle", {})
                    for meta in core.BLOQUES:
                        scores_b = det.get(meta["clave"], {})
                        linea = " · ".join(
                            f"{etiqueta}: {core._flotante(scores_b.get(clave)):.1f}"
                            for clave, etiqueta in meta["subs"])
                        st.markdown(f"**{meta['titulo']}** — {linea}")
                    st.caption(f"Bloques: " + " | ".join(
                        f"{m['titulo']} {core._flotante(voto.get('notas_bloques', {}).get(m['clave'])):.1f}"
                        for m in core.BLOQUES))
                # Editor de voto (sliders precargados)
                if st.session_state.get(f"edit_voto_{cata['id']}_{voto['perfil_id']}"):
                    st.markdown("---")
                    st.markdown(f"**✏️ Editando voto de {nombre_votante}**")
                    prefijo_ev = f"ev_{cata['id']}_{voto['perfil_id']}_"
                    scores = render_sliders_blocks(
                        voto_precargar=voto.get("puntuaciones_detalle", {}),
                        prefijo=prefijo_ev)
                    if st.button("💾 Guardar voto editado", type="primary",
                                 key=f"save_edit_{cata['id']}_{voto['perfil_id']}"):
                        core.upsert_voto(cata, voto["perfil_id"],
                                         obtener_scores_actuales(prefijo_ev))
                        guardar(datos)
                        del st.session_state[f"edit_voto_{cata['id']}_{voto['perfil_id']}"]
                        st.success("✅ Voto actualizado.")
                        st.rerun()
                # Eliminar voto (solo el dueño del voto o el admin)
                if puede_gestionar:
                    c_del, _ = st.columns([1, 3])
                    with c_del:
                        if st.button("🗑 Eliminar voto",
                                     key=f"del_voto_{cata['id']}_{voto['perfil_id']}"):
                            if core.quitar_voto(cata, voto["perfil_id"]):
                                guardar(datos)
                                st.success("Voto eliminado.")
                                st.rerun()

    # Comentarios de la gente de confianza (el admin también comenta)
    seccion_comentarios(datos, cata)


# =============================================================================
# SECCIÓN 3 — PRODUCTORES
# =============================================================================

def seccion_productores(datos: dict):
    st.markdown("## 🏭 Productores")
    admin = es_admin(datos)
    if admin:
        st.caption("👑 **Modo admin:** puedes añadir productores, cambiar sus fotos, "
                   "renombrarlos y eliminarlos.")
    else:
        st.caption("Puedes explorar los productores y ver sus productos.")

    # ---- Crear (solo admin) ----
    if admin:
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            nuevo_prod = st.text_input("Nombre del nuevo productor", key="prod_nuevo")
        with c2:
            nuevo_pais = st.selectbox("País", core.PAISES_VALIDOS, key="prod_pais")
        with c3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("➕ Añadir productor", type="primary", width="stretch"):
                nombre = nuevo_prod.strip()
                if not nombre:
                    st.error("Escribe un nombre.")
                elif any(p["nombre"].lower() == nombre.lower()
                         for p in datos["productores"]):
                    st.warning("Ya existe ese productor.")
                else:
                    datos["productores"].append({
                        "id": core.generar_id({p.get("id", "") for p in datos["productores"]},
                                              prefijo="pr_"),
                        "nombre": nombre, "foto": "", "pais": nuevo_pais})
                    guardar(datos)
                    st.rerun()

    st.divider()

    if not datos["productores"]:
        st.info("Todavía no hay productores.")
        return

    prod_ficha = st.session_state.get("prod_ficha")
    for prod in list(datos["productores"]):
        _tarjeta_productor(datos, prod, admin, prod_ficha)


@st.fragment
def _tarjeta_productor(datos: dict, prod: dict, admin: bool, prod_ficha):
    """Tarjeta de productor como FRAGMENTO: renombrar, cambiar foto o eliminar
    re-renderiza SOLO esta tarjeta (recarga parcial). Abrir/cerrar la ficha
    (cambio de vista) sigue reruneando la app."""
    # Si el productor se eliminó en este pase, el fragmento no re-renderiza
    if not any(p.get("id") == prod.get("id") for p in datos["productores"]):
        return
    catas_prod = [c for c in datos["catas"]
                  if str(c.get("productor", "")).strip() == prod["nombre"]]
    medias = [core.nota_media(c) for c in catas_prod]
    media = round(sum(medias) / len(medias), 1) if medias else 0.0

    with st.container(border=True):
        c_foto, c_info, c_acc = st.columns([1, 3, 2])
        with c_foto:
            mostrar_foto(prod.get("foto"), width=80, b64=prod.get("foto_b64", ""))
        with c_info:
            st.markdown(f"**{prod['nombre']}**")
            if prod.get("pais"):
                st.markdown(chip(prod["pais"],
                                 core.COLOR_PAIS.get(prod["pais"], "#444444")),
                            unsafe_allow_html=True)
            st.caption(f"{len(catas_prod)} cata{'s' if len(catas_prod) != 1 else ''}"
                       f" · nota media {media:.1f}")
        with c_acc:
            if admin:
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("Abrir", key=f"prod_abrir_{prod['id']}",
                                 width="stretch"):
                        st.session_state["prod_ficha"] = prod["nombre"]
                        st.rerun()  # cambio de vista: ficha completa
                with b2:
                    if st.button("✏️", key=f"prod_ren_{prod['id']}",
                                 width="stretch"):
                        st.session_state[f"prod_edit_{prod['id']}"] = True
                with b3:
                    if st.button("🗑", key=f"prod_del_{prod['id']}",
                                 width="stretch"):
                        if len(catas_prod) > 0:
                            st.error("Tiene catas asignadas: bórralas o reasígnalas "
                                     "antes de eliminar el productor.")
                        else:
                            datos["productores"] = [p for p in datos["productores"]
                                                    if p["id"] != prod["id"]]
                            guardar(datos)
                            try:
                                st.rerun(scope="fragment")
                            except Exception:
                                pass
            else:
                if st.button("Abrir", key=f"prod_abrir_{prod['id']}",
                             width="stretch"):
                    st.session_state["prod_ficha"] = prod["nombre"]
                    st.rerun()  # cambio de vista: ficha completa

        # Renombrar, país y foto: solo admin
        if admin:
            if st.session_state.get(f"prod_edit_{prod['id']}"):
                c1, c2, c3 = st.columns([3, 1, 1])
                with c1:
                    nuevo_nombre = st.text_input("Nuevo nombre", prod["nombre"],
                                                 key=f"prod_nombre_{prod['id']}")
                with c2:
                    nuevo_pais_edit = st.selectbox(
                        "País", core.PAISES_VALIDOS,
                        index=core.PAISES_VALIDOS.index(prod["pais"])
                        if prod.get("pais") in core.PAISES_VALIDOS else 0,
                        key=f"prod_pais_{prod['id']}")
                with c3:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Guardar", key=f"prod_save_{prod['id']}",
                                 width="stretch"):
                        nuevo = nuevo_nombre.strip()
                        if nuevo and nuevo != prod["nombre"]:
                            for c in datos["catas"]:
                                if str(c.get("productor", "")).strip() == prod["nombre"]:
                                    c["productor"] = nuevo
                            prod["nombre"] = nuevo
                        prod["pais"] = nuevo_pais_edit
                        guardar(datos)
                        del st.session_state[f"prod_edit_{prod['id']}"]
                        try:
                            st.rerun(scope="fragment")
                        except Exception:
                            pass

            # Foto del productor (accesible directamente desde la lista)
            with st.expander(f"📷 Foto de {prod['nombre']}", expanded=False):
                upload = st.file_uploader(
                    "Sube o cambia la foto del productor",
                    type=["png", "jpg", "jpeg", "webp", "bmp", "gif"],
                    key=f"prod_foto_{prod['id']}")
                if upload is not None:
                    if st.button("Guardar foto", key=f"prod_foto_save_{prod['id']}"):
                        rid = core.generar_id({p.get("foto", "") for p in datos["productores"]},
                                              prefijo="pr_")
                        prod["foto"], prod["foto_b64"] = guardar_foto_upload(upload, rid)
                        guardar(datos)
                        try:
                            st.rerun(scope="fragment")
                        except Exception:
                            pass

        # Ficha del productor (sus catas + foto)
        if prod_ficha == prod["nombre"]:
            st.divider()
            st.markdown(f"**Catas de {prod['nombre']}**")
            if not catas_prod:
                st.caption("Sin catas todavía.")
            for c in sorted(catas_prod, key=lambda c: -core.nota_media(c)):
                cc1, cc2, cc3 = st.columns([3, 1, 1])
                with cc1:
                    st.markdown(f"{c.get('nombre', '—')} · {c.get('tipo', '')} "
                                f"· {len(core.votos_validos(c))} voto"
                                f"{'s' if len(core.votos_validos(c)) != 1 else ''}")
                with cc2:
                    st.markdown(f"**{core.nota_media(c):.1f}**")
                with cc3:
                    if st.button("Ver", key=f"prod_cata_{c['id']}"):
                        st.session_state["ficha_id"] = c["id"]
                        st.session_state["pagina"] = "📦 Catálogo"
                        st.rerun()  # cambio de vista
            if st.button("Cerrar ficha", key=f"prod_close_{prod['id']}"):
                del st.session_state["prod_ficha"]
                st.rerun()  # cambio de vista


# =============================================================================
# SECCIÓN 4 — RANKINGS
# =============================================================================

def fila_ranking(cata: dict, pos: int = None, datos: dict = None,
                 nota_fn=None, etiqueta_nota: str = "", prefijo_key: str = "rkg"):
    """Fila de la lista de ranking: [foto | datos | nota] en una línea,
    con st.columns([1, 4, 1]) y alineación vertical centrada.
    - col1: foto del producto (o placeholder) a la izquierda.
    - col2: nombre (con medalla), productor y chips.
    - col3: nota grande a la derecha (+ etiqueta opcional, p. ej. '⭐ prof.').
    - Botón 'Ver ficha': enrutamiento cruzado a la ficha del Catálogo.
    nota_fn: función alternativa para la nota mostrada (p. ej. profesional).
    prefijo_key: prefijo único de las keys por tab (evita colisiones)."""
    media = (nota_fn(cata) if nota_fn else core.nota_media(cata))
    n_votos = len(core.votos_validos(cata))
    nombre = _html.escape(str(cata.get("nombre", "—")))
    productor = _html.escape(str(cata.get("productor", "") or "—"))
    if pos:
        medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
        pos_txt = f"{medallas.get(pos, '')} {pos}º".strip()  # sin espacio inicial
        nombre = f"{pos_txt} · {nombre}"
    color_nota = core.color_nota(media / 10)

    # Chips compactos: tipo / país / año
    chips_row = [chip(_html.escape(str(cata.get("tipo", "—"))),
                      core.COLOR_TIPO.get(cata.get("tipo", ""), "#444444"))]
    if cata.get("pais"):
        chips_row.append(chip(_html.escape(str(cata["pais"])),
                              core.COLOR_PAIS.get(cata["pais"], "#444444")))
    if cata.get("anio"):
        chips_row.append(chip(f"📅 {cata['anio']}", core.COLOR_ANIO))

    with st.container(border=True):
        c_foto, c_info, c_nota, c_ver = st.columns([1, 4, 1, 1],
                                                   vertical_alignment="center")
        with c_foto:
            foto_html = foto_base64(core.resolver_ruta_foto(cata.get("foto")), px=64,
                                    radius=8, b64=cata.get("foto_b64", ""))
            if foto_html:
                st.markdown(foto_html, unsafe_allow_html=True)
            else:
                st.markdown(placeholder_imagen(64, "🌿", radius=8),
                            unsafe_allow_html=True)
        with c_info:
            # Nombre más destacado (con medalla si hay posición). Negrita HTML
            # en vez de markdown `**` — más robusto con caracteres tipo º/·/emoji.
            st.markdown(
                f'<span style="font-weight:700;font-size:15px;color:#F2F5F9;'
                f'line-height:1.25">{_html.escape(nombre)}</span>',
                unsafe_allow_html=True)
            st.caption(f"🏭 {productor} · {n_votos} voto{'s' if n_votos != 1 else ''}")
            st.markdown(" ".join(chips_row), unsafe_allow_html=True)
        with c_nota:
            # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
            st.markdown(
                f'<div style="font-size:24px;font-weight:800;color:{color_nota};'
                f'line-height:1.1">{media:.1f}</div>',
                unsafe_allow_html=True)
            if etiqueta_nota:
                st.caption(etiqueta_nota)
        with c_ver:
            # Enrutamiento cruzado: Rankings -> Detalle del Catálogo.
            # Botón compacto integrado en la fila; en móvil apila a ancho
            # completo (CSS rk_lista) y no cuelga desbalanceado.
            if st.button("👁 Ver", key=f"abrir_{prefijo_key}_{cata['id']}",
                         width="stretch", help="Ver ficha"):
                st.session_state["producto_seleccionado"] = cata["id"]
                st.session_state["ficha_id"] = cata["id"]
                st.session_state["pagina"] = "📦 Catálogo"
                st.rerun()


def seccion_rankings(datos: dict):
    st.markdown("## 🏆 Rankings")

    # Filtros agrupados en un expander: en móvil las pills fluyen hacia
    # abajo de forma natural (sin columnas forzadas que rompan el ancho).
    with st.expander("🔍 Filtros de Búsqueda", expanded=False):
        texto = st.text_input("Buscar por nombre o productor", key="rk_buscar")
        filtro_tipo = st.pills("Tipo", ["Todos"] + core.TIPOS_VALIDOS,
                               key="rk_tipo", selection_mode="single",
                               default="Todos")
        filtro_pais = st.pills("País", ["Todos"] + core.PAISES_VALIDOS,
                               key="rk_pais", selection_mode="single",
                               default="Todos")
        filtro_anio = st.selectbox("Año", ["Todos"] + core.anios_produccion(),
                                   key="rk_anio")

    def filtrar():
        lista = []
        for c in datos["catas"]:
            if filtro_tipo != "Todos" and c.get("tipo") != filtro_tipo:
                continue
            if filtro_pais != "Todos" and c.get("pais") != filtro_pais:
                continue
            if filtro_anio != "Todos" and str(c.get("anio", "")) != filtro_anio:
                continue
            if texto and texto.lower() not in (c.get("nombre", "") + " " +
                                               c.get("productor", "")).lower():
                continue
            lista.append(c)
        return lista

    tab_general, tab_personal, tab_confianza = st.tabs(
        ["🌍 Top General", "👤 Top Personal", "🧑🔬 Top Confianza"])

    # ---------------- Top General ----------------
    with tab_general:
        lista = filtrar()
        lista.sort(key=lambda c: (-core.nota_media(c),
                                  -core._flotante(core.nota_media_bloques(c).get("sabor")),
                                  str(c.get("nombre", "")).lower()))
        if not lista:
            st.info("Sin resultados.")
        else:
            # Podium épico del Top 3 (fotos + medallas; colapsa a 1 columna en móvil)
            st.markdown("### 🥇 Podium")
            podium_epico(lista[:3])
            st.divider()
            # Lista: filas foto | datos | nota (alineación centrada)
            with st.container(key="rk_lista_general"):
                for pos, cata in enumerate(lista, start=1):
                    fila_ranking(cata, pos=pos, datos=datos, prefijo_key="rkg")

    # ---------------- Top Personal ----------------
    with tab_personal:
        nombres = [p["nombre"] for p in datos["perfiles"]]
        if not nombres:
            st.info("Crea perfiles primero.")
            return
        activo = st.session_state.get("usuario", nombres[0])
        elegido = st.selectbox("Perfil", nombres, index=nombres.index(activo)
                               if activo in nombres else 0, key="rk_perfil")
        perfil = perfil_por_nombre(datos, elegido)

        items = []
        for c in filtrar():
            voto = core.voto_de_perfil(c, perfil["id"])
            if voto is None:
                continue
            items.append((c, core._flotante(voto.get("nota_final")),
                          core._flotante(voto.get("notas_bloques", {}).get("sabor")),
                          core.nota_media(c)))
        items.sort(key=lambda t: (-t[1], -t[2], str(t[0].get("nombre", "")).lower()))

        if not items:
            st.info(f"**{perfil['nombre']}** todavía no ha votado ninguna cata "
                    "con estos filtros.")
            return

        with st.container(key="rk_lista_personal"):
            for pos, (cata, nota, sabor, media) in enumerate(items, start=1):
                with st.container(border=True):
                    c_foto, c_info, c_nota, c_ver = st.columns([1, 4, 1, 1],
                                                               vertical_alignment="center")
                    with c_foto:
                        foto_html = foto_base64(core.resolver_ruta_foto(cata.get("foto")), px=64,
                                                radius=8, b64=cata.get("foto_b64", ""))
                        if foto_html:
                            st.markdown(foto_html, unsafe_allow_html=True)
                        else:
                            st.markdown(placeholder_imagen(64, "🌿", radius=8),
                                        unsafe_allow_html=True)
                    with c_info:
                        medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
                        st.markdown(f"{medallas.get(pos, '')} **{pos}º** — "
                                    f"{cata.get('nombre', '—')}")
                        st.caption(f"{cata.get('productor', '—')} · media {media:.1f} · "
                                   f"{len(core.votos_validos(cata))} voto"
                                   f"{'s' if len(core.votos_validos(cata)) != 1 else ''}")
                    with c_nota:
                        color_nota = core.color_nota(nota / 10)
                        st.markdown(
                            f'<div style="font-size:24px;font-weight:800;'
                            f'color:{color_nota};line-height:1.1">Tu nota: '
                            f'{nota:.1f}</div>',
                            unsafe_allow_html=True)
                    with c_ver:
                        # Enrutamiento cruzado: Top Personal -> Catálogo
                        if st.button("👁 Ver", key=f"abrir_rkp_{cata['id']}",
                                     width="stretch", help="Ver ficha"):
                            st.session_state["producto_seleccionado"] = cata["id"]
                            st.session_state["ficha_id"] = cata["id"]
                            st.session_state["pagina"] = "📦 Catálogo"
                            st.rerun()

    # ---------------- Top Confianza (valoración profesional) ----------------
    with tab_confianza:
        items = []
        for c in filtrar():
            p = core.nota_media_profesional(c, datos)
            if p is not None:
                items.append((c, p))
        items.sort(key=lambda t: (-t[1], -core.nota_media(t[0]),
                                  str(t[0].get("nombre", "")).lower()))
        if not items:
            st.info("Todavía no hay valoraciones profesionales. Marca a alguien "
                    "como **🤝 gente de confianza** en 👥 Perfiles para que sus "
                    "votos cuenten en este Top.")
        else:
            st.caption("Solo cuentan los votos de la **gente de confianza** y "
                       "los **admins** (valoración profesional).")
            st.markdown("### 🥇 Podium Profesional")
            podium_epico([c for c, _ in items[:3]],
                         nota_fn=lambda c: core.nota_media_profesional(c, datos))
            st.divider()
            with st.container(key="rk_lista_confianza"):
                for pos, (cata, p) in enumerate(items, start=1):
                    fila_ranking(cata, pos=pos, datos=datos,
                                 nota_fn=lambda c: core.nota_media_profesional(c, datos),
                                 etiqueta_nota="⭐ profesional",
                                 prefijo_key="rkc")


# =============================================================================
# SECCIÓN 6 — EVOLUCIÓN (gráficas: productor en el tiempo / época)
# =============================================================================

def _anio_int(cata) -> int:
    """Año de producción como entero (None si no es válido)."""
    try:
        return int(str(cata.get("anio", "")).strip())
    except (TypeError, ValueError):
        return None


# Paleta de color para los charts (verne neón + violeta, coherente con el tema).
# Streamlit NO usa primaryColor en los charts (usa su paleta default azul), así
# que se pasa color explícito. Coherente con el Design System v3.
_PALETA_CHART = ["#34D97B", "#7E5AE0", "#22C55E", "#A78BFA", "#4ADE80"]


def _chart_actividad(ser, height_px=170):
    """Chart de barras con degradado sutil verde→violeta (años más recientes
    más violeta, referencia cósmica del personaje). Devuelve una Figure o None
    si matplotlib no está disponible."""
    if not _HAY_MPL:
        return None
    try:
        s = pd.Series(ser).sort_index()
        if s.empty:
            return None
        anos = list(s.index)
        valores = list(s.values)
        n = len(anos)
        cmap = _LSC.from_list("cosmic", ["#34D97B", "#4ADE80", "#7E5AE0"])
        colores = [cmap(i / max(1, n - 1)) for i in range(n)] if n > 1 else [cmap(0.5)]
        fig, ax = plt.subplots(figsize=(9, max(1.4, height_px / 100)), dpi=110)
        fig.patch.set_facecolor("#0A0D16")
        ax.set_facecolor("#0A0D16")
        ax.bar([str(a) for a in anos], valores, color=colores, width=0.62)
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color("#2A313C")
        ax.tick_params(axis="x", colors="#A5AEB8", labelsize=9)
        ax.tick_params(axis="y", colors="#A5AEB8", labelsize=8, length=0)
        ax.grid(axis="y", color="#1B2129", linewidth=0.8, alpha=0.6)
        ax.set_axisbelow(True)
        fig.tight_layout()
        return fig
    except Exception:
        return None



def _df_notas(catas_filtradas) -> pd.DataFrame:
    """DataFrame (anio, temporada, periodo, clave, productor, nota) de catas
    con voto y año válido. 'periodo' = '2025 · S2' si hay temporada, si no '2025';
    'clave' ordena cronológicamente (año*10 + nº de temporada)."""
    nums = {"S1": 1, "S2": 2, "S3": 3}
    filas = []
    for c in catas_filtradas:
        anio = _anio_int(c)
        if anio is None or not core.votos_validos(c):
            continue
        temp = str(c.get("temporada", "") or "").strip()
        num = nums.get(temp, 0)
        periodo = f"{anio} · {temp}" if num else str(anio)
        filas.append({"anio": anio, "temporada": temp, "periodo": periodo,
                      "clave": anio * 10 + num,
                      "productor": str(c.get("productor", "")).strip() or "—",
                      "nota": round(core.nota_media(c), 1)})
    return pd.DataFrame(filas)


def seccion_evolucion(datos: dict):
    st.markdown("## 📈 Evolución")
    st.caption("Nota media (sobre 100) de las catas con voto, según el año de "
               "producción y temporada (S1/S2/S3 = tiradas del año).")

    # ---- Filtros simples: procedencia, tipo y temporada ----
    c1, c2, c3 = st.columns(3)
    with c1:
        f_pais = st.pills("Procedencia", ["Todos"] + core.PAISES_VALIDOS,
                          key="ev_pais", selection_mode="single", default="Todos")
    with c2:
        f_tipo = st.pills("Tipo", ["Todos"] + core.TIPOS_VALIDOS,
                          key="ev_tipo", selection_mode="single", default="Todos")
    with c3:
        f_temp = st.pills("Temporada", ["Todas"] + core.TEMPORADAS_VALIDAS,
                          key="ev_temp", selection_mode="single", default="Todas")

    catas_base = []
    for c in datos["catas"]:
        if f_pais != "Todos" and c.get("pais") != f_pais:
            continue
        if f_tipo != "Todos" and c.get("tipo") != f_tipo:
            continue
        if f_temp != "Todas" and c.get("temporada") != f_temp:
            continue
        catas_base.append(c)

    if not catas_base:
        st.info("Sin catas con esos filtros.")
        return

    df = _df_notas(catas_base)
    if df.empty:
        st.info("Las catas con esos filtros no tienen **año de producción** ni "
                "votos todavía. Asigna el año desde la ficha (📦 Catálogo).")
        return

    pais_prod = {p["nombre"]: p.get("pais", "")
                 for p in datos["productores"]}
    prod_foto = {p["nombre"]: p.get("foto", "")
                 for p in datos["productores"]}
    prod_b64 = {p["nombre"]: p.get("foto_b64", "")
                for p in datos["productores"]}

    def foto_productor_html(nombre_prod: str, px: int = 40) -> str:
        """Foto circular del productor (base64) o placeholder 🏭."""
        f = foto_base64(core.resolver_ruta_foto(prod_foto.get(nombre_prod, "")),
                        px=px, radius=px // 2,
                        b64=prod_b64.get(nombre_prod, ""))
        return f or placeholder_imagen(px, "🏭", radius=px // 2)

    tab_prod, tab_epoca = st.tabs(["🏭 Evolución de un productor",
                                   "⏳ ¿Quién era el mejor?"])

    # ---------- Tab 1: evolución de uno o varios productores ----------
    with tab_prod:
        productores = sorted(df["productor"].unique())
        elegidos = st.multiselect("Productores a comparar", productores,
                                  default=[productores[0]] if productores else [],
                                  key="ev_prods")
        agrupar = st.segmented_control("Agrupar por", ["Año", "Año + Temporada"],
                                       default="Año", key="ev_agrupar")
        if not elegidos:
            st.info("Elige al menos un productor.")
        else:
            # ---- Actividad: catas registradas por año (TODAS, con o sin voto).
            #      Da contexto y evita que la sección parezca vacía si aún hay
            #      pocas catas con nota. ----
            _vol = {}
            for c in catas_base:
                a = _anio_int(c)
                if a is not None:
                    _vol[a] = _vol.get(a, 0) + 1
            if _vol:
                _ser = pd.Series(dict(sorted(_vol.items())))
                st.caption(f"**Actividad**: {int(_ser.sum())} catas registradas por año")
                fig = _chart_actividad(_ser, height_px=170)
                if fig is not None:
                    st.pyplot(fig, clear_figure=True)
                st.divider()
            sub_df = df[df["productor"].isin(elegidos)]
            if agrupar == "Año":
                piv = (sub_df.pivot_table(index="anio", columns="productor",
                                          values="nota", aggfunc="mean"))
                if not piv.empty:
                    piv = piv.reindex(range(int(piv.index.min()),
                                            int(piv.index.max()) + 1))
            else:
                piv = (sub_df.pivot_table(index="periodo", columns="productor",
                                          values="nota", aggfunc="mean"))
                if not piv.empty:
                    orden = (sub_df.drop_duplicates("periodo")
                             .set_index("periodo")["clave"].sort_values())
                    piv = piv.reindex(orden.index)
            if piv.empty:
                st.info("Sin datos para esos productores con los filtros actuales.")
            else:
                st.line_chart(piv, height=260, color=_PALETA_CHART[:max(1, len(piv.columns))])
                # Catas de los productores elegidos (con mini foto del productor)
                sub = [c for c in catas_base
                       if str(c.get("productor", "")).strip() in elegidos]
                sub.sort(key=lambda c: (_anio_int(c) or 0, -core.nota_media(c)))
                with st.container(key="ev_catas_lista", border=True):
                    st.markdown(f"**{len(sub)} cata(s)** en el gráfico")
                    for c in sub[:12]:
                        anio = str(c.get("anio", "") or "—")
                        temp = str(c.get("temporada", "") or "")
                        nombre = _html.escape(str(c.get("nombre", "—")))
                        prod = _html.escape(str(c.get("productor", "") or "—"))
                        color_nota = core.color_nota(core.nota_media(c) / 10)
                        temp_txt = (f" · <span style='color:{core.COLOR_TEMPORADA.get(temp, '#888')}"
                                    f";font-weight:700'>◈ {temp}</span>" if temp else "")
                        # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
                        st.markdown(
                            '<div style="display:flex;align-items:center;gap:10px">'
                            f'<div style="flex:0 0 auto">{foto_productor_html(c.get("productor", ""), 32)}</div>'
                            '<div style="flex:1;min-width:0">'
                            f'<div style="font-weight:600;font-size:14px;color:#F2F5F9">{nombre}</div>'
                            f'<div style="font-size:12px;color:#8B93A1">{anio}{temp_txt} · {prod}</div>'
                            '</div>'
                            f'<div style="text-align:right;font-size:19px;font-weight:800;color:{color_nota}">'
                            f'{core.nota_media(c):.1f}</div>'
                            '</div>',
                            unsafe_allow_html=True)

    # ---------- Tab 2: quién era el mejor en una época ----------
    with tab_epoca:
        periodos = (df.drop_duplicates("periodo").set_index("periodo")["clave"]
                    .sort_values(ascending=False).index.tolist())
        epoca = st.selectbox("Época / tirada", ["Todas"] + periodos, key="ev_epoca")
        df_ep = df if epoca == "Todas" else df[df["periodo"] == epoca]
        if df_ep.empty:
            st.info("Sin datos en esa época.")
        else:
            rank = (df_ep.groupby("productor")["nota"]
                    .agg(["mean", "count"]).reset_index()
                    .sort_values(["mean", "count"], ascending=False)
                    .head(10))
            rank.columns = ["productor", "nota_media", "n_catas"]
            st.bar_chart(rank.set_index("productor")["nota_media"],
                         horizontal=True, height=280, color="#34D97B")
            # Ranking en tarjetas con la FOTO del productor (circular)
            with st.container(key="ev_rank_epoca", border=True):
                st.markdown(f"**Top por nota media** en la época seleccionada:")
                medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
                for i, r in enumerate(rank.itertuples(), start=1):
                    pais = pais_prod.get(r.productor, "")
                    chip_pais = (chip(_html.escape(pais),
                                      core.COLOR_PAIS.get(pais, "#444444"))
                                 if pais else "")
                    color_nota = core.color_nota(r.nota_media / 10)
                    pos = f"{medallas.get(i, '')} {i}º"
                    # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
                    st.markdown(
                        '<div style="display:flex;align-items:center;gap:12px">'
                        f'<div style="flex:0 0 auto">{foto_productor_html(r.productor, 44)}</div>'
                        '<div style="flex:1;min-width:0">'
                        f'<div style="font-weight:700;font-size:14px;color:#F2F5F9">'
                        f'{pos} · {_html.escape(str(r.productor))}</div>'
                        f'<div style="font-size:12px;color:#8B93A1;margin-top:2px">'
                        f'{chip_pais if chip_pais else ""} {int(r.n_catas)} cata'
                        f'{"s" if r.n_catas != 1 else ""}</div>'
                        '</div>'
                        f'<div style="text-align:right;font-size:21px;font-weight:800;color:{color_nota}">'
                        f'{r.nota_media:.1f}</div>'
                        '</div>',
                        unsafe_allow_html=True)


# =============================================================================
# SECCIÓN 7 — PERFILES
# =============================================================================

@st.fragment
def _tarjeta_perfil(datos: dict, perfil: dict, admin: bool):
    """Tarjeta de un perfil como FRAGMENTO: eliminar, cambiar contraseña,
    toggles de rango y renombrar re-renderizan SOLO esta tarjeta."""
    # Si el perfil se eliminó en este pase, el fragmento no re-renderiza
    if not any(p.get("id") == perfil.get("id") for p in datos["perfiles"]):
        return
    n_votos = 0
    notas = []
    for c in datos["catas"]:
        v = core.voto_de_perfil(c, perfil["id"])
        if v is not None and v.get("puntuaciones_detalle"):
            n_votos += 1
            notas.append(core._flotante(v.get("nota_final")))
    media = round(sum(notas) / len(notas), 1) if notas else 0.0
    es_owner = perfil.get("id") == "p_default"
    es_adm = bool(perfil.get("es_admin")) or es_owner
    es_yo = st.session_state.get("usuario") == perfil["nombre"]

    with st.container(border=True):
        c1, c2 = st.columns([3, 2])
        with c1:
            badge = "👑 **Admin** — " if es_adm else ""
            yo = " **← tú**" if es_yo else ""
            st.markdown(f"{badge}**{perfil['nombre']}**{yo}")
            st.caption(f"{n_votos} voto{'s' if n_votos != 1 else ''} emitidos · "
                       f"nota media {media:.1f}")
        with c2:
            if admin and not es_owner:  # el dueño no se puede eliminar
                if st.button("🗑 Eliminar", key=f"perf_del_{perfil['id']}"):
                    if len(datos["perfiles"]) <= 1:
                        st.error("Necesitas al menos un perfil.")
                    elif es_yo:
                        st.error("No puedes eliminar tu propia cuenta estando "
                                 "logueado (cierra sesión primero).")
                    else:
                        datos["perfiles"] = [p for p in datos["perfiles"]
                                             if p["id"] != perfil["id"]]
                        for c in datos["catas"]:
                            core.quitar_voto(c, perfil["id"])
                        guardar(datos)
                        try:
                            st.rerun(scope="fragment")
                        except Exception:
                            pass

        # Cambiar contraseña: cualquiera la suya; el admin la de cualquiera
        if es_yo or admin:
            with st.expander("🔑 Cambiar contraseña"):
                n1 = st.text_input("Nueva contraseña (mín. 4)", type="password",
                                   key=f"pw1_{perfil['id']}")
                n2 = st.text_input("Repite la contraseña", type="password",
                                   key=f"pw2_{perfil['id']}")
                if st.button("Guardar contraseña", key=f"pw_save_{perfil['id']}"):
                    if len(n1) < 4:
                        st.error("La contraseña debe tener al menos 4 caracteres.")
                    elif n1 != n2:
                        st.error("Las contraseñas no coinciden.")
                    else:
                        perfil["password_hash"] = hash_password(n1)
                        guardar(datos)
                        st.success("✅ Contraseña actualizada.")
                        try:
                            st.rerun(scope="fragment")
                        except Exception:
                            pass

        # Gestión (solo admin)
        if admin:
            if not es_owner:
                nuevo_admin = st.toggle("👑 Puede eliminar productos (admin)",
                                        value=bool(perfil.get("es_admin")),
                                        key=f"perf_admin_{perfil['id']}")
                if nuevo_admin != bool(perfil.get("es_admin")):
                    perfil["es_admin"] = nuevo_admin
                    guardar(datos)
                    try:
                        st.rerun(scope="fragment")
                    except Exception:
                        pass
            if not es_owner:
                nueva_confianza = st.toggle(
                    "🤝 Gente de confianza (comenta y su voto cuenta como "
                    "valoración profesional)",
                    value=bool(perfil.get("es_confianza")),
                    key=f"perf_conf_{perfil['id']}")
                if nueva_confianza != bool(perfil.get("es_confianza")):
                    perfil["es_confianza"] = nueva_confianza
                    guardar(datos)
                    try:
                        st.rerun(scope="fragment")
                    except Exception:
                        pass
            if st.button("✏️ Renombrar", key=f"perf_ren_{perfil['id']}"):
                st.session_state[f"perf_edit_{perfil['id']}"] = True
            if st.session_state.get(f"perf_edit_{perfil['id']}"):
                e1, e2 = st.columns([3, 1])
                with e1:
                    nuevo_nombre = st.text_input("Nuevo nombre", perfil["nombre"],
                                                 key=f"perf_nombre_{perfil['id']}")
                with e2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Guardar", key=f"perf_save_{perfil['id']}",
                                 width="stretch"):
                        nuevo = nuevo_nombre.strip()
                        if nuevo:
                            perfil["nombre"] = nuevo
                            if es_yo:
                                st.session_state["usuario"] = nuevo
                            guardar(datos)
                        del st.session_state[f"perf_edit_{perfil['id']}"]
                        if es_yo:  # el nombre cambia en el sidebar: rerun app
                            st.rerun()
                        else:
                            try:
                                st.rerun(scope="fragment")
                            except Exception:
                                pass


# =============================================================================
# ASOCIACIONES / COFFEESHOPS — vistas (directorio geolocalizado)
# =============================================================================

def tarjeta_coffeeshop(datos: dict, cs: dict, posicion: int = 0) -> str:
    """Tarjeta del grid: foto, nombre, ubicación, nota media, pills.
    Estilo coherente con el Design System (verde neón + oscuro)."""
    nota = core.nota_media_coffeeshop(cs)
    n_votos = len(core.votos_coffeeshop_validos(cs))
    ciudad = core.ciudad_por_id(datos, cs.get("ciudad_id", "")).get("nombre", "")
    pais = core.pais_por_id(datos, cs.get("pais_id", "")).get("nombre", "")
    ubicacion = " · ".join(x for x in [ciudad, pais] if x) or "—"
    prod_nombres = [p.get("nombre", "")
                    for p in core.productores_de_coffeeshop(datos, cs)]
    # Pills de productores en verde neón (coherente con el resto de chips de la app)
    pills = " ".join(
        f"<span style='background:rgba(74,222,128,0.14);color:#8BE9B0;"
        f"border:1px solid rgba(74,222,128,0.28);padding:2px 9px;"
        f"border-radius:10px;font-size:11px;font-weight:600;margin:2px 3px 2px 0'>"
        f"{_html.escape(str(n))}</span>" for n in prod_nombres[:5])
    if not pills:
        pills = ("<span style='color:#7A8391;font-size:11px'>"
                 "sin productores vinculados</span>")
    color_nota = core.color_nota(nota / 10) if nota else "#7A8391"
    nota_html = (f"<span style='font-size:24px;font-weight:800;"
                 f"color:{color_nota}'>{nota:.1f}</span>") if nota else (
        f"<span style='font-size:14px;color:#7A8391'>sin votos</span>")
    foto_html = (foto_base64("", px=84, radius=12,
                             b64=cs.get("foto_b64", ""))
                 or placeholder_imagen(84, "🏪"))
    medalla = {1: "🥇", 2: "🥈", 3: "🥉"}.get(posicion, "") if posicion else ""
    return (
        f"<div class='cs-card'>"
        f"<div style='display:flex;justify-content:space-between;align-items:start;"
        f"gap:8px'>"
        f"<div style='font-size:16px;font-weight:800;color:#F2F5F9;line-height:1.25'>"
        f"{medalla} {_html.escape(str(cs.get('nombre', '')))}</div>"
        f"<div style='text-align:right'>{nota_html}<div style='font-size:10px;"
        f"color:#7A8391'>{n_votos} voto{'s' if n_votos != 1 else ''}</div></div>"
        f"</div>"
        f"<div style='display:flex;justify-content:center;padding:2px 0'>"
        f"{foto_html}</div>"
        f"<div style='font-size:12px;color:#A5AEB8'>📍 {_html.escape(ubicacion)}</div>"
        f"<div style='display:flex;flex-wrap:wrap;margin-top:auto'>{pills}</div>"
        f"</div>")


def panel_admin_coffeeshops(datos: dict):
    """Herramientas de administración (solo admin 👑): países, ciudades,
    coffeeshops y vinculación de productores al menú."""
    if not es_admin(datos):
        return
    with st.expander("🛠️ Administración de Asociaciones", expanded=False):
        try:
            _panel_admin_cs_inner(datos)
        except Exception as _e:
            st.error(f"⚠️ Error en el panel admin: {type(_e).__name__}: {_e}")
            st.exception(_e)


def _panel_admin_cs_inner(datos: dict):
    # --- Añadir país ---
        c1, c2 = st.columns(2)
        with c1:
            nuevo_pais = st.text_input("➕ Nuevo país", key="cs_pais_nuevo")
            if st.button("Añadir país", key="cs_btn_pais", width="stretch"):
                if core.upsert_pais(datos, nuevo_pais):
                    guardar(datos)
                    st.success(f"✅ País '{nuevo_pais.strip()}' añadido.")
                    st.rerun()
                else:
                    st.warning("Escribe el nombre del país.")
        # --- Añadir ciudad ---
        with c2:
            paises = {p["nombre"]: p["id"] for p in datos.get("paises", [])}
            pais_sel = st.selectbox("País de la ciudad", list(paises) or ["—"],
                                    key="cs_ciud_pais")
            nueva_ciudad = st.text_input("➕ Nueva ciudad", key="cs_ciud_nueva")
            if st.button("Añadir ciudad", key="cs_btn_ciud", width="stretch"):
                pid = paises.get(pais_sel, "")
                if pid and core.upsert_ciudad(datos, nueva_ciudad, pid):
                    guardar(datos)
                    st.success(f"✅ Ciudad '{nueva_ciudad.strip()}' añadida.")
                    st.rerun()
                else:
                    st.warning("Elige país y escribe la ciudad.")

        st.divider()
        # --- Crear coffeeshop ---
        st.markdown("**🏪 Crear nueva Asociación / Coffeeshop**")
        a1, a2 = st.columns(2)
        paises = {p["nombre"]: p["id"] for p in datos.get("paises", [])}
        with a1:
            cs_nombre = st.text_input("Nombre del local", key="cs_nuevo_nombre")
            cs_pais = st.selectbox("País", list(paises) or ["—"], key="cs_nuevo_pais")
        with a2:
            ciudades = [c["nombre"] for c in datos.get("ciudades", [])
                        if c.get("pais_id") == paises.get(cs_pais, "")]
            cs_ciudad = st.selectbox("Ciudad", ["—"] + ciudades, key="cs_nuevo_ciudad")
            cs_direccion = st.text_input("Dirección (opcional)", key="cs_nuevo_dir")
        cs_biografia = st.text_area("Biografía / descripción del local",
                                    key="cs_nuevo_bio", height=90)
        cs_foto_up = st.file_uploader("📷 Foto del local (opcional)",
                                      type=["png", "jpg", "jpeg", "webp", "bmp", "gif"],
                                      key="cs_nuevo_foto")
        if st.button("💾 Crear coffeeshop", type="primary",
                     key="cs_btn_crear", width="stretch"):
            pid = paises.get(cs_pais, "")
            cid = next((c["id"] for c in datos.get("ciudades", [])
                        if c.get("nombre") == cs_ciudad
                        and c.get("pais_id") == pid), "")
            foto_b64 = ""
            if cs_foto_up is not None:
                _, foto_b64 = guardar_foto_upload(cs_foto_up, "cs_nuevo")
            if core.upsert_coffeeshop(datos, cs_nombre, pid, cid,
                                      cs_direccion, cs_biografia, foto_b64):
                guardar(datos)
                st.success(f"✅ '{cs_nombre.strip()}' creado.")
                st.rerun()
            else:
                st.warning("Escribe el nombre del local.")

        st.divider()
        # --- Vincular productores al menú ---
        st.markdown("**🔗 Menú: vincular productores a un local**")
        cs_nombres = {c["nombre"]: c for c in datos.get("coffeeshops", [])}
        if not cs_nombres:
            st.caption("Aún no hay coffeeshops creados.")
            return
        v1, v2 = st.columns([1, 2])
        with v1:
            sel_cs = st.selectbox("Local", list(cs_nombres), key="cs_vin_local")
        cs_obj = cs_nombres.get(sel_cs)
        if cs_obj is not None:
            prod_opciones = {p["nombre"]: p["id"] for p in datos.get("productores", [])}
            vinculados = set(cs_obj.get("productores", []))
            actuales = [n for n, i in prod_opciones.items() if i in vinculados]
            with v2:
                elegidos = st.multiselect("Productores en el menú", list(prod_opciones),
                                          default=actuales, key="cs_vin_prods")
            if st.button("💾 Guardar menú", key="cs_btn_vin", width="stretch"):
                elegidos_ids = {prod_opciones[n] for n in elegidos}
                for pid in vinculados - elegidos_ids:
                    core.desvincular_productor_cs(cs_obj, pid)
                for pid in elegidos_ids - vinculados:
                    core.vincular_productor_cs(cs_obj, pid)
                guardar(datos)
                st.success("✅ Menú actualizado.")
                st.rerun()

        st.divider()
        # --- Eliminar país / ciudad / asociación ---
        st.markdown("**🗑 Eliminar (con confirmación)**")
        d1, d2, d3 = st.columns(3)
        # País
        with d1:
            paises_nombres = {p["nombre"]: p["id"] for p in datos.get("paises", [])}
            sel_pais_del = st.selectbox("País", list(paises_nombres) or ["—"],
                                        key="cs_del_pais")
            if st.button("🗑 Eliminar país", key="cs_btn_del_pais",
                         width="stretch"):
                st.session_state["cs_confirm_pais"] = sel_pais_del
            if st.session_state.get("cs_confirm_pais"):
                _nom = st.session_state["cs_confirm_pais"]
                st.warning(f"Se eliminará **{_nom}** y sus ciudades/asociaciones.")
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Sí", key="cs_confirm_pais_si",
                                 width="stretch"):
                        pid = paises_nombres.get(
                            st.session_state.pop("cs_confirm_pais", ""), "")
                        n_cs = core.eliminar_pais(datos, pid)
                        guardar(datos)
                        st.success(f"🗑 País eliminado "
                                   f"({n_cs} asociaciones en cascada).")
                        st.rerun()
                with cc2:
                    if st.button("❌ No", key="cs_confirm_pais_no",
                                 width="stretch"):
                        st.session_state.pop("cs_confirm_pais", None)
                        st.rerun()
        # Ciudad
        with d2:
            ciudades_nombres = {f"{c['nombre']} ({core.pais_por_id(datos, c.get('pais_id','')).get('nombre','?')})": c["id"]
                                for c in datos.get("ciudades", [])}
            sel_ciud_del = st.selectbox("Ciudad", list(ciudades_nombres) or ["—"],
                                        key="cs_del_ciudad")
            if st.button("🗑 Eliminar ciudad", key="cs_btn_del_ciudad",
                         width="stretch"):
                st.session_state["cs_confirm_ciudad"] = sel_ciud_del
            if st.session_state.get("cs_confirm_ciudad"):
                _nom = st.session_state["cs_confirm_ciudad"]
                st.warning(f"Se eliminará la ciudad **{_nom}**.")
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Sí", key="cs_confirm_ciudad_si",
                                 width="stretch"):
                        cid = ciudades_nombres.get(
                            st.session_state.pop("cs_confirm_ciudad", ""), "")
                        core.eliminar_ciudad(datos, cid)
                        guardar(datos)
                        st.success("🗑 Ciudad eliminada.")
                        st.rerun()
                with cc2:
                    if st.button("❌ No", key="cs_confirm_ciudad_no",
                                 width="stretch"):
                        st.session_state.pop("cs_confirm_ciudad", None)
                        st.rerun()
        # Asociación
        with d3:
            cs_nombres_del = {c["nombre"]: c["id"]
                              for c in datos.get("coffeeshops", [])}
            sel_cs_del = st.selectbox("Asociación", list(cs_nombres_del) or ["—"],
                                      key="cs_del_cs")
            if st.button("🗑 Eliminar asociación", key="cs_btn_del_cs",
                         width="stretch"):
                st.session_state["cs_confirm_cs"] = sel_cs_del
            if st.session_state.get("cs_confirm_cs"):
                _nom = st.session_state["cs_confirm_cs"]
                st.warning(f"Se eliminará **{_nom}** (votos y menú incluidos).")
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ Sí", key="cs_confirm_cs_si",
                                 width="stretch"):
                        cid = cs_nombres_del.get(
                            st.session_state.pop("cs_confirm_cs", ""), "")
                        core.eliminar_coffeeshop(datos, cid)
                        guardar(datos)
                        st.success("🗑 Asociación eliminada.")
                        st.rerun()
                with cc2:
                    if st.button("❌ No", key="cs_confirm_cs_no",
                                 width="stretch"):
                        st.session_state.pop("cs_confirm_cs", None)
                        st.rerun()


@st.fragment
def _votacion_cs(datos: dict, cs: dict):
    """Valoración del local como FRAGMENTO: guardar/quitar valoración
    re-renderiza SOLO este bloque (sin recargar la ficha ni la app)."""
    st.markdown("### 🗳 Valoración del local")
    usuario = st.session_state.get("usuario", "")
    if not usuario:
        st.info("🔒 Inicia sesión para valorar este local.")
        if st.button("🔑 Iniciar sesión", key="cs_login"):
            st.session_state["pagina"] = "🔐 Acceso"
            st.rerun()
        return
    perfil = perfil_por_nombre(datos, usuario)
    if perfil is None:
        st.warning("Perfil no encontrado.")
        return
    ya_votado = core.voto_coffeeshop_de_perfil(cs, perfil["id"])
    v1, v2 = st.columns([1, 1])
    with v1:
        nota_slider = st.slider("Nota", 0.0, 10.0, 5.0, 0.5,
                                key=f"cs_sl_{cs['id']}")
    with v2:
        comentario = st.text_input(
            "Comentario (opcional)",
            value=(ya_votado.get("comentario", "") if ya_votado else ""),
            key=f"cs_com_{cs['id']}")
    if st.button("💾 Guardar mi valoración", type="primary",
                 key="cs_guardar_voto", width="stretch"):
        core.upsert_voto_coffeeshop(cs, perfil["id"], nota_slider, comentario)
        guardar(datos)
        st.toast("✅ Valoración guardada.")
        try:
            st.rerun(scope="fragment")
        except Exception:
            pass
    if ya_votado:
        st.caption(f"Tu voto actual: ⭐ {ya_votado['nota']:.1f}"
                   + (f" — {ya_votado['comentario']}"
                      if ya_votado.get("comentario") else ""))
        if st.button("🗑 Quitar mi voto", key="cs_quitar_voto"):
            core.quitar_voto_coffeeshop(cs, perfil["id"])
            guardar(datos)
            try:
                st.rerun(scope="fragment")
            except Exception:
                pass
    # Lista de valoraciones
    votos = core.votos_coffeeshop_validos(cs)
    if votos:
        st.caption("Valoraciones:")
        for v in sorted(votos, key=lambda x: -x["nota"]):
            nombre_v = next((p["nombre"] for p in datos["perfiles"]
                             if p.get("id") == v["perfil_id"]), v["perfil_id"])
            extra = f" — {v['comentario']}" if v.get("comentario") else ""
            st.caption(f"⭐ {v['nota']:.1f} · {nombre_v}{extra}")


def ficha_coffeeshop(datos: dict, cs: dict):
    """Ficha individual: cabecera, biografía, valoración y menú por productor."""
    st.button("← Volver al listado", key="cs_volver", width="stretch",
              on_click=lambda: (st.session_state.pop("cs_ficha", None), st.rerun()))
    ciudad = core.ciudad_por_id(datos, cs.get("ciudad_id", "")).get("nombre", "")
    pais = core.pais_por_id(datos, cs.get("pais_id", "")).get("nombre", "")
    ubicacion = " · ".join(x for x in [ciudad, pais] if x) or "—"
    nota = core.nota_media_coffeeshop(cs)
    n_votos = len(core.votos_coffeeshop_validos(cs))

    st.markdown(f"## 🏪 {_html.escape(str(cs.get('nombre', '')))}")
    st.caption(f"📍 {_html.escape(ubicacion)}"
               + (f" · {_html.escape(str(cs.get('direccion', '')))}"
                  if cs.get("direccion") else ""))
    hf1, hf2 = st.columns([1, 2])
    with hf1:
        mostrar_foto("", width=160, emoji="🏪",
                     b64=cs.get("foto_b64", ""))
        # Cambiar foto (solo admin)
        if es_admin(datos):
            up_foto = st.file_uploader("📷 Cambiar foto",
                                       type=["png", "jpg", "jpeg", "webp", "bmp", "gif"],
                                       key=f"cs_foto_up_{cs['id']}")
            if up_foto is not None:
                if st.button("💾 Guardar foto", key=f"cs_foto_save_{cs['id']}",
                             width="stretch"):
                    _, b64 = guardar_foto_upload(up_foto, cs["id"])
                    cs["foto_b64"] = b64
                    guardar(datos)
                    st.success("✅ Foto actualizada.")
                    st.rerun()
    with hf2:
        col_met, col_bio = st.columns(2)
        with col_met:
            if nota:
                color = core.color_nota(nota / 10)
                st.markdown(f"<div style='font-size:34px;font-weight:800;"
                            f"color:{color}'>{nota:.1f}</div>",
                            unsafe_allow_html=True)
                st.caption(f"⭐ Media de {n_votos} voto{'s' if n_votos != 1 else ''}")
            else:
                st.markdown("<div style='font-size:22px;color:#5A6472'>"
                            "Sin valoraciones todavía</div>", unsafe_allow_html=True)
        with col_bio:
            if cs.get("biografia"):
                st.markdown("**📖 Biografía**")
                st.write(cs["biografia"])

    # ---- Valoración (solo usuarios logueados) ----
    st.divider()
    _votacion_cs(datos, cs)

    # ---- Menú / materiales disponibles por productor ----
    st.divider()
    st.markdown("### 🧾 Menú / Materiales disponibles")
    productores_menu = core.productores_de_coffeeshop(datos, cs)
    if not productores_menu:
        st.caption("Este local aún no tiene productores vinculados a su menú.")
    for prod in productores_menu:
        foto_prod = (foto_base64(core.resolver_ruta_foto(prod.get("foto", "")),
                                 px=26, radius=13, b64=prod.get("foto_b64", ""))
                     or placeholder_imagen(26, "🏭", radius=13))
        st.markdown(f"<div style='display:flex;align-items:center;gap:8px;"
                    f"margin:6px 0 2px'>{foto_prod}"
                    f"<span style='font-weight:700;font-size:15px'>"
                    f"{_html.escape(str(prod.get('nombre', '')))}</span></div>",
                    unsafe_allow_html=True)
        catas_prod = core.catas_de_productor(datos, prod.get("nombre", ""))
        if not catas_prod:
            st.caption("  *(sin catas registradas en el catálogo)*")
        for c in catas_prod:
            n = core.nota_media(c)
            color = core.color_nota(n / 10) if n else "#5A6472"
            m1, m2, m3 = st.columns([3, 1, 1])
            with m1:
                st.markdown(f"🌿 {_html.escape(str(c.get('nombre', '')))} "
                            f"· {_html.escape(str(c.get('tipo', '')))}")
            with m2:
                if n:
                    st.markdown(f"<span style='color:{color};font-weight:700'>"
                                f"{n:.1f}</span>", unsafe_allow_html=True)
            with m3:
                if st.button("📂 Ver", key=f"cs_cata_{cs['id']}_{c['id']}"):
                    st.session_state["ficha_id"] = c["id"]
                    st.session_state["pagina"] = "📦 Catálogo"
                    st.session_state.pop("cs_ficha", None)
                    st.rerun()

    # ---- Eliminar asociación (solo admin 👑, con confirmación) ----
    if es_admin(datos):
        st.divider()
        _confirm_key = f"cs_confirm_ficha_{cs['id']}"
        if st.button("🗑 Eliminar esta asociación", key=f"cs_del_ficha_{cs['id']}",
                     width="stretch"):
            st.session_state[_confirm_key] = True
        if st.session_state.get(_confirm_key):
            st.warning(f"¿Seguro que quieres eliminar **{cs.get('nombre', '')}**? "
                       "Se borrarán sus valoraciones y su menú.")
            fc1, fc2 = st.columns(2)
            with fc1:
                if st.button("✅ Sí, eliminar", key=f"cs_yes_ficha_{cs['id']}",
                             width="stretch"):
                    core.eliminar_coffeeshop(datos, cs["id"])
                    guardar(datos)
                    st.session_state.pop(_confirm_key, None)
                    st.session_state.pop("cs_ficha", None)
                    st.success("🗑 Asociación eliminada.")
                    st.rerun()
            with fc2:
                if st.button("❌ Cancelar", key=f"cs_no_ficha_{cs['id']}",
                             width="stretch"):
                    st.session_state.pop(_confirm_key, None)
                    st.rerun()


def seccion_asociaciones(datos: dict):
    """Directorio geolocalizado: filtros en cascada + grid + ficha."""
    # Si hay una ficha abierta, mostrarla
    cs_ficha = st.session_state.get("cs_ficha")
    if cs_ficha:
        cs = next((c for c in datos.get("coffeeshops", [])
                   if c.get("id") == cs_ficha), None)
        if cs is not None:
            ficha_coffeeshop(datos, cs)
            return

    st.markdown("## 🏪 Asociaciones / Coffeeshops")
    st.caption("Directorio geolocalizado: puntúa los locales y descubre "
               "qué productores tienen en su menú.")

    # ---- Filtros en cascada (país -> ciudad) ----
    paises = datos.get("paises", [])
    ciudades = datos.get("ciudades", [])
    nombres_paises = [p["nombre"] for p in paises]
    filtro_pais = st.selectbox("🌍 País", ["Todos"] + nombres_paises,
                               key="cs_filtro_pais")
    pais_id = next((p["id"] for p in paises if p["nombre"] == filtro_pais), "")
    ciudades_filtro = [c for c in ciudades if c.get("pais_id") == pais_id]
    if filtro_pais == "Todos":
        opciones_ciudad = ["Todas"] + [c["nombre"] for c in ciudades]
    else:
        opciones_ciudad = ["Todas"] + [c["nombre"] for c in ciudades_filtro]
    filtro_ciudad = st.selectbox("🏙 Ciudad", opciones_ciudad, key="cs_filtro_ciudad")
    ciudad_id = next((c["id"] for c in ciudades
                      if c.get("nombre") == filtro_ciudad
                      and (not pais_id or c.get("pais_id") == pais_id)), "")

    # ---- Listado filtrado / ranking global ----
    locales = datos.get("coffeeshops", [])
    if pais_id:
        locales = [c for c in locales if c.get("pais_id") == pais_id]
    if ciudad_id:
        locales = [c for c in locales if c.get("ciudad_id") == ciudad_id]
    locales = sorted(locales,
                     key=lambda c: -core.nota_media_coffeeshop(c))

    modo_ranking = (filtro_pais == "Todos" and filtro_ciudad == "Todas")

    if not locales:
        st.info("No hay asociaciones con esos criterios todavía.")
    else:
        if modo_ranking:
            st.markdown("### 🏆 Ranking global de asociaciones")
            st.caption("Todas las asociaciones ordenadas por nota media. "
                       "Usa los filtros de arriba para buscar por país o ciudad.")
        else:
            st.caption(f"{len(locales)} local{'es' if len(locales) != 1 else ''}")
        # Grid responsive: 3 columnas en escritorio, 1 en móvil
        for i in range(0, len(locales), 3):
            fila = locales[i:i + 3]
            cols = st.columns(3)
            for col, cs in zip(cols, fila):
                with col:
                    pos = i + fila.index(cs) + 1 if modo_ranking else 0
                    st.markdown(tarjeta_coffeeshop(datos, cs, posicion=pos),
                                unsafe_allow_html=True)
                    if st.button("👁 Ver Asociación",
                                 key=f"cs_ver_{cs['id']}",
                                 width="stretch"):
                        st.session_state["cs_ficha"] = cs["id"]
                        st.rerun()

    # ---- Panel de administración (solo admin 👑) ----
    panel_admin_coffeeshops(datos)


# =============================================================================
# SECCIÓN INICIO — portada: hero con la imagen de marca + tarjetas de navegación
# =============================================================================

def seccion_inicio(datos: dict, logueado: bool):
    """Portada: banner grande con la imagen del personaje (fondo + overlay para
    legibilidad) y tarjetas clicables que llevan a cada sección. Integra la
    estética de la imagen (violeta galaxia + verde neón) de forma profesional."""
    n = len(datos.get("catas", []))
    if core._db_nube() is not None:
        chip = "<span style='color:#8BE9B0;font-weight:700'>🟢 Conectado</span>"
    else:
        chip = "<span style='color:#E8C35A;font-weight:700'>💾 Modo local</span>"
    if logueado:
        chip += (f"&nbsp;&nbsp;<span style='color:#A5AEB8'>·&nbsp;👤 "
                 f"{_html.escape(str(st.session_state.get('usuario', '')))}</span>")

    hero_b64 = _asset_b64("inicio_hero.jpg")
    avatar_b64 = _asset_b64("inicio_avatar.jpg")

    intro = (
        "Encuentra, mira y evalúa materiales de todas las granjas. "
        "Localiza sitios donde encontrarlos (Asociaciones, Coffeeshops…)")

    # ---- HERO: imagen de fondo + overlay degradado para legibilidad ----
    estilo_img = (f"background:url('{hero_b64}') center/cover no-repeat;"
                  if hero_b64 else
                  "background:linear-gradient(135deg,rgba(126,90,224,0.30),rgba(74,222,128,0.22));")
    st.markdown(
        f'<div style="position:relative;border-radius:20px;overflow:hidden;'
        f'border:1px solid rgba(126,90,224,0.35);box-shadow:0 12px 40px rgba(0,0,0,0.45);'
        f'min-height:300px;display:flex;align-items:flex-end;{estilo_img}">'
        f'<div style="position:absolute;inset:0;background:linear-gradient(180deg,'
        f'rgba(10,13,22,0.25) 0%,rgba(10,13,22,0.55) 55%,rgba(10,13,22,0.92) 100%);"></div>'
        f'<div style="position:relative;padding:2.2rem 1.6rem 1.5rem;width:100%;'
        f'display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;flex-wrap:wrap;">'
        f'<div style="min-width:0">'
        f'<div style="font-size:1.85rem;font-weight:800;color:#F5F8FB;'
        f'letter-spacing:-0.03em;line-height:1.1;text-shadow:0 2px 18px rgba(0,0,0,0.6)">'
        f'TerpsXHunter</div>'
        f'<div style="font-size:1rem;color:#D7DEE8;margin-top:6px;max-width:520px;'
        f'text-shadow:0 1px 10px rgba(0,0,0,0.6)">{_html.escape(intro)}</div>'
        f'<div style="margin-top:12px;font-size:0.82rem">{chip}'
        f'&nbsp;&nbsp;<span style="color:#A5AEB8">· {n} catas registradas</span></div>'
        f'</div>'
        f'<div style="flex:0 0 auto">'
        f'<img src="{avatar_b64}" alt="" style="width:116px;height:116px;'
        f'border-radius:20px;object-fit:cover;border:2px solid rgba(126,90,224,0.5);'
        f'box-shadow:0 6px 22px rgba(0,0,0,0.5)">'
        f'</div></div></div>',
        unsafe_allow_html=True)

    # ---- Acceso rápido: botón de sesión si invitado ----
    if not logueado:
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("🔑 Iniciar sesión para votar", key="inicio_login",
                         type="primary", width="stretch"):
                st.session_state["pagina"] = "🔐 Acceso"
                st.rerun()
        with c2:
            if st.button("✨ Crear mi perfil", key="inicio_registro",
                         width="stretch"):
                st.session_state["pagina"] = "🔐 Acceso"
                st.rerun()

    st.markdown("### 🧭 Explora las secciones")
    st.caption("Toca una tarjeta para ir a esa sección.")

    # ---- Tarjetas de navegación (clicable, con icono + descripción) ----
    secciones = [
        ("📦", "Catálogo", "Busca, filtra y abre la ficha de cada producto.", "📦 Catálogo"),
        ("🎯", "Por votar", "Puntúa los productos que te faltan por probar.", "🎯 Por votar"),
        ("🏭", "Productores", "Cultivadores, sus catas y su nota media.", "🏭 Productores"),
        ("🏆", "Rankings", "Top general, personal y de la comunidad.", "🏆 Rankings"),
        ("📈", "Evolución", "Tendencia por año, temporada y productor.", "📈 Evolución"),
        ("🏪", "Asociaciones", "Coffeeshops y dónde encontrar cada producto.", "🏪 Asociaciones"),
        ("👥", "Perfiles", "Tu perfil, preferencias y contraseña.", "👥 Perfiles"),
    ]
    if es_admin(datos) or es_profesional(datos):
        secciones.insert(1, ("➕", "Nueva Cata", "Añade un nuevo producto al registro.",
                             "➕ Nueva Cata"))

    # Grid responsivo 3-2-1 columnas
    with st.container(key="inicio_grid"):
        for i in range(0, len(secciones), 3):
            fila = secciones[i:i + 3]
            cols = st.columns(3)
            for col, (icono, titulo, desc, pagina_dest) in zip(cols, fila):
                with col:
                    with st.container(key=f"inicio_card_{pagina_dest}", border=True):
                        st.markdown(
                            f'<div style="text-align:center;padding:6px 4px 2px">'
                            f'<div style="font-size:2rem;line-height:1">{icono}</div>'
                            f'<div style="font-weight:800;font-size:15px;color:#F2F5F9;'
                            f'margin-top:6px">{_html.escape(titulo)}</div>'
                            f'<div style="font-size:12px;color:#A5AEB8;margin-top:3px;'
                            f'line-height:1.4">{_html.escape(desc)}</div></div>',
                            unsafe_allow_html=True)
                        if st.button("Abrir", key=f"inicio_btn_{pagina_dest}",
                                     width="stretch"):
                            st.session_state["pagina"] = pagina_dest
                            st.rerun()


def seccion_perfiles(datos: dict):
    st.markdown("## 👥 Perfiles")
    admin = es_admin(datos)
    usuario = st.session_state.get("usuario", "")
    if admin:
        st.caption("👑 **Modo admin:** gestionas las cuentas de todos los usuarios.")
    else:
        st.caption("Aquí puedes ver tu perfil y cambiar tu contraseña.")

    if not datos["perfiles"]:
        st.info("Todavía no hay perfiles.")
        return

    if not admin:
        # El usuario normal solo ve su propia tarjeta
        perfil = perfil_por_nombre(datos, usuario)
        if perfil is not None:
            _tarjeta_perfil(datos, perfil, admin=False)
        return

    for perfil in datos["perfiles"]:
        _tarjeta_perfil(datos, perfil, admin=True)


# =============================================================================
# MAIN
# =============================================================================

# Secciones visibles sin iniciar sesión (modo invitado, solo lectura)
PAGINAS_LECTURA = {"🏠 Inicio", "📦 Catálogo", "🏭 Productores", "🏆 Rankings",
                   "📈 Evolución", "🏪 Asociaciones"}

# Claves de sesión con valor por defecto (se crean UNA vez al arrancar)
_STATE_DEFAULTS = {
    "usuario": "",            # perfil logueado ('' = invitado)
    "pagina": "🏠 Inicio",    # sección activa (arranca en la portada)
    "menu_abierto": False,    # panel ☰ móvil
    "ficha_id": None,         # ficha de producto abierta en catálogo
    "prod_ficha": None,       # ficha de productor abierta
    "votar_id": None,         # formulario de voto abierto en "Por votar"
}


def init_state():
    """Inicializa las claves de sesión una sola vez. Evita re-ejecuciones y
    mantiene el estado estable entre reruns (sin parpadeos en el móvil)."""
    for clave, valor in _STATE_DEFAULTS.items():
        if clave not in st.session_state:
            st.session_state[clave] = valor


def main():
    init_state()
    datos = cargar()
    # Auto-recuperación de caché: si la app está en modo nube pero la caché
    # sirvió un resultado vacío (p. ej. por un fallo transitorio de conexión
    # al arrancar) y la BD SÍ tiene datos, se limpia la caché y se reintenta.
    if core._db_nube() is not None and not datos["catas"]:
        try:
            import db_supabase as _db
            _fresco = _db.cargar_datos()
            if _fresco.get("catas"):
                cargar.clear()
                st.rerun()
        except Exception:
            pass
    # Retorno del proveedor OAuth (?code=... en la URL) → valida y abre sesión
    _manejar_retorno_oauth(datos)
    # Sesión persistente: cookie válida → entrar sin formularios
    _auto_login_cookie(datos)
    inyectar_css()  # UI mobile-first (solo vista; no toca la lógica de datos)
    _inyectar_marca_agua()  # marca de agua sutil con el logo del personaje
    logueado = bool(st.session_state.get("usuario"))

    # Invitado que pulsó "Iniciar sesión" -> pantalla de acceso
    if not logueado and st.session_state.get("pagina") == "🔐 Acceso":
        pantalla_login(datos)
        return

    sidebar(datos)
    menu_movil(datos)

    # La página se lee DESPUÉS del sidebar: el radio de navegación es quien
    # actualiza session_state["pagina"] con la opción pulsada. Leerla antes
    # provocaba que la sección mostrada fuera siempre la del clic anterior.
    pagina = st.session_state.get("pagina", "🏠 Inicio")
    if pagina == "🔐 Acceso":  # caso residual tras iniciar sesión
        pagina = "🏠 Inicio"

    # Scroll al inicio al cambiar de sección/ficha/formulario: evita quedarse
    # a mitad de página en el móvil. Se inyecta SOLO cuando la vista cambia.
    clave_vista = (pagina, st.session_state.get("ficha_id"),
                   st.session_state.get("votar_id"))
    if st.session_state.get("_vista_prev") != clave_vista:
        st.session_state["_vista_prev"] = clave_vista
        st.html(
            "<script>setTimeout(()=>{"
            "window.parent.document.querySelector("
            "'[data-testid=\"stAppViewContainer\"]')?.scrollTo({top:0});"
            "window.scrollTo({top:0});},0)</script>")

    # Al cambiar de sección se cierra la ficha de producto abierta
    if pagina != "📦 Catálogo":
        st.session_state.pop("ficha_id", None)

    # Invitado tocando una sección restringida -> login obligatorio
    if not logueado and pagina not in PAGINAS_LECTURA:
        pantalla_login(datos)
        return

    # El recuadro 'TerpsXHunter' con la imagen solo está en el INICIO
    # (seccion_inicio usa su propio banner grande). El resto de secciones
    # arrancan directamente con su título, sin la banda compacta.

    if pagina == "🏠 Inicio":
        seccion_inicio(datos, logueado)
    elif pagina == "📦 Catálogo":
        seccion_catalogo(datos)
    elif pagina == "➕ Nueva Cata":
        seccion_nueva_cata(datos)
    elif pagina == "🎯 Por votar":
        seccion_por_votar(datos)
    elif pagina == "🏭 Productores":
        seccion_productores(datos)
    elif pagina == "🏆 Rankings":
        seccion_rankings(datos)
    elif pagina == "📈 Evolución":
        seccion_evolucion(datos)
    elif pagina == "🏪 Asociaciones":
        seccion_asociaciones(datos)
    else:
        seccion_perfiles(datos)


if __name__ == "__main__":
    main()
