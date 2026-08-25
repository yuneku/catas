# -*- coding: utf-8 -*-
"""
===============================================================================
 bot_terps.py — Bot de Telegram de TerpsXHunter (consultas + acceso a la app)
===============================================================================
Botonera con menús navegables (sin escribir comandos):
  · 🌿 Abrir la app        → Web App embebido (la app completa dentro de TG)
  · 🏭 Productores         → botonera de productores → sus productos con nota
  · 🔍 Buscar producto     → escribe el nombre → fichas con nota y foto
  · 🏆 Rankings            → Top general y Top por tipo
  · 📅 Por año/tipo        → filtros con botones
  · ❓ Ayuda               → guía de uso

Dependencias: SOLO requests + psycopg2 (ya presentes). Long polling manual.
Config: token en telegram_bot.token · URL Supabase desde .streamlit/secrets.toml
Ejecución:  python bot_terps.py
===============================================================================
"""
import os
import sys
import json
import time
import base64
import tomllib
import difflib
import unicodedata
import threading
import urllib.parse
import tempfile

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def _leer_token() -> str:
    with open(os.path.join(BASE_DIR, "telegram_bot.token"), encoding="utf-8") as f:
        return f.read().strip()


def _leer_config() -> dict:
    """bot_config.json: {'canal_novedades': '-100...', 'intervalo_min': 15}"""
    ruta = os.path.join(BASE_DIR, "bot_config.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_json_atomico(ruta: str, contenido: dict) -> None:
    """Guarda estado local sin dejar un JSON truncado si el proceso se corta."""
    carpeta = os.path.dirname(ruta)
    fd, temporal = tempfile.mkstemp(prefix=".terpsx_", suffix=".json", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(contenido, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    except Exception:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def _guardar_config(cfg: dict):
    _guardar_json_atomico(os.path.join(BASE_DIR, "bot_config.json"), cfg)


def _leer_snapshot() -> dict:
    ruta = os.path.join(BASE_DIR, "bot_snapshot.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"catas": [], "productores": []}


def _guardar_snapshot(snap: dict):
    _guardar_json_atomico(os.path.join(BASE_DIR, "bot_snapshot.json"), snap)


def _leer_estado() -> dict:
    """Cursor de Telegram persistente: evita reprocesar mensajes al reiniciar."""
    ruta = os.path.join(BASE_DIR, "bot_runtime.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            estado = json.load(f)
        return estado if isinstance(estado, dict) else {}
    except Exception:
        return {}


def _guardar_estado(estado: dict) -> None:
    _guardar_json_atomico(os.path.join(BASE_DIR, "bot_runtime.json"), estado)


TOKEN = _leer_token()
API = f"https://api.telegram.org/bot{TOKEN}"
APP_URL = "https://terpsxhunter.streamlit.app"
MAX_LEN = 3800  # margen sobre el límite de 4096 de Telegram

# URL de Supabase para db_supabase (misma fuente que la app)
try:
    with open(os.path.join(BASE_DIR, ".streamlit", "secrets.toml"), "rb") as _f:
        _sec = tomllib.load(_f)
    _url = _sec.get("supabase", {}).get("url", "")
    if _url:
        os.environ["SUPABASE_URL"] = _url
except Exception as _e:
    print(f"[bot_terps] ⚠️ No se pudo leer secrets.toml: {_e}")

import db_supabase  # noqa: E402  (usa SUPABASE_URL del entorno)

# --------------------------------------------------------------------------
# Datos (caché con refresco)
# --------------------------------------------------------------------------
_datos = None
_espera_busqueda = {}  # chat_id -> True (el próximo texto es una búsqueda)
_ultima_busqueda = {}  # chat_id -> consulta (para paginar resultados)
_filtro_busq = {}      # chat_id -> {"productor","tipo","anio"} (búsqueda asistida)


def cargar_datos() -> dict:
    global _datos
    try:
        _datos = db_supabase.cargar_datos()
    except Exception as e:
        print(f"[bot_terps] ⚠️ Error cargando datos: {e}")
        _datos = _datos or {"perfiles": [], "productores": [], "catas": [],
                            "paises": [], "ciudades": [], "coffeeshops": []}
    return _datos


def nota_media(cata):
    vs = [v for v in cata.get("votos", [])
          if isinstance(v, dict) and v.get("nota_final") is not None]
    if not vs:
        return None, 0
    return sum(float(v["nota_final"]) for v in vs) / len(vs), len(vs)


def _fmt_nota(n, nvotos):
    if n is None:
        return "sin votos"
    return f"⭐ {n:.1f} ({nvotos} voto{'s' if nvotos != 1 else ''})"


def _norm(s: str) -> str:
    """Normaliza para comparar: minúsculas, sin acentos, sin no-alfanuméricos."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = s.encode("ascii", "ignore").decode("utf-8", "ignore")
    return "".join(c for c in s.lower() if c.isalnum())


def _md(txt):
    """Escapa caracteres de Markdown para no romper el formato (* _ ` ~ [ ])."""
    s = str(txt or "")
    for ch in ("\\", "`", "*", "_", "[", "]", "~"):
        s = s.replace(ch, "\\" + ch)
    return s


def _fuzzy(q: str, nombres) -> list:
    """[(score, nombre)] con coincidencias tolerantes a erratas (hashhut→Hash Hut)."""
    qn = _norm(q)
    if not qn:
        return []
    scored = []
    for n in nombres:
        nn = _norm(n)
        if not nn:
            continue
        if qn in nn or nn in qn:
            scored.append((1.0, n))
        else:
            r = difflib.SequenceMatcher(None, qn, nn).ratio()
            if r >= 0.62:
                scored.append((r, n))
    scored.sort(key=lambda x: -x[0])
    return scored


def _resultados_ordenados(hits):
    """Todos los resultados: más recientes (fecha) ordenados por mayor nota."""
    def fkey(c):
        return str(c.get("fecha") or "") or ""

    recientes = sorted(hits, key=fkey, reverse=True)
    recientes.sort(key=lambda c: -(nota_media(c)[0] or 0))
    return recientes


def _lista_resultados(hits, offset=0, limite=10):
    """Trozo de los resultados (recientes por votación) con fecha y granja en negrita."""
    def fkey(c):
        return str(c.get("fecha") or "") or ""

    lin = []
    for c in _resultados_ordenados(hits)[offset: offset + limite]:
        n, nv = nota_media(c)
        ext = f" — ⭐ {n:.1f}" if n is not None else ""
        fecha = fkey(c)[:7]
        nombre = _md(c.get("nombre", "?"))
        prod = _md(c.get("productor", "?"))
        lin.append(f"• *{nombre}*{ext} · *{prod}*"
                   f"{' · _' + fecha + '_' if fecha else ''}")
    return lin


def _kb_resultados(hits, offset=0, limite=10, consulta=""):
    """Teclado de resultados con paginación y menú."""
    fila = []
    if offset > 0:
        fila.append(_btn("◀ Antes", f"bmas:{max(0, offset - limite)}"))
    if offset + limite < len(hits):
        fila.append(_btn(f"▶ Más ({len(hits) - offset - limite})", f"bmas:{offset + limite}"))
    fila.append(_btn("◀ Menú", "menu"))
    return {"inline_keyboard": [fila]}


def _url_web_filtros(f, consulta="", cata_id=None):
    """Construye la URL de la web con los filtros como query params.

    app_streamlit.py lee ?tipo=&anio=&buscar=&cata= y abre el Catálogo ya
    filtrado (o la ficha del producto si llega ?cata=<id>).
    """
    params = {}
    if cata_id:
        params["cata"] = cata_id
    else:
        if f.get("tipo"):
            params["tipo"] = f["tipo"]
        if f.get("anio"):
            params["anio"] = f["anio"]
        if f.get("productor"):
            params["buscar"] = f["productor"]
        elif consulta:
            params["buscar"] = consulta
    qs = urllib.parse.urlencode(params)
    return APP_URL + ("?" + qs if qs else "")


def _kb_web(kb, url):
    """Añade al teclado un botón '🌐 Ver en la web' con el filtro aplicado."""
    if not url:
        return kb
    kb = dict(kb)
    kb["inline_keyboard"] = list(kb["inline_keyboard"]) + [
        [_btn("🌐 Ver en la web (filtrado)", url=url)]]
    return kb


def _kb_ficha(cata):
    """Teclado de ficha de cata: botón para ver fotos y votar en la web."""
    url = _url_web_filtros({}, cata_id=cata.get("id"))
    return {"inline_keyboard": [
        [_btn("🌐 Ver fotos y votar", url=url)],
        [_btn("◀ Menú", "menu")]]}


def _kb_prod_web(nombre, prod_id=None):
    """Teclado de ficha de productor: botón para verlo en la web."""
    url = _url_web_filtros({}, consulta=nombre)
    filas = [[_btn("🌐 Ver en la web", url=url)]]
    if prod_id is not None:
        filas.append([_btn("◀ Volver a productores", "prods:0")])
    return {"inline_keyboard": filas}


def _truncar(txt, limite=MAX_LEN):
    return txt if len(txt) <= limite else txt[: limite - 1] + "…"


# --------------------------------------------------------------------------
# API Telegram (requests)
# --------------------------------------------------------------------------
def _api(method, **params):
    for intento in range(3):
        try:
            r = requests.post(f"{API}/{method}", json=params, timeout=60)
            try:
                d = r.json()
            except ValueError:
                d = {}
            if d.get("ok"):
                return d.get("result")

            # Telegram limita temporalmente el ritmo: esperar su indicación
            # es mucho más fiable que reintentar inmediatamente.
            if r.status_code == 429 and intento < 2:
                espera = int(d.get("parameters", {}).get("retry_after", 2))
                print(f"[bot_terps] ⏳ {method}: límite; reintento en {espera}s")
                time.sleep(max(1, espera))
                continue
            if r.status_code >= 500 and intento < 2:
                time.sleep(2 * (intento + 1))
                continue
            print(f"[bot_terps] ⚠️ {method}: {d.get('description') or r.status_code}")
            return None
        except requests.RequestException as e:
            print(f"[bot_terps] ⚠️ {method} red: {e}")
            if intento < 2:
                time.sleep(2 * (intento + 1))
    return None


def _foto(chat_id, b64, caption=""):
    """Envía una foto decodificando el base64 guardado en la BD."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
        mime = "image/png" if b64.startswith(("iVBOR", "data:image/png")) else "image/jpeg"
        r = requests.post(
            f"{API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024],
                  "parse_mode": "Markdown"},
            files={"photo": ("foto.jpg", raw, mime)},
            timeout=90)
        return r.json().get("ok")
    except Exception as e:
        print(f"[bot_terps] ⚠️ foto: {e}")
        return None


def _album(chat_id, items):
    """Álbum de fotos (sendMediaGroup). items: [(b64, caption), ...] máx 10."""
    if not items:
        return None
    media = []
    files = {}
    for i, (b64, cap) in enumerate(items[:10]):
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        key = f"f{i}"
        files[key] = ("foto.jpg", raw, "image/jpeg")
        media.append({"type": "photo", "media": f"attach://{key}",
                      "caption": cap})
    if not media:
        return None
    try:
        r = requests.post(
            f"{API}/sendMediaGroup",
            data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False),
                  "parse_mode": "Markdown"},
            files=files, timeout=120)
        return r.json().get("ok")
    except Exception as e:
        print(f"[bot_terps] ⚠️ álbum: {e}")
        return None


# --------------------------------------------------------------------------
# Teclados (botoneras)
# --------------------------------------------------------------------------
def _btn(txt, cb=None, url=None, web_app=None):
    b = {"text": txt}
    if cb:
        b["callback_data"] = cb
    if url:
        b["url"] = url
    if web_app:
        b["web_app"] = {"url": web_app}
    return b


def kb_menu():
    return {"inline_keyboard": [
        [_btn("🌿 Abrir la app", web_app=APP_URL)],
        [_btn("🏭 Productores", "prods:0"), _btn("🔍 Buscar", "busq")],
        [_btn("🏆 Rankings", "rk"), _btn("📅 Por año/tipo", "filtro")],
        [_btn("📢 Novedades", "nov"), _btn("📊 Stats", "stats")],
        [_btn("❓ Ayuda", "help")],
    ]}


def kb_prods(offset=0, tam=8):
    prods = productores_ordenados()
    pag = prods[offset: offset + tam]
    filas = [[_btn(f"{p['nombre']}", f"prod:{p['id']}")] for p in pag]
    nav = []
    if offset > 0:
        nav.append(_btn("◀", f"prods:{max(0, offset - tam)}"))
    if offset + tam < len(prods):
        nav.append(_btn("▶", f"prods:{offset + tam}"))
    nav.append(_btn("◀ Menú", "menu"))
    filas.append(nav)
    return {"inline_keyboard": filas}


def kb_prod_volver(productor_id):
    return {"inline_keyboard": [[_btn("◀ Volver a productores", "prods:0")]]}


def kb_rk():
    return {"inline_keyboard": [
        [_btn("🏆 Top general", "rkg")],
        [_btn("🧊 Top por tipo", "rkt")],
        [_btn("◀ Menú", "menu")],
    ]}


def kb_filtro():
    d = cargar_datos()
    anos = sorted({c.get("anio") for c in d.get("catas", []) if c.get("anio")},
                  reverse=True)
    tipos = sorted({str(c.get("tipo", "")).strip()
                    for c in d.get("catas", []) if c.get("tipo")})
    filas = []
    if anos:
        filas.append([_btn(f"🗓️ {a}", f"ano:{a}") for a in anos[:4]])
    for t in tipos[:4]:
        filas.append([_btn(f"🧊 {t}", f"tipo:{t}")])
    filas.append([_btn("◀ Menú", "menu")])
    return {"inline_keyboard": filas}


def kb_help():
    return {"inline_keyboard": [[_btn("◀ Menú", "menu")]]}


# --------------------------------------------------------------------------
# Datos para menús
# --------------------------------------------------------------------------
def productores_ordenados():
    d = cargar_datos()
    out = []
    for p in d.get("productores", []):
        catas = [c for c in d.get("catas", [])
                 if str(c.get("productor", "")) == str(p.get("nombre", ""))]
        notas = [nota_media(c)[0] for c in catas]
        notas = [n for n in notas if n is not None]
        out.append({
            "id": p.get("id"), "nombre": p.get("nombre", ""),
            "pais": p.get("pais", ""), "foto_b64": p.get("foto_b64", ""),
            "n_catas": len(catas),
            "media": (sum(notas) / len(notas)) if notas else None})
    out.sort(key=lambda x: -(x["media"] or 0))
    return out


def catas_de_productor(nombre_prod):
    d = cargar_datos()
    return [c for c in d.get("catas", [])
            if str(c.get("productor", "")) == str(nombre_prod)]


# --------------------------------------------------------------------------
# Contenido de los mensajes
# --------------------------------------------------------------------------
def texto_menu():
    return ("🌿 *TerpsXHunter*\n"
            "_Bot de consultas · chat privado_ 🔒\n\n"
            "Todo se maneja con botones, sin comandos.\n"
            "Elige una opción 👇")


def texto_prod(p):
    catas = catas_de_productor(p["nombre"])
    media_txt = f" · ⭐ {p['media']:.1f}" if p["media"] is not None else ""
    lin = [f"🏭 *{_md(p['nombre'])}*"]
    if p.get("pais"):
        lin.append(f"🌍 {_md(p['pais'])}")
    lin.append(f"📦 {len(catas)} producto{'s' if len(catas) != 1 else ''}{media_txt}")
    lin.append("")
    for c in sorted(catas, key=lambda x: -(nota_media(x)[0] or 0)):
        n, nv = nota_media(c)
        ext = f" · {_fmt_nota(n, nv)}" if n is not None else ""
        lin.append(f"• {_md(c.get('nombre', '?'))}"
                   f"{' · ' + _md(str(c.get('tipo', '')).strip()) if c.get('tipo') else ''}"
                   f"{' · ' + str(c.get('anio', '')).strip() if c.get('anio') else ''}"
                   f"{ext}")
    return _truncar("\n".join(lin))


def texto_ficha(c):
    n, nv = nota_media(c)
    lin = [f"📦 *{_md(c.get('nombre', '?'))}*"]
    if c.get("productor"):
        lin.append(f"🏭 {_md(c.get('productor'))}")
    # Tipo · Año en una línea compacta
    datos = []
    if c.get("tipo"):
        datos.append(f"🧊 {_md(str(c.get('tipo', '')).strip())}")
    if c.get("anio"):
        datos.append(f"🗓️ {c.get('anio')}")
    if datos:
        lin.append(" · ".join(datos))
    if c.get("pais"):
        lin.append(f"🌍 {_md(c.get('pais'))}")
    # Nota destacada en su propia línea
    lin.append("")
    lin.append(_fmt_nota(n, nv))
    if c.get("comentarios"):
        com = str(c["comentarios"]).strip()
        lin.append("")
        lin.append("📝 " + _md(com[:280] + "…" if len(com) > 280 else com))
    return _truncar("\n".join(lin))


def texto_rkg():
    d = cargar_datos()
    rows = []
    for c in d.get("catas", []):
        n, nv = nota_media(c)
        if n is not None:
            rows.append((n, c.get("nombre", "?"), c.get("productor", "?"),
                         str(c.get("tipo", "")).strip(), nv))
    rows.sort(reverse=True)
    lin = ["🏆 *Top 10 general*", ""]
    for i, (n, nombre, prod, tipo, nv) in enumerate(rows[:10], 1):
        lin.append(f"{i}. {_md(nombre)} — ⭐ {n:.1f}"
                   f"{' · ' + _md(tipo) if tipo else ''} · {_md(prod)}")
    return _truncar("\n".join(lin))


def texto_rkt(tipo):
    d = cargar_datos()
    rows = []
    for c in d.get("catas", []):
        if str(c.get("tipo", "")).strip() != tipo:
            continue
        n, nv = nota_media(c)
        if n is not None:
            rows.append((n, c.get("nombre", "?"), c.get("productor", "?"), nv))
    if not rows:
        return f"🧊 *{tipo}* — sin votos todavía."
    rows.sort(reverse=True)
    lin = [f"🧊 *Top {tipo}*", ""]
    for i, (n, nombre, prod, nv) in enumerate(rows[:10], 1):
        lin.append(f"{i}. {nombre} — ⭐ {n:.1f} · {prod}")
    return _truncar("\n".join(lin))


def texto_ano(anio):
    d = cargar_datos()
    rows = []
    for c in d.get("catas", []):
        if str(c.get("anio", "")).strip() != str(anio).strip():
            continue
        n, nv = nota_media(c)
        rows.append((n, c.get("nombre", "?"), str(c.get("tipo", "")).strip(),
                     c.get("productor", "?")))
    rows.sort(key=lambda x: -(x[0] or 0))
    lin = [f"🗓️ *Catas de {anio}* ({len(rows)})", ""]
    for n, nombre, tipo, prod in rows[:12]:
        ext = f" — ⭐ {n:.1f}" if n is not None else ""
        lin.append(f"• {nombre}{' · ' + tipo if tipo else ''}{ext} · {prod}")
    return _truncar("\n".join(lin))


def texto_busqueda(consulta):
    d = cargar_datos()
    q = consulta.strip()
    qn = _norm(q)
    if not qn:
        return "🔍 Escribe un nombre a buscar."

    # 1) Coincidencias directas (substring normalizado)
    hits = []
    for c in d.get("catas", []):
        if (qn in _norm(c.get("nombre", ""))
                or qn in _norm(c.get("productor", ""))
                or qn in _norm(c.get("tipo", ""))):
            hits.append(c)

    if hits:
        lin = [f"🔍 {len(hits)} resultado{'s' if len(hits) != 1 else ''} "
               f"para «{q}»:", ""]
        lin += _lista_resultados(hits)
        if len(hits) > 10:
            lin.append("")
            lin.append("_No caben todas: te muestro las 10 más recientes "
                       "por votación._")
        return _truncar("\n".join(lin))

    # 2) Tolerante a erratas: sugiere el nombre más parecido
    nombres = ([str(c.get("nombre", "")) for c in d.get("catas", [])]
               + [str(p.get("nombre", "")) for p in d.get("productores", [])])
    sug = _fuzzy(q, nombres)
    if not sug:
        return f"🔍 Sin resultados para «{q}». Revisa la ortografía."
    mejor = sug[0][1]
    candidatos = [c for c in d.get("catas", [])
                  if _norm(c.get("nombre", "")) == _norm(mejor)
                  or _norm(c.get("productor", "")) == _norm(mejor)]
    if not candidatos:
        tops = {_norm(s[1]) for s in sug[:3]}
        candidatos = [c for c in d.get("catas", [])
                      if _norm(c.get("productor", "")) in tops
                      or _norm(c.get("nombre", "")) in tops]
    if not candidatos:
        return f"🔍 ¿Querías decir *{mejor}*? Todavía sin productos registrados."
    lin = [f"🔍 ¿Querías decir *{mejor}*?", ""]
    lin += _lista_resultados(candidatos)
    return _truncar("\n".join(lin))


def texto_ayuda():
    return ("❓ *Guía de uso*\n\n"
            "Todo se maneja con botones, sin comandos.\n\n"
            "🌿 **Abrir la app** — abre TerpsXHunter completa dentro de Telegram.\n"
            "🏭 **Productores** — elige uno y verás sus productos con nota media.\n"
            "🔍 **Buscar** — escribe el nombre (producto, productor o tipo). "
            "*Tolera erratas*: si te equivocas te sugiero lo que querías decir.\n"
            "🏆 **Rankings** — Top 10 general o por tipo.\n"
            "📅 **Por año/tipo** — filtra por campaña o por tipo.\n"
            "📢 **Novedades** — últimas catas subidas.\n"
            "📊 **Stats** — totales y mejor productor.\n\n"
            "▫️ *Las consultas son privadas.* En los canales el bot solo te "
            "redirige aquí.")


def texto_novedades_manual():
    d = cargar_datos()
    recientes = sorted(d.get("catas", []),
                       key=lambda c: str(c.get("fecha") or ""), reverse=True)[:10]
    lin = ["📢 *Últimas catas subidas*", ""]
    for c in recientes:
        n, _nv = nota_media(c)
        ext = f" — ⭐ {n:.1f}" if n is not None else ""
        lin.append(f"• {c.get('nombre', '?')}{ext} · {c.get('productor', '?')}")
    if not recientes:
        lin.append("Todavía no hay catas.")
    return _truncar("\n".join(lin))


def texto_stats():
    d = cargar_datos()
    catas = d.get("catas", [])
    prods = d.get("productores", [])
    notas = [nota_media(c)[0] for c in catas]
    notas = [n for n in notas if n is not None]
    media_global = (sum(notas) / len(notas)) if notas else 0.0
    pstats = []
    for p in prods:
        pc = [c for c in catas if str(c.get("productor", "")) == str(p.get("nombre", ""))]
        pn = [nota_media(c)[0] for c in pc]
        pn = [n for n in pn if n is not None]
        if pn:
            pstats.append((sum(pn) / len(pn), p.get("nombre", ""), len(pc)))
    top = max(pstats) if pstats else None
    lin = ["📊 *Estadísticas*", "",
           f"🏭 **Productores** · {len(prods)}",
           f"📦 **Catas** · {len(catas)}",
           f"⭐ **Nota media global** · {media_global:.1f}"]
    if top:
        lin.append(f"🏆 **Mejor productor** · {top[1]} ({top[0]:.1f} ⭐ · {top[2]} catas)")
    return _truncar("\n".join(lin))


# --------------------------------------------------------------------------
# Búsqueda asistida (botoneras en cascada: productor → tipo → año)
# --------------------------------------------------------------------------
def _texto_filtros_activos(f):
    paso = f.get("paso", "t")
    orden = {
        "t": ("Paso *1 de 3* · Elige 🧊 *tipo*", "Luego podrás acotar por año y por productor."),
        "a": ("Paso *2 de 3* · Elige 🗓️ *año*", "Ahora, si quieres, elige el productor."),
        "p": ("Paso *3 de 3* · Elige 🏭 *productor* (o cualquiera)", ""),
    }
    titulo, nota = orden.get(paso, ("🔍 *Búsqueda avanzada* 🔎", ""))
    lin = ["🔍 *Búsqueda avanzada* 🔎", "", titulo]
    if f.get("productor") or f.get("tipo") or f.get("anio"):
        lin.append("")
        lin.append("_Seleccionado:_")
        if f.get("tipo"):
            lin.append(f"🧊 *{_md(f['tipo'])}*")
        if f.get("anio"):
            lin.append(f"🗓️ *{_md(f['anio'])}*")
        if f.get("productor"):
            lin.append(f"🏭 *{_md(f['productor'])}*")
    if nota:
        lin.append("")
        lin.append("_" + nota + "_")
    return "\n".join(lin)


def _tipos_disponibles():
    d = cargar_datos()
    return sorted({str(c.get("tipo", "")).strip() for c in d.get("catas", []) if c.get("tipo")})


def _anos_disponibles():
    d = cargar_datos()
    return sorted({str(c.get("anio", "")).strip() for c in d.get("catas", []) if c.get("anio")}, reverse=True)


def _kb_paso_busq(paso, f, off_p=0):
    """Botonera del paso actual (productor/tipo/año) con saltos y cancelar."""
    kb = []
    if paso == "p":
        prods = productores_ordenados()
        pag = prods[off_p: off_p + 7]
        for p in pag:
            kb.append([_btn(f"🏭 {p['nombre']}", f"bf:p:{p['nombre']}")])
        nav = []
        if off_p > 0:
            nav.append(_btn("◀", f"bfpage:p:{max(0, off_p - 7)}"))
        if off_p + 7 < len(prods):
            nav.append(_btn("▶", f"bfpage:p:{off_p + 7}"))
        if nav:
            kb.append(nav)
        kb.append([_btn("🌿 Cualquier productor", "bf:p:TODOS")])
    elif paso == "t":
        for t in _tipos_disponibles():
            kb.append([_btn(f"🧊 {t}", f"bf:t:{t}")])
        kb.append([_btn("🌿 Cualquier tipo", "bf:t:TODOS")])
    else:  # año
        for a in _anos_disponibles():
            kb.append([_btn(f"🗓️ {a}", f"bf:a:{a}")])
        kb.append([_btn("🌿 Cualquier año", "bf:a:TODOS")])

    fila_fin = [_btn("✅ Ver resultados", "bfgo")]
    if paso != "t":  # el primer paso es tipo: no hay "Atrás" ahí
        fila_fin.append(_btn("↩️ Atrás", "bfback"))
    fila_fin.append(_btn("❌ Cancelar", "bfcancel"))
    kb.append(fila_fin)
    kb.append([_btn("✍️ Escribir búsqueda", "bfwrite")])
    return {"inline_keyboard": kb}


def _mostrar_paso_busq(paso, chat_id, msg_id=None, off_p=0):
    """Muestra el paso actual del asistente (edita el mensaje o responde nuevo)."""
    f = _filtro_busq.get(chat_id)
    if f is None:
        f = {"productor": None, "tipo": None, "anio": None}
        _filtro_busq[chat_id] = f
    f["paso"] = paso
    txt = _texto_filtros_activos(f)
    kb = _kb_paso_busq(paso, f, off_p)
    if msg_id is None:
        responder(chat_id, txt, kb=kb)
    else:
        editar(chat_id, msg_id, txt, kb)


def _filtrar_catas(f):
    d = cargar_datos()
    res = []
    for c in d.get("catas", []):
        if f.get("productor") and _norm(c.get("productor", "")) != _norm(f["productor"]):
            continue
        if f.get("tipo") and _norm(c.get("tipo", "")) != _norm(f["tipo"]):
            continue
        if f.get("anio") and str(c.get("anio", "")).strip() != str(f["anio"]).strip():
            continue
        res.append(c)
    return _resultados_ordenados(res)


def _mostrar_resultados_filtrados(chat_id, msg_id, f):
    res = _filtrar_catas(f)
    desc = _texto_filtros_activos(f).replace("Elige filtros a tu gusto para afinar la búsqueda.", "")
    if not res:
        editar(chat_id, msg_id, "🔍 *Sin catas* con esos filtros.\n\nPrueba a quitar alguno.",
               kb_paso_volver("p"))
        return
    cab = (f"🔍 *{len(res)} resultado{'s' if len(res) != 1 else ''}*"
           + (f" para {desc.split('_Seleccionado:_')[-1].strip()}" if "_Seleccionado:_" in desc else ""))
    # Álbum si hay fotos suficientes
    items = []
    for c in res[:10]:
        n, _nv = nota_media(c)
        ext = f"⭐ {n:.1f}" if n is not None else "sin votos"
        cap = (f"*{_md(c.get('nombre', '?'))}* — {ext} · *{_md(c.get('productor', '?'))}*"
               f"{' · ' + str(c.get('anio', '')).strip() if c.get('anio') else ''}")
        if c.get("foto_b64"):
            items.append((c["foto_b64"], cap))
    url = _url_web_filtros(f)
    if len(items) >= 2:
        _album(chat_id, items)
        if len(res) > 10:
            ab = f"Álbum con los 10 mejores ⭐ y {len(res) - 10} más:"
            editar(chat_id, msg_id, cab + " — " + ab,
                   kb=_kb_web(_kb_resultados(res, 10, 10), url))
        else:
            editar(chat_id, msg_id, cab + ":",
                   kb=_kb_web(_kb_resultados(res, 0, 10), url))
        return
    # Lista de texto paginada
    lin = [cab, ""]
    lin += _lista_resultados(res, 0, 10)
    editar(chat_id, msg_id, _truncar("\n".join(lin)),
           kb=_kb_web(_kb_resultados(res, 0, 10), url))


def kb_paso_volver(paso):
    return {"inline_keyboard": [[_btn("↩️ Seguir buscando", "busq")],
                                [_btn("◀ Menú", "menu")]]}


def _texto_escribir_con_filtros(chat_id):
    """Prompt de escritura manual avisando de los filtros ya elegidos."""
    f = _filtro_busq.get(chat_id, {})
    filtros = []
    if f.get("tipo"):
        filtros.append(f"🧊 {_md(f['tipo'])}")
    if f.get("anio"):
        filtros.append(f"🗓️ {_md(f['anio'])}")
    if f.get("productor"):
        filtros.append(f"🏭 {_md(f['productor'])}")
    extra = ("\n_Filtros activos: " + " · ".join(filtros) + "._") if filtros else ""
    return ("🔍 *Escribe el nombre* a buscar (producto, productor o tipo). "
            "Por ejemplo: _Hungover_ o _Frozen_.\n\n"
            "La búsqueda se hará respetando los filtros que ya elegiste." + extra)


# --------------------------------------------------------------------------
# Envío
# --------------------------------------------------------------------------
def responder(chat_id, texto, kb=None, foto_b64=None, caption=None):
    if not texto:
        return
    params = {"chat_id": chat_id, "text": texto,
              "parse_mode": "Markdown", "disable_web_page_preview": True}
    if kb:
        params["reply_markup"] = kb
    if foto_b64:
        _foto(chat_id, foto_b64, caption or texto)
    else:
        _api("sendMessage", **params)


def editar(chat_id, msg_id, texto, kb):
    _api("editMessageText",
         chat_id=chat_id, message_id=msg_id, text=texto,
         parse_mode="Markdown", reply_markup=kb)


def responder_busqueda(chat_id, consulta):
    """Busca: ficha si hay 1 claro, álbum de fotos si hay varios. Respeta filtros activos."""
    d = cargar_datos()
    qn = _norm(consulta)
    f = _filtro_busq.get(chat_id) or {}
    activos = [f[k] for k in ("tipo", "anio", "productor") if f.get(k)]
    pool = _filtrar_catas(f) if activos else d.get("catas", [])
    filtro_nota = ""
    if activos:
        etq = []
        if f.get("tipo"):
            etq.append(f"🧊 {_md(f['tipo'])}")
        if f.get("anio"):
            etq.append(f"🗓️ {_md(f['anio'])}")
        if f.get("productor"):
            etq.append(f"🏭 {_md(f['productor'])}")
        filtro_nota = " (filtros: " + " · ".join(etq) + ")"
    hits = [c for c in pool
            if qn and (qn in _norm(c.get("nombre", ""))
                       or qn in _norm(c.get("productor", "")))]
    if not hits:
        # ¿Sugerencia por erratas?
        nombres = ([str(c.get("nombre", "")) for c in d.get("catas", [])]
                   + [str(p.get("nombre", "")) for p in d.get("productores", [])])
        sug = _fuzzy(consulta, nombres)
        if sug:
            mejor = sug[0][1]
            kb = {"inline_keyboard": [
                [_btn(f"▶ Abrir {mejor}", f"pbn:{mejor}")],
                [_btn("◀ Menú", "menu")]]}
            responder(chat_id, texto_busqueda(consulta), kb=kb)
        else:
            responder(chat_id, texto_busqueda(consulta),
                      kb={"inline_keyboard": [[_btn("◀ Menú", "menu")]]})
        return

    _ultima_busqueda[chat_id] = consulta
    if len(hits) == 1:
        c = hits[0]
        responder(chat_id, texto_ficha(c), kb=_kb_ficha(c),
                  foto_b64=c.get("foto_b64", ""), caption=texto_ficha(c))
        return

    # Varios resultados: álbum con las fotos de los mejores
    top = _resultados_ordenados(hits)[:10]
    items = []
    for c in top:
        n, _nv = nota_media(c)
        ext = f"⭐ {n:.1f}" if n is not None else "sin votos"
        cap = (f"*{_md(c.get('nombre', '?'))}* — {ext} · *{_md(c.get('productor', '?'))}*"
               f"{' · ' + str(c.get('anio','')).strip() if c.get('anio') else ''}")
        if c.get("foto_b64"):
            items.append((c["foto_b64"], cap))
    cab = f"🔍 {len(hits)} resultado{'s' if len(hits) != 1 else ''} para «{consulta.strip()}»{filtro_nota}"
    url = _url_web_filtros(f, consulta)
    if len(items) >= 2:
        _album(chat_id, items)
        if len(hits) > 10:
            responder(chat_id, f"{cab}. Álbum con los 10 mejores ⭐ y {len(hits) - 10} más:",
                      kb=_kb_web(_kb_resultados(hits, 10, 10, consulta), url))
        else:
            responder(chat_id, cab + ":",
                      kb=_kb_web(_kb_resultados(hits, 0, 10, consulta), url))
        return

    # Sin fotos suficientes → lista de texto con paginación
    lin = [cab + ":", ""]
    lin += _lista_resultados(hits, 0, 10)
    responder(chat_id, _truncar("\n".join(lin)),
              kb=_kb_web(_kb_resultados(hits, 0, 10, consulta), url))


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def manejar_mensaje(msg):
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    texto = (msg.get("text") or "").strip()
    tipo = chat.get("type", "")

    if tipo != "private":
        # En grupos/foros solo un aviso breve (las consultas son en privado)
        if texto.startswith("/start"):
            responder(chat_id, "🤖 Escribe al bot en privado para consultar: "
                              f"t.me/@{_bot_username}")
        return

    if _espera_busqueda.pop(chat_id, False):
        if not texto:
            responder(chat_id, "Escribe el nombre a buscar.")
            return
        responder_busqueda(chat_id, texto)
        return

    if texto.startswith("/start") or texto in ("menu", "Menú", "menu"):
        responder(chat_id, texto_menu(), kb_menu())
    elif texto.startswith("/productor"):
        nombre = texto.replace("/productor", "").strip()
        prods = productores_ordenados()
        p = next((x for x in prods if nombre.lower() in x["nombre"].lower()), None)
        if p:
            responder(chat_id, texto_prod(p), _kb_prod_web(p["nombre"], p["id"]),
                      foto_b64=p.get("foto_b64"), caption=texto_prod(p))
        else:
            responder(chat_id, "🏭 No encontré ese productor. Usa el menú 🏭 Productores.")
    else:
        responder(chat_id, texto_menu(), kb_menu())


def manejar_callback(cb):
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    _api("answerCallbackQuery", callback_query_id=cb.get("id"))

    if data == "menu":
        editar(chat_id, msg_id, texto_menu(), kb_menu())
    elif data == "help":
        editar(chat_id, msg_id, texto_ayuda(), kb_help())
    elif data == "nov":
        editar(chat_id, msg_id, texto_novedades_manual(), kb_menu())
    elif data == "stats":
        editar(chat_id, msg_id, texto_stats(), kb_menu())
    elif data.startswith("bmas:"):
        offset = int(data.split(":")[1])
        consulta = _ultima_busqueda.get(chat_id, "")
        if consulta:
            d = cargar_datos()
            qn = _norm(consulta)
            hits = [c for c in d.get("catas", [])
                    if qn and (qn in _norm(c.get("nombre", ""))
                               or qn in _norm(c.get("productor", "")))]
            cab = f"🔍 {len(hits)} resultados para «{consulta.strip()}»:"
        elif _filtro_busq.get(chat_id) and any(
                _filtro_busq[chat_id].get(k) for k in ("productor", "tipo", "anio")):
            hits = _filtrar_catas(_filtro_busq[chat_id])
            cab = f"🔍 {len(hits)} resultados (filtros elegidos):"
        else:
            hits = []
            cab = ""
        if not hits:
            editar(chat_id, msg_id, "🔍 La búsqueda ya no está disponible.",
                   kb_menu())
            return
        lin = [cab, ""]
        lin += _lista_resultados(hits, offset, 10)
        editar(chat_id, msg_id, _truncar("\n".join(lin)),
               _kb_resultados(hits, offset, 10, consulta))
    elif data.startswith("pbn:"):
        nombre = data.split(":", 1)[1]
        prods = productores_ordenados()
        p = next((x for x in prods if _norm(x["nombre"]) == _norm(nombre)), None)
        if not p:
            p = next((x for x in prods if _norm(nombre) in _norm(x["nombre"])), None)
        if p:
            editar(chat_id, msg_id, texto_prod(p), _kb_prod_web(p["nombre"], p["id"]))
            if p.get("foto_b64"):
                _foto(chat_id, p["foto_b64"], caption=texto_prod(p))
        else:
            editar(chat_id, msg_id, "🏭 No encontré ese productor.", kb_menu())
    elif data == "busq":
        _filtro_busq[chat_id] = {"productor": None, "tipo": None, "anio": None, "paso": "t"}
        _mostrar_paso_busq("t", chat_id, msg_id)
    elif data.startswith("bf:t:"):
        valor = data.split(":", 2)[2]
        f = _filtro_busq.setdefault(chat_id, {"productor": None, "tipo": None, "anio": None})
        f["tipo"] = None if valor == "TODOS" else valor
        _mostrar_paso_busq("a", chat_id, msg_id)
    elif data.startswith("bf:a:"):
        valor = data.split(":", 2)[2]
        f = _filtro_busq.setdefault(chat_id, {"productor": None, "tipo": None, "anio": None})
        f["anio"] = None if valor == "TODOS" else valor
        _mostrar_paso_busq("p", chat_id, msg_id)
    elif data.startswith("bf:p:"):
        valor = data.split(":", 2)[2]
        f = _filtro_busq.setdefault(chat_id, {"productor": None, "tipo": None, "anio": None})
        f["productor"] = None if valor == "TODOS" else valor
        _mostrar_resultados_filtrados(chat_id, msg_id, f)
    elif data.startswith("bfpage:"):
        _, paso, off = data.split(":")
        _mostrar_paso_busq(paso, chat_id, msg_id, off_p=int(off))
    elif data == "bfgo":
        f = _filtro_busq.get(chat_id, {"productor": None, "tipo": None, "anio": None})
        _mostrar_resultados_filtrados(chat_id, msg_id, f)
    elif data == "bfback":
        f = _filtro_busq.get(chat_id, {"productor": None, "tipo": None, "anio": None})
        paso = f.get("paso", "t")
        if paso == "a":
            _mostrar_paso_busq("t", chat_id, msg_id)
        elif paso == "p":
            _mostrar_paso_busq("a", chat_id, msg_id)
        else:
            editar(chat_id, msg_id, texto_menu(), kb_menu())
    elif data == "bfwrite":
        _espera_busqueda[chat_id] = True
        kb = {"inline_keyboard": [[_btn("↩️ Volver a filtros", "bfresume")],
                                  [_btn("◀ Menú", "menu")]]}
        editar(chat_id, msg_id, _texto_escribir_con_filtros(chat_id), kb)
    elif data == "bfresume":
        f = _filtro_busq.get(chat_id) or {}
        _mostrar_paso_busq(f.get("paso", "t"), chat_id, msg_id)
    elif data == "bfcancel":
        _filtro_busq.pop(chat_id, None)
        editar(chat_id, msg_id, texto_menu(), kb_menu())
    elif data.startswith("prods:"):
        offset = int(data.split(":")[1])
        editar(chat_id, msg_id, texto_menu(), kb_prods(offset))
    elif data.startswith("prod:"):
        pid = data.split(":")[1]
        p = next((x for x in productores_ordenados() if str(x["id"]) == str(pid)), None)
        if p:
            editar(chat_id, msg_id, texto_prod(p), _kb_prod_web(p["nombre"], pid))
            if p.get("foto_b64"):
                _foto(chat_id, p["foto_b64"], caption=texto_prod(p))
    elif data == "rk":
        editar(chat_id, msg_id, "🏆 *Rankings*", kb_rk())
    elif data == "rkg":
        editar(chat_id, msg_id, texto_rkg(), kb_rk())
    elif data == "rkt":
        d = cargar_datos()
        tipos = sorted({str(c.get("tipo", "")).strip()
                        for c in d.get("catas", []) if c.get("tipo")})
        kb = {"inline_keyboard": [
            [_btn(f"🧊 {t}", f"rkt:{t}")] for t in tipos[:8]] +
            [[_btn("◀ Volver", "rk")]]}
        editar(chat_id, msg_id, "Elige un tipo:", kb)
    elif data.startswith("rkt:"):
        tipo = data.split(":", 1)[1]
        editar(chat_id, msg_id, texto_rkt(tipo), kb_rk())
    elif data == "filtro":
        editar(chat_id, msg_id, "📅 *Filtra por año o tipo:*", kb_filtro())
    elif data.startswith("ano:"):
        anio = data.split(":", 1)[1]
        editar(chat_id, msg_id, texto_ano(anio), kb_filtro())
    elif data.startswith("tipo:"):
        tipo = data.split(":", 1)[1]
        editar(chat_id, msg_id, texto_rkt(tipo), kb_filtro())


# --------------------------------------------------------------------------
# Monitor de novedades (publica en la sección "Novedades" cuando se suben cosas)
# --------------------------------------------------------------------------
def _texto_novedades(nuevas_catas, nuevos_productores):
    lin = ["📢 *Novedades en TerpsXHunter*", ""]
    if nuevas_catas:
        lin.append(f"Se han subido {len(nuevas_catas)} "
                   f"cata{'s' if len(nuevas_catas) != 1 else ''} "
                   f"nueva{'s' if len(nuevas_catas) != 1 else ''}:")
        lin.append("")
        for c in nuevas_catas[:10]:
            n, _nv = nota_media(c)
            ext = f" — ⭐ {n:.1f}" if n is not None else ""
            lin.append(f"• {c.get('nombre', '?')}{ext} · {c.get('productor', '?')}")
    if nuevos_productores:
        lin.append("")
        lin.append(f"{len(nuevos_productores)} "
                   f"productor{'es' if len(nuevos_productores) != 1 else ''} "
                   f"nuevo{'s' if len(nuevos_productores) != 1 else ''}:")
        lin.append("")
        for p in nuevos_productores[:5]:
            lin.append(f"• {p}")
    lin.append("")
    lin.append("Pruébalos y vota \U0001F33F")
    return _truncar("\n".join(lin))


def _chequear_novedades():
    """Compara con el snapshot; si hay novedades, publica en la sección."""
    cfg = _leer_config()
    canal = cfg.get("canal_novedades", "")
    if not canal:
        return  # sección aún sin configurar
    d = cargar_datos()
    snap = _leer_snapshot()
    conocidas = set(snap.get("catas", []))
    conocidos_prod = set(snap.get("productores", []))
    nuevas_catas = [c for c in d.get("catas", [])
                    if c.get("id") and str(c["id"]) not in conocidas]
    nuevos_prod = [p.get("nombre") for p in d.get("productores", [])
                   if p.get("id") and str(p["id"]) not in conocidos_prod]
    _guardar_snapshot({
        "catas": sorted(str(c.get("id")) for c in d.get("catas", []) if c.get("id")),
        "productores": sorted(str(p.get("id"))
                              for p in d.get("productores", []) if p.get("id")),
    })
    if not nuevas_catas and not nuevos_prod:
        return
    nuevas_catas.sort(key=lambda c: str(c.get("fecha") or ""), reverse=True)
    texto = _texto_novedades(nuevas_catas, nuevos_prod)
    r = _api("sendMessage", chat_id=canal, text=texto, parse_mode="Markdown")
    print(f"[bot_terps] 📢 Novedades publicadas ({len(nuevas_catas)} catas, "
          f"{len(nuevos_prod)} productores): {bool(r)}")


def _hilo_novedades():
    """Primer arranque: solo inicializa el snapshot (sin notificar)."""
    d = cargar_datos()
    _guardar_snapshot({
        "catas": sorted(str(c.get("id")) for c in d.get("catas", []) if c.get("id")),
        "productores": sorted(str(p.get("id"))
                              for p in d.get("productores", []) if p.get("id")),
    })
    cfg = _leer_config()
    intervalo = max(5, int(cfg.get("intervalo_min", 15))) * 60
    while True:
        time.sleep(intervalo)
        try:
            _chequear_novedades()
        except Exception as e:
            print(f"[bot_terps] ⚠️ novedades: {e}")


# --------------------------------------------------------------------------
# Polling
# --------------------------------------------------------------------------
def main():
    global _bot_username
    me = _api("getMe")
    _bot_username = (me or {}).get("username", "TerpsXHunterAppBot")
    print(f"[bot_terps] 🤖 @{_bot_username} activo — consultas cargadas")
    cargar_datos()
    print(f"[bot_terps] 📊 {len(_datos.get('productores', []))} productores · "
          f"{len(_datos.get('catas', []))} catas")

    threading.Thread(target=_hilo_novedades, daemon=True).start()

    estado = _leer_estado()
    offset = int(estado.get("update_offset", 0) or 0)
    while True:
        try:
            r = requests.post(f"{API}/getUpdates",
                              json={"offset": offset, "timeout": 50,
                                    "allowed_updates": ["message", "callback_query"]},
                              timeout=65)
            data = r.json()
            if not data.get("ok"):
                print(f"[bot_terps] ⚠️ getUpdates: {data.get('description')}")
                time.sleep(3)
                continue
            for upd in data.get("result", []):
                siguiente_offset = upd["update_id"] + 1
                if "message" in upd:
                    manejar_mensaje(upd["message"])
                elif "callback_query" in upd:
                    manejar_callback(upd["callback_query"])
                # Se guarda tras procesar: si el equipo se apaga antes, como
                # mucho se repetirá una respuesta, nunca se perderá un mensaje.
                offset = siguiente_offset
                _guardar_estado({"update_offset": offset})
        except requests.RequestException as e:
            print(f"[bot_terps] ⚠️ red: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"[bot_terps] ⚠️ error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
