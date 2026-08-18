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
import shutil
import hashlib
import secrets
import base64
import io
import textwrap
import html as _html
from datetime import datetime

import pandas as pd
import streamlit as st

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
    page_title="🌿 Sistema de Catas",
    page_icon="🌿",
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


@st.cache_data(show_spinner=False)
def cargar() -> dict:
    """Lee catas.json UNA vez por sesión (caché). Se invalida en cada guardar()."""
    return core.cargar_datos()


def guardar(datos: dict):
    """Persiste los datos: backup del estado previo + escritura + invalida caché."""
    _backup_antes_de_guardar()
    core.guardar_datos(datos)
    cargar.clear()  # el próximo rerun relee del disco (nunca datos stale)


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
    BD porque el filesystem de la nube es efímero; en local b64 = ''."""
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
            with open(destino, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception:
        b64 = ""
    return ruta, b64


def inyectar_css():
    """UI mobile-first: compacta paddings, tarjetas oscuras, botones ≥46px,
    sliders táctiles y apilado automático de columnas en pantallas pequeñas.
    Solo afecta a la capa de vistas; la lógica de datos no se toca."""
    st.markdown("""<style>
/* ============ FONDO OSCURO BASE ============ */
html, body, [data-testid="stAppViewContainer"] { background: #0E1116; }

/* ============ MÓVIL (360-480px): compactar y apilar ============ */
@media (max-width: 480px) {
  [data-testid="stMainBlockContainer"] { padding: 0.4rem 0.75rem 3.5rem; max-width: 100%; }
  [data-testid="stHeader"] { background: transparent; }
  /* Apilar filas de columnas que contienen sliders, desplegables o pills */
  [data-testid="stHorizontalBlock"]:has([data-testid="stSlider"]),
  [data-testid="stHorizontalBlock"]:has([data-testid="stSelectbox"]),
  [data-testid="stHorizontalBlock"]:has([data-testid="stPills"]) { flex-wrap: wrap; gap: 0.25rem; }
  [data-testid="stHorizontalBlock"]:has([data-testid="stSlider"]) > [data-testid="column"],
  [data-testid="stHorizontalBlock"]:has([data-testid="stSelectbox"]) > [data-testid="column"],
  [data-testid="stHorizontalBlock"]:has([data-testid="stPills"]) > [data-testid="column"] { min-width: 100% !important; }
}

/* ============ TARJETAS OSCURAS ============ */
[data-testid="stVerticalBlockBorderWrapper"] {
  background: #161A20; border: 1px solid #262B33; border-radius: 14px;
  padding: 0.1rem 0.3rem;
}

/* ============ BOTONES ERGONÓMICOS (44-48px) ============ */
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button {
  min-height: 46px; border-radius: 10px; font-weight: 600; font-size: 15px;
}
[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"],
[data-testid="stBaseButton-primary"] { box-shadow: 0 2px 12px rgba(63,185,106,.28); }
/* CTA del formulario de cata: fijo al fondo en móvil */
.st-key-btn_guardar_voto { position: sticky; bottom: 0.5rem; z-index: 999; }
.st-key-btn_guardar_voto button { box-shadow: 0 -6px 18px rgba(0,0,0,.55); }

/* ============ SLIDERS TÁCTILES ============ */
[data-testid="stSlider"] [role="slider"] { width: 24px !important; height: 24px !important; margin-top: -12px !important; }
[data-testid="stSlider"] [data-baseweb="slider"] > div > div { height: 6px; }

/* ============ DESPLEGABLES MÁS ALTOS ============ */
[data-testid="stSelectbox"] [data-baseweb="select"] > div { min-height: 44px; }

/* ============ NAVEGACIÓN / RADIO TÁCTIL ============ */
[data-testid="stRadio"] label { padding: 0.55rem 0.4rem; font-size: 15px; border-radius: 8px; }
[data-testid="stRadio"] label:hover { background: #1B2129; }

/* ============ PILLS (filtros táctiles) ============ */
[data-testid="stPills"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
[data-testid="stPills"] button { min-height: 36px; border-radius: 18px; font-size: 13px; padding: 0 14px; }

/* ============ EXPANDERS COMPACTOS ============ */
[data-testid="stExpander"] details { border-radius: 12px; background: #14181F; border: 1px solid #262B33; }
[data-testid="stExpander"] summary { min-height: 46px; display: flex; align-items: center; }

/* ============ CHIPS: permitir salto de línea en móvil ============ */
[data-testid="stMarkdownContainer"] p { overflow-wrap: anywhere; }

/* ============ OCULTAR FOOTER DE STREAMLIT ============ */
footer { visibility: hidden; }

/* ============ BOTÓN ☰ MENÚ: solo en móvil ============ */
.st-key-btn_menu { display: none; }
@media (max-width: 769px) {
  .st-key-btn_menu { display: block; }
  .st-key-btn_menu button { font-size: 16px; min-height: 44px; }
}

/* ============ LISTA DE RANKING (filas foto|datos|nota) ============ */
[class*="st-key-rk_lista"] [data-testid="stVerticalBlockBorderWrapper"] { padding: 0.45rem 0.6rem; }
[class*="st-key-rk_lista"] [data-testid="column"] img { border-radius: 8px; }
[class*="st-key-rk_lista"] [data-testid="column"]:last-child { text-align: right; }
@media (max-width: 768px) {
  [class*="st-key-rk_lista"] [data-testid="stVerticalBlockBorderWrapper"] { padding: 0.3rem 0.45rem; }
  [class*="st-key-rk_lista"] p { font-size: 13px !important; line-height: 1.3; }
  [class*="st-key-rk_lista"] [data-testid="column"] { min-width: 0 !important; }
}

/* ============ GRID DEL CATÁLOGO (tarjetas 3-2-1 columnas) ============ */
[class*="st-key-grid_catalogo"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: 0.4rem; }
[class*="st-key-grid_catalogo"] [data-testid="column"] { min-width: 0; }
@media (min-width: 1100px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"] { flex: 0 0 calc(33.33% - 0.3rem) !important; }
}
@media (max-width: 1099px) and (min-width: 769px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"] { flex: 0 0 calc(50% - 0.3rem) !important; }
}
@media (max-width: 768px) {
  [class*="st-key-grid_catalogo"] [data-testid="column"] { flex: 1 0 100% !important; min-width: 100% !important; }
}

/* ============ FICHA DE PRODUCTO (2 columnas; colapsa en móvil) ============ */
[class*="st-key-ficha_detalle"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
@media (max-width: 768px) {
  [class*="st-key-ficha_detalle"] [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    min-width: 100% !important; flex: 1 0 100% !important;
  }
}
</style>""", unsafe_allow_html=True)


def mostrar_foto(foto: str, width: int = 120, emoji: str = "🌿", b64: str = ""):
    """Muestra la foto si existe; si no, un placeholder discreto.
    En modo nube, `b64` trae la imagen desde la BD (filesystem efímero)."""
    if b64:
        st.markdown(f'<img src="data:image/jpeg;base64,{b64}" '
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
    En modo nube se pasa `b64` (foto guardada en la BD) y se omite el archivo."""
    if b64:
        return (f'<img src="data:image/jpeg;base64,{b64}" alt="" '
                f'style="width:{px}px;height:{px}px;border-radius:{radius}px;'
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
    Renderiza los 4 bloques de sliders con ETIQUETA visible y escala 1-100
    (enteros). Devuelve las puntuaciones en escala 1-10 (÷10) para mantener
    intacta la capa de datos (puntuaciones_detalle en 1-10).
    - Cada bloque va en un desplegable (el primero abierto): sin scroll
      infinito antes de guardar, y en escritorio los sliders van de 2 en 2.
    - voto_precargar: dict de puntuaciones (1-10) a precargar (×10 al slider).
    - prefijo: identifica el conjunto de sliders sin colisión de keys.
    """
    scores = {}
    primer_bloque = True
    # Flag de precarga: el pop de las keys solo debe hacerse la PRIMERA vez que
    # se muestra el conjunto (al abrir el editor / cargar la cata). Si se hiciera
    # en cada rerun, los sliders volverían a saltar al valor precargado nada más
    # moverlos (bug: el usuario editaba y se guardaba siempre el valor original).
    flag_pre = f"{prefijo}_precargado"
    for meta in core.BLOQUES:
        with st.expander(f"{meta['titulo']} · {int(meta['peso'] * 100)}%",
                         expanded=primer_bloque):
            primer_bloque = False
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
            # Nota del bloque (sobre 100), en vivo
            nota10 = core.calcular_nota_bloque(scores_bloque, meta["clave"])
            st.caption(f"📊 Bloque: **{nota10 * 10:.0f} / 100**")
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
    .pod-card {{ flex:1; min-width:0; border-radius:16px; padding:12px 8px 10px; text-align:center;
      background:linear-gradient(180deg,#1B2129 0%,#161A20 100%); position:relative; }}
    .pod-1 {{ border:2px solid #FFD700; box-shadow:0 0 20px rgba(255,215,0,.32), inset 0 0 14px rgba(255,215,0,.07); }}
    .pod-2 {{ border:2px solid #C0C0C0; box-shadow:0 0 16px rgba(192,192,192,.22), inset 0 0 10px rgba(192,192,192,.05); }}
    .pod-3 {{ border:2px solid #CD7F32; box-shadow:0 0 16px rgba(205,127,50,.22), inset 0 0 10px rgba(205,127,50,.05); }}
    .pod-foto {{ width:100%; margin-bottom:8px; }}
    .pod-foto img {{ display:block; }}
    .pod-medal {{ font-size:30px; line-height:1; }}
    .pod-name {{ font-weight:700; font-size:13px; color:#F2F5F9; margin-top:6px; line-height:1.25;
      display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
    .pod-prod {{ font-size:11px; color:#8B93A1; margin-top:2px; white-space:nowrap;
      overflow:hidden; text-overflow:ellipsis; }}
    .pod-nota {{ font-size:21px; font-weight:800; margin-top:6px; line-height:1; }}
    .pod-votos {{ font-size:10px; color:#6B7480; margin-top:3px; }}
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
    - Admin: todas (puede crear productos).
    - Usuario normal: todo menos crear productos (➕ Nueva Cata)."""
    if not st.session_state.get("usuario"):
        return ["📦 Catálogo", "➕ Nueva Cata", "🎯 Por votar", "🏭 Productores",
                "🏆 Rankings", "📈 Evolución", "👥 Perfiles"]
    if es_admin(datos):
        return ["📦 Catálogo", "➕ Nueva Cata", "🎯 Por votar", "🏭 Productores",
                "🏆 Rankings", "📈 Evolución", "👥 Perfiles"]
    return ["📦 Catálogo", "🎯 Por votar", "🏭 Productores",
            "🏆 Rankings", "📈 Evolución", "👥 Perfiles"]


def menu_movil(datos: dict):
    """Botón '☰ Menú' (visible solo en móvil por CSS) que despliega la
    navegación en la propia página. Streamlit no permite abrir el sidebar
    nativo con JS (sanitiza scripts), así que es un panel propio, más visual."""
    if st.button("☰ Menú", key="btn_menu", use_container_width=True):
        st.session_state["menu_abierto"] = not st.session_state.get("menu_abierto", False)
    if st.session_state.get("menu_abierto"):
        with st.container(border=True):
            st.caption("Navegación")
            for p in paginas_para(datos):
                if st.button(p, key=f"menu_{p}", use_container_width=True):
                    st.session_state["pagina"] = p
                    st.session_state["menu_abierto"] = False
                    st.rerun()


def sidebar(datos: dict):
    with st.sidebar:
        st.markdown("## 🌿 CATAS")
        st.caption("registro & rankings · web")

        # ---- Estado de datos (diagnóstico de conexión) ----
        if core._db_nube() is not None:
            st.caption(f"🗄 **Conectado a Supabase** · {len(datos['catas'])} catas")
        else:
            st.caption(f"🗄 Modo local (catas.json) · {len(datos['catas'])} catas")

        # ---- Sesión (usuario logueado o modo invitado) ----
        st.divider()
        usuario = st.session_state.get("usuario", "")
        if usuario:
            perfil = perfil_por_nombre(datos, usuario)
            badge = "  👑" if es_admin(datos) else (
                "  🤝" if perfil is not None and perfil.get("es_confianza") else "")
            st.markdown(f"**👤 {usuario}**{badge}")
            if st.button("🚪 Cerrar sesión", use_container_width=True):
                st.session_state.pop("usuario", None)
                st.session_state["pagina"] = "📦 Catálogo"
                st.rerun()
        else:
            st.markdown("**👤 Invitado** *(solo lectura)*")
            if st.button("🔑 Iniciar sesión", use_container_width=True):
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
        st.caption(f"🌿 {total} catas · {votos} votos")


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
# PANTALLA DE LOGIN / REGISTRO
# =============================================================================

def pantalla_login(datos: dict):
    """Muestra login y registro; solo se llega al resto de la app con sesión."""
    st.markdown("## 🌿 Sistema de Catas")
    st.caption("Inicia sesión con tu perfil para votar. ¿No tienes cuenta? "
               "Crea una abajo (solo nombre y contraseña).")

    # ---- Login ----
    with st.form("login"):
        l_nombre = st.text_input("Nombre de usuario")
        l_pw = st.text_input("Contraseña", type="password")
        entrar = st.form_submit_button("🔓 Entrar", type="primary",
                                       use_container_width=True)
    if entrar:
        nombre = l_nombre.strip()
        perfil = perfil_por_nombre(datos, nombre)
        if perfil is None:
            st.error(f"'{nombre}' no existe. Regístrate abajo.")
        elif perfil.get("password_hash"):
            if verificar_password(l_pw, perfil["password_hash"]):
                st.session_state["usuario"] = perfil["nombre"]
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
                st.success(f"✅ Contraseña asignada a '{nombre}'. ¡Bienvenido!")
                st.rerun()
            else:
                st.warning(f"'{nombre}' todavía no tiene contraseña: escríbela "
                           "aquí para reclamar la cuenta.")

    st.divider()

    # ---- Registro ----
    st.markdown("### ✨ ¿Nuevo? Crea tu perfil")
    with st.form("registro"):
        r_nombre = st.text_input("Nombre de usuario", key="reg_nombre")
        r_pw = st.text_input("Contraseña (mínimo 4 caracteres)", type="password",
                             key="reg_pw")
        r_pw2 = st.text_input("Repite la contraseña", type="password", key="reg_pw2")
        crear = st.form_submit_button("➕ Crear cuenta", use_container_width=True)
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
            datos["perfiles"].append({
                "id": core.generar_id({p["id"] for p in datos["perfiles"]},
                                      prefijo="p_"),
                "nombre": nombre,
                "password_hash": hash_password(r_pw),
            })
            guardar(datos)
            st.session_state["usuario"] = nombre
            st.rerun()

    sin_pw = perfiles_sin_password(datos)
    if sin_pw:
        st.caption(f"Cuentas sin contraseña (reclámalas desde el login): "
                   f"{', '.join(sin_pw)}")

    st.divider()
    if st.button("← Volver como invitado", use_container_width=True):
        st.session_state["pagina"] = "📦 Catálogo"
        st.rerun()


# =============================================================================
# SECCIÓN 1 — NUEVA CATA (crear producto + votar; solo admin)
# =============================================================================

def seccion_nueva_cata(datos: dict):
    st.markdown("## ➕ Nueva Cata")
    # Crear productos es exclusivo de administradores (los demás solo votan)
    if not es_admin(datos):
        st.warning("🔒 Solo los **administradores** pueden crear productos nuevos. "
                   "Tú puedes votar desde **🎯 Por votar** o abriendo la ficha de "
                   "cualquier producto en **📦 Catálogo**.")
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
    if st.button("💾 Guardar voto", type="primary", use_container_width=True,
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

def seccion_por_votar(datos: dict):
    st.markdown("## 🎯 Por votar")
    perfil = perfil_activo(datos)
    if perfil is None:
        st.info("Primero crea o elige tu perfil en el menú lateral (👤 Votando como).")
        return

    pendientes = [c for c in datos["catas"]
                  if core.voto_de_perfil(c, perfil["id"]) is None]

    st.caption(f"Votando como **{perfil['nombre']}** — {len(pendientes)} producto"
               f"{'s' if len(pendientes) != 1 else ''} sin tu voto.")

    if not pendientes:
        if datos["catas"]:
            st.success("🎉 ¡Ya has votado todos los productos del catálogo!")
        else:
            st.info("Todavía no hay productos en el catálogo.")
        return

    for cata in pendientes:
        with st.container(border=True):
            fila_producto(cata, datos=datos)
            with st.expander(f"🗳 Votar '{cata.get('nombre', '')}'"):
                prefijo_pv = f"pv_{cata['id']}_"
                render_sliders_blocks(prefijo=prefijo_pv)
                coment = st.text_area("Comentarios (opcional)",
                                      key=f"pv_coment_{cata['id']}")
                _, nota_final10 = core.calcular_notas(
                    obtener_scores_actuales(prefijo_pv))
                st.markdown(f"### 💛 Tu nota: **{nota_final10 * 10:.1f} / 100**")
                if st.button("💾 Guardar mi voto", type="primary",
                             key=f"pv_guardar_{cata['id']}"):
                    scores = obtener_scores_actuales(prefijo_pv)
                    resultado = core.upsert_voto(cata, perfil["id"], scores)
                    if coment.strip():
                        cata["comentarios"] = (cata.get("comentarios", "") +
                                               "\n\n" + coment.strip()).strip()
                    guardar(datos)
                    for k in list(st.session_state.keys()):
                        if k.startswith(prefijo_pv):
                            del st.session_state[k]
                    st.success(f"✅ Voto de {perfil['nombre']} guardado — "
                               f"{cata['nombre']} · "
                               f"{core._flotante(core.voto_de_perfil(cata, perfil['id']).get('nota_final')):.1f}/100")
                    st.rerun()


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
        if st.button("Sí, eliminar", type="primary", use_container_width=True):
            datos["catas"] = [c for c in datos["catas"] if c.get("id") != cata.get("id")]
            guardar(datos)
            st.success("Producto eliminado.")
            st.rerun()
    with c2:
        if st.button("Cancelar", use_container_width=True):
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
        f'<div style="font-weight:700;font-size:14px;color:#F2F5F9;line-height:1.25">{nombre}</div>'
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

    # Botones compactos: abrir ficha (siempre) + eliminar (solo admin)
    b_abrir, b_del = st.columns([3, 1])
    with b_abrir:
        if st.button("📂 Abrir ficha", key=f"abrir_{cata['id']}",
                     use_container_width=True):
            st.session_state["ficha_id"] = cata["id"]
            st.rerun()
    with b_del:
        if admin:
            if st.button("🗑", key=f"del_{cata['id']}",
                         use_container_width=True):
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

    # ---- Buscador y filtros (pills táctiles; año en desplegable) ----
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
    lista.sort(key=lambda c: (-core.nota_media(c), str(c.get("nombre", "")).lower()))

    st.caption(f"{len(lista)} producto{'s' if len(lista) != 1 else ''}")
    if not lista:
        st.info("Sin productos con esos criterios.")
        return

    # Grid de tarjetas: 3 por fila en desktop (CSS: 2 en tablets, 1 en móvil)
    with st.container(key="grid_catalogo"):
        for i in range(0, len(lista), 3):
            fila = lista[i:i + 3]
            cols = st.columns(3)
            for col, cata in zip(cols, fila):
                with col:
                    with st.container(border=True):
                        tarjeta_catalogo(cata, datos, admin)


def seccion_comentarios(datos: dict, cata: dict):
    """Comentarios de usuarios (visibles para todos; publican la gente de
    confianza y los admins). Se usa en la ficha premium y en la de edición."""
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
        st.markdown("---")
        nuevo_comentario = st.text_area("Tu comentario (como gente de confianza)",
                                        key=f"comentario_{cata['id']}")
        if st.button("📝 Publicar comentario", key=f"pub_com_{cata['id']}",
                     use_container_width=True):
            if nuevo_comentario.strip():
                cata.setdefault("comentarios_usuarios", []).append({
                    "perfil_id": perfil["id"],
                    "nombre": perfil["nombre"],
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "texto": nuevo_comentario.strip()})
                guardar(datos)
                st.success("✅ Comentario publicado.")
                st.rerun()
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

        # ================= COLUMNA IZQUIERDA: imagen + info =================
        with col_izq:
            ruta_foto = core.resolver_ruta_foto(cata.get("foto"))
            if ruta_foto:
                st.image(ruta_foto, use_container_width=True)
            else:
                st.markdown(placeholder_imagen_fluid("🌿"), unsafe_allow_html=True)

            st.markdown(f"### {nombre}")
            st.markdown(f"🏭 **{productor}**")
            st.markdown(" ".join(chips_row), unsafe_allow_html=True)

            # Puntuación general grande + profesional
            st.markdown(
                f'<div style="font-size:38px;font-weight:800;color:{color_nota};'
                f'line-height:1.1;margin-top:6px">{media:.1f}</div>',
                unsafe_allow_html=True)
            st.caption(f"{n_votos} voto{'s' if n_votos != 1 else ''}{prof_txt}")

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
                             use_container_width=True):
                    p = perfil_por_nombre(datos, nuevo_perfil)
                    st.session_state[f"nv_{cata['id']}_{p['id']}"] = True
                    st.rerun()
        else:
            st.caption("✓ Todos los perfiles han votado este producto.")

        # Editor del voto NUEVO (para el perfil pendiente elegido)
        for p in pendientes:
            if st.session_state.get(f"nv_{cata['id']}_{p['id']}"):
                with st.expander(f"🗳 Votar como {p['nombre']}", expanded=True):
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
            if st.button("➕ Añadir productor", type="primary", use_container_width=True):
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
    for prod in datos["productores"]:
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
                                     use_container_width=True):
                            st.session_state["prod_ficha"] = prod["nombre"]
                            st.rerun()
                    with b2:
                        if st.button("✏️", key=f"prod_ren_{prod['id']}",
                                     use_container_width=True):
                            st.session_state[f"prod_edit_{prod['id']}"] = True
                    with b3:
                        if st.button("🗑", key=f"prod_del_{prod['id']}",
                                     use_container_width=True):
                            if len(catas_prod) > 0:
                                st.error("Tiene catas asignadas: bórralas o reasígnalas "
                                         "antes de eliminar el productor.")
                            else:
                                datos["productores"] = [p for p in datos["productores"]
                                                        if p["id"] != prod["id"]]
                                guardar(datos)
                                st.rerun()
                else:
                    if st.button("Abrir", key=f"prod_abrir_{prod['id']}",
                                 use_container_width=True):
                        st.session_state["prod_ficha"] = prod["nombre"]
                        st.rerun()

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
                                     use_container_width=True):
                            nuevo = nuevo_nombre.strip()
                            if nuevo and nuevo != prod["nombre"]:
                                for c in datos["catas"]:
                                    if str(c.get("productor", "")).strip() == prod["nombre"]:
                                        c["productor"] = nuevo
                                prod["nombre"] = nuevo
                            prod["pais"] = nuevo_pais_edit
                            guardar(datos)
                            del st.session_state[f"prod_edit_{prod['id']}"]
                            st.rerun()

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
                            st.rerun()

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
                            st.rerun()
                if st.button("Cerrar ficha", key=f"prod_close_{prod['id']}"):
                    del st.session_state["prod_ficha"]
                    st.rerun()


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
        nombre = f"{medallas.get(pos, '')} {pos}º · {nombre}"
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
        c_foto, c_info, c_nota = st.columns([1, 4, 1],
                                            vertical_alignment="center")
        with c_foto:
            ruta_foto = core.resolver_ruta_foto(cata.get("foto"))
            if ruta_foto:
                st.image(ruta_foto, width=64)
            else:
                st.markdown(placeholder_imagen(64, "🌿", radius=8),
                            unsafe_allow_html=True)
        with c_info:
            st.markdown(f"**{nombre}**")
            st.caption(f"🏭 {productor} · {n_votos} voto"
                       f"{'s' if n_votos != 1 else ''}")
            st.markdown(" ".join(chips_row), unsafe_allow_html=True)
        with c_nota:
            # HTML en UNA SOLA LÍNEA (evita HTML en crudo)
            st.markdown(
                f'<div style="font-size:24px;font-weight:800;color:{color_nota};'
                f'line-height:1.1">{media:.1f}</div>',
                unsafe_allow_html=True)
            if etiqueta_nota:
                st.caption(etiqueta_nota)
        # Enrutamiento cruzado: Rankings -> Detalle del Catálogo
        if st.button("📂 Ver ficha", key=f"abrir_{prefijo_key}_{cata['id']}",
                     use_container_width=True):
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
                    c_foto, c_info, c_nota = st.columns([1, 4, 1],
                                                        vertical_alignment="center")
                    with c_foto:
                        ruta_foto = core.resolver_ruta_foto(cata.get("foto"))
                        if ruta_foto:
                            st.image(ruta_foto, width=64)
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
                    # Enrutamiento cruzado: Top Personal -> Detalle del Catálogo
                    if st.button("📂 Ver ficha", key=f"abrir_rkp_{cata['id']}",
                                 use_container_width=True):
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
                st.line_chart(piv, height=260)
                # Catas de los productores elegidos (con mini foto del productor)
                sub = [c for c in catas_base
                       if str(c.get("productor", "")).strip() in elegidos]
                sub.sort(key=lambda c: (_anio_int(c) or 0, -core.nota_media(c)))
                st.caption(f"{len(sub)} cata(s) en el gráfico")
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
                         horizontal=True, height=280)
            # Ranking en tarjetas con la FOTO del productor (circular)
            st.caption("Top por nota media en la época seleccionada:")
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

def _tarjeta_perfil(datos: dict, perfil: dict, admin: bool):
    """Tarjeta de un perfil: stats + acciones según rol (admin / propio)."""
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
                        st.rerun()

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
                        st.rerun()

        # Gestión (solo admin)
        if admin:
            if not es_owner:
                nuevo_admin = st.toggle("👑 Puede eliminar productos (admin)",
                                        value=bool(perfil.get("es_admin")),
                                        key=f"perf_admin_{perfil['id']}")
                if nuevo_admin != bool(perfil.get("es_admin")):
                    perfil["es_admin"] = nuevo_admin
                    guardar(datos)
                    st.rerun()
            if not es_owner:
                nueva_confianza = st.toggle(
                    "🤝 Gente de confianza (comenta y su voto cuenta como "
                    "valoración profesional)",
                    value=bool(perfil.get("es_confianza")),
                    key=f"perf_conf_{perfil['id']}")
                if nueva_confianza != bool(perfil.get("es_confianza")):
                    perfil["es_confianza"] = nueva_confianza
                    guardar(datos)
                    st.rerun()
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
                                 use_container_width=True):
                        nuevo = nuevo_nombre.strip()
                        if nuevo:
                            perfil["nombre"] = nuevo
                            if es_yo:
                                st.session_state["usuario"] = nuevo
                            guardar(datos)
                        del st.session_state[f"perf_edit_{perfil['id']}"]
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
PAGINAS_LECTURA = {"📦 Catálogo", "🏭 Productores", "🏆 Rankings", "📈 Evolución"}

# Claves de sesión con valor por defecto (se crean UNA vez al arrancar)
_STATE_DEFAULTS = {
    "usuario": "",            # perfil logueado ('' = invitado)
    "pagina": "📦 Catálogo",  # sección activa
    "menu_abierto": False,    # panel ☰ móvil
    "ficha_id": None,         # ficha de producto abierta en catálogo
    "prod_ficha": None,       # ficha de productor abierta
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
    inyectar_css()  # UI mobile-first (solo vista; no toca la lógica de datos)
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
    pagina = st.session_state.get("pagina", "📦 Catálogo")
    if pagina == "🔐 Acceso":  # caso residual tras iniciar sesión
        pagina = "📦 Catálogo"

    # Al cambiar de sección se cierra la ficha de producto abierta
    if pagina != "📦 Catálogo":
        st.session_state.pop("ficha_id", None)

    # Invitado tocando una sección restringida -> login obligatorio
    if not logueado and pagina not in PAGINAS_LECTURA:
        pantalla_login(datos)
        return

    if pagina == "📦 Catálogo":
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
    else:
        seccion_perfiles(datos)


if __name__ == "__main__":
    main()
