# -*- coding: utf-8 -*-
"""
===============================================================================
 app_datos.py — Backend puro del Sistema de Catas (SIN interfaz gráfica)
===============================================================================
 Separado de app_catas.py para poder importarse en entornos sin tkinter
 (Streamlit Community Cloud, headless). Contiene la capa de datos, la
 normalización y toda la matemática de notas. La GUI de escritorio vive en
 app_catas.py, que hace `from app_datos import *`.

 Hook de avisos: _avisar_error(msg) — por defecto imprime; la web lo
 reemplaza en runtime por st.error(...).
===============================================================================
"""

def _avisar_error(mensaje: str) -> None:
    """Aviso de error sin dependencias de UI (la web lo parchea)."""
    print("[aviso]", mensaje)

"""
===============================================================================
 SISTEMA DE CATAS — REGISTRO Y RANKINGS  ·  v2.0 (Multi-Voto)
===============================================================================
 Aplicación de escritorio premium (CustomTkinter, deep dark + acentos
 dorados/verdes) con sistema MULTI-VOTANTE:

   - Cada persona crea su PERFIL (solo nombre) y puntúa las muestras.
   - Cada cata acumula VOTOS (uno por perfil); la nota de la cata es la MEDIA
     de sus votos -> resultado más fiable.
   - Top GENERAL (media de todos) y Top PERSONAL (por perfil), en secciones
     distintas. Catálogo de PRODUCTORES con foto, renombrado y edición.

 Arquitectura en 3 capas:
   CAPA 1 — DATOS (backend puro): persistencia tolerante + migración v1->v2,
            normalización, coerción numérica y matemática de ponderación.
   CAPA 2 — VISTAS (frontend): VistaFormulario, VistaProductores,
            VistaRankings, VistaPerfiles + modales (detalle, productor, voto).
   CAPA 3 — ORQUESTACIÓN: AppCatas (sidebar, navegación, mediador).

 Ponderaciones (especificación original):
   Visual 25% (Resinoso 40 · Limpieza 35 · Curado 25)
   Aroma  15% (Intensidad 30 · Cuerpo 25 · Limpieza 25 · Curado olor 20)
   Sabor  45% (Perfil 25 · Cantidad 25 · Limpieza boca 20 · Cuerpo 15 · Curado boca 15)
   Efectos 15% (Overall 50 · Potencia 30 · Duración 20)
   Nota Final = Visual*0.25 + Aroma*0.15 + Sabor*0.45 + Efectos*0.15
   (cálculo interno 1-10; mostrado y almacenado sobre 100)

 Datos: C:/Users/Yunes/Desktop/Catas/catas.json   Fotos: ./imagenes/
 Ejecutar:  python app_catas.py
===============================================================================
"""

import json
import os
import time
import hmac
import hashlib
import shutil
from datetime import datetime


# =============================================================================
# 1. CONFIGURACIÓN GLOBAL
# =============================================================================

RUTA_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(RUTA_DIR, "catas.json")
RUTA_IMAGENES = os.path.join(RUTA_DIR, "imagenes")

# --- Paleta premium ----------------------------------------------------------
COLOR_FONDO      = "#0D0F12"
COLOR_SIDEBAR    = "#12151A"
COLOR_TARJETA    = "#161A20"
COLOR_TARJETA_HV = "#1B212B"
COLOR_BORDE      = "#232A34"
COLOR_BORDE_CHIP = "#2A3240"
COLOR_TEXTO      = "#E8E6E1"
COLOR_TEXTO_2    = "#9AA0A6"
COLOR_DORADO     = "#D4A373"
COLOR_DORADO_L   = "#E6C07A"
COLOR_VERDE      = "#2E7D32"
COLOR_VERDE_L    = "#81C784"
COLOR_ACTIVO     = "#1B5E20"
COLOR_PELIGRO    = "#A03434"

TIPOS_VALIDOS = ["Flor", "Dry", "Static", "Frozen", "Fresh Frozen", "WPFF",
                 "Rosin", "BHO", "Live Resin"]
PAISES_VALIDOS = ["España", "Marruecos", "USA", "Tailandia"]
TEMPORADAS_VALIDAS = ["S1", "S2", "S3"]  # tiradas del año (p. ej. cada ~4 meses)

PESOS_GLOBALES = {"visual": 0.25, "aroma": 0.15, "sabor": 0.45, "efectos": 0.15}
PESOS_INTERNOS = {
    "visual":   {"resinoso": 0.40, "limpieza": 0.35, "curado_aspecto": 0.25},
    "aroma":    {"intensidad": 0.30, "cuerpo": 0.25, "limpieza": 0.25, "curado_olor": 0.20},
    "sabor":    {"perfil_terpenos": 0.25, "cantidad_terpenos": 0.25,
                 "limpieza_boca": 0.20, "cuerpo": 0.15, "curado_boca": 0.15},
    "efectos":  {"sensacion_overall": 0.50, "potencia": 0.30, "duracion": 0.20},
}
BLOQUES = [
    {"clave": "visual", "titulo": "ASPECTO VISUAL", "peso": 0.25,
     "color": "#C49A4A", "texto_color": COLOR_DORADO_L,
     "subs": [("resinoso", "Resinoso"), ("limpieza", "Limpieza"),
              ("curado_aspecto", "Punto de curado en aspecto")]},
    {"clave": "aroma", "titulo": "AROMA", "peso": 0.15,
     "color": "#7E57C2", "texto_color": "#B39DDB",
     "subs": [("intensidad", "Intensidad de terpenos"), ("cuerpo", "Cuerpo / Sensación dura"),
              ("limpieza", "Limpieza"), ("curado_olor", "Punto de curado en olor")]},
    {"clave": "sabor", "titulo": "SABOR", "peso": 0.45,
     "color": COLOR_VERDE, "texto_color": COLOR_VERDE_L,
     "subs": [("perfil_terpenos", "Perfil de terpenos"), ("cantidad_terpenos", "Cantidad de terpenos"),
              ("limpieza_boca", "Limpieza en boca"), ("cuerpo", "Cuerpo / Sensación al fumar"),
              ("curado_boca", "Punto de curado en boca")]},
    {"clave": "efectos", "titulo": "EFECTOS", "peso": 0.15,
     "color": "#D84315", "texto_color": "#FF8A65",
     "subs": [("sensacion_overall", "Sensación Overall"), ("potencia", "Potencia"),
              ("duracion", "Duración")]},
]
COLOR_TIPO = {
    "Flor": "#3fb96a", "Dry": "#d9a13c", "Static": "#3cc8c8", "Frozen": "#4a90d9",
    "Fresh Frozen": "#4fc3f7", "WPFF": "#b39ddb", "Rosin": "#e8c35a",
    "BHO": "#e0803c", "Live Resin": "#a06ee8",
}
COLOR_PAIS = {"España": "#e8a33d", "Marruecos": "#5fc48e", "USA": "#6fa8dc", "Tailandia": "#e8765e"}
COLOR_ANIO = "#3b5b8c"
COLOR_TEMPORADA = {"S1": "#3fb96a", "S2": "#6fa8dc", "S3": "#e8a33d"}
MAP_PAIS = {"EEUU": "USA", "Estados Unidos": "USA", "United States": "USA", "Espana": "España"}
COLORES_PERFIL = ["#3fb96a", "#e8c35a", "#6fa8dc", "#c07ae8", "#e8765e", "#3cc8c8", "#e8a33d", "#8fdc4f"]


def anios_produccion(max_anio=None) -> list:
    """Años de producción disponibles (descendente, año actual+1 -> 2000)."""
    actual = max_anio or datetime.now().year
    return [str(a) for a in range(actual + 1, 1999, -1)]

# -----------------------------------------------------------------------------
# Helpers de presentación
# -----------------------------------------------------------------------------

def color_nota(nota: float) -> str:
    """Color semántico según la nota (escala 0-10)."""
    if nota >= 8.5:
        return "#34d17b"
    if nota >= 7.0:
        return "#8fdc4f"
    if nota >= 5.5:
        return "#e8c35a"
    if nota >= 4.0:
        return "#e88a4a"
    return "#e05c5c"


def _flotante(valor) -> float:
    """Coerción numérica A PRUEBA DE FALLOS (None/'abc'/NaN/Inf -> 0.0)."""
    try:
        v = float(valor)
        return v if v == v and abs(v) != float("inf") else 0.0
    except (TypeError, ValueError):
        return 0.0


def resolver_ruta_foto(foto: str):
    if not foto:
        return None
    ruta = foto if os.path.isabs(foto) else os.path.join(RUTA_DIR, foto)
    return ruta if os.path.exists(ruta) else None


# 2. CAPA DE DATOS (backend puro)
# =============================================================================

def estructura_vacia() -> dict:
    """Estructura canónica del JSON (v6: + identidades_oauth para Google/OTP)."""
    return {"perfiles": [], "productores": [], "catas": [],
            "paises": [], "ciudades": [], "coffeeshops": [], "descartes": [],
            "identidades": []}


def _db_nube():
    """Devuelve el módulo db_supabase si está disponible (nunca crashea)."""
    try:
        import db_supabase as db
        return db if db.activo() else None
    except Exception:
        return None


def _normalizar_estructura(datos):
    """Normaliza cualquier estructura (v1/v2/v3 o leída de Postgres)."""
    if isinstance(datos, list):          # formato v1 (hasta v1.0)
        datos = _migrar_v1(datos)
        _asegurar_productores(datos)
        return datos
    if isinstance(datos, dict):          # formato v2/v3/v4/v5
        datos.setdefault("perfiles", [])
        datos.setdefault("catas", [])
        datos.setdefault("paises", [])
        datos.setdefault("ciudades", [])
        datos.setdefault("coffeeshops", [])
        datos.setdefault("descartes", [])
        datos.setdefault("identidades", [])
        datos["perfiles"] = [p for p in datos["perfiles"] if isinstance(p, dict)]
        for p in datos["perfiles"]:
            p.setdefault("es_confianza", False)  # rango 'gente de confianza'
        datos["catas"] = [c for c in (normalizar_cata(c) for c in datos["catas"])
                          if c is not None]
        datos["paises"] = [p for p in datos["paises"] if isinstance(p, dict)]
        datos["ciudades"] = [c for c in datos["ciudades"] if isinstance(c, dict)]
        datos["coffeeshops"] = [c for c in
                                (normalizar_coffeeshop(c)
                                 for c in datos["coffeeshops"])
                                if c is not None]
        # Descartes "No lo probé": solo dicts con cata y perfil válidos
        datos["descartes"] = [d for d in datos["descartes"]
                              if isinstance(d, dict) and d.get("cata_id")
                              and d.get("perfil_id")]
        for d in datos["descartes"]:
            d.setdefault("fecha", "")
        # Identidades OAuth (Google): solo dicts con proveedor+sub+perfil
        datos["identidades"] = [i for i in datos["identidades"]
                                if isinstance(i, dict) and i.get("proveedor")
                                and i.get("sub") and i.get("perfil_id")]
        for i in datos["identidades"]:
            i.setdefault("email", "")
        _asegurar_productores(datos)
        return datos
    return estructura_vacia()


def cargar_datos() -> dict:
    """
    Carga los datos con tolerancia total y MIGRACIÓN automática v1 -> v2.
    - En modo NUBE (Supabase configurado): lee de Postgres.
    - En modo local: lee catas.json.
    """
    db = _db_nube()
    if db is not None:
        return _normalizar_estructura(db.cargar_datos())
    if not os.path.exists(DB_FILE):
        return estructura_vacia()
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            datos = json.load(f)
    except (json.JSONDecodeError, OSError):
        _avisar_error(
            "Error de datos",
            f"No se pudo leer {os.path.basename(DB_FILE)} (¿corrupto?).\n"
            "Se iniciará con datos vacíos.",
        )
        return estructura_vacia()
    return _normalizar_estructura(datos)


def _migrar_v1(lista: list) -> dict:
    """Convierte la lista plana v1 en la estructura v2 (perfil por defecto)."""
    datos = estructura_vacia()
    datos["perfiles"].append({"id": "p_default", "nombre": "Yunes"})
    for r in lista:
        if not isinstance(r, dict):
            continue
        # Leer las puntuaciones ANTES de normalizar (normalizar las elimina)
        detalle = r.get("puntuaciones_detalle")
        cata = normalizar_cata(r)
        if isinstance(detalle, dict) and detalle:
            notas, final = _recalcular_notas_escala100(detalle)
            cata["votos"] = [{
                "perfil_id": "p_default",
                "fecha": r.get("fecha", ""),
                "puntuaciones_detalle": detalle,
                "notas_bloques": notas,
                "nota_final": final,
            }]
        datos["catas"].append(cata)
    return datos


def _asegurar_productores(datos: dict) -> None:
    """
    Garantiza la lista de productores (entidad v3). Migra el antiguo dict
    'fotos_productores' (nombre -> ruta) y crea entidades para todos los
    productores citados en las catas, de modo que el selector del formulario
    siempre los tenga disponibles.
    """
    fotos_viejas = datos.pop("fotos_productores", {})
    if not isinstance(fotos_viejas, dict):
        fotos_viejas = {}

    por_nombre = {}
    ids_usados = set()
    for p in datos.get("productores", []):
        if isinstance(p, dict) and p.get("nombre"):
            p.setdefault("foto", "")
            por_nombre[str(p["nombre"]).strip()] = p
            ids_usados.add(p.get("id", ""))

    # Migrar el dict viejo: foto por nombre de productor
    for nombre, foto in fotos_viejas.items():
        ent = por_nombre.setdefault(nombre, {
            "id": generar_id(ids_usados, prefijo="pr_"),
            "nombre": nombre, "foto": ""})
        if not ent.get("foto") and isinstance(foto, str):
            ent["foto"] = foto
        ids_usados.add(ent.get("id", ""))

    # Productores citados en catas que aún no existen como entidad
    for c in datos.get("catas", []):
        nombre = str(c.get("productor", "")).strip()
        if not nombre or nombre in por_nombre:
            continue
        ent = {"id": generar_id(ids_usados, prefijo="pr_"),
               "nombre": nombre, "foto": ""}
        por_nombre[nombre] = ent
        ids_usados.add(ent["id"])

    datos["productores"] = list(por_nombre.values())
    for p in datos["productores"]:
        p.setdefault("pais", "")  # país de origen del productor (etiqueta)


def normalizar_cata(r) -> dict:
    """Normaliza una cata (v1 o v2): campos por defecto, migraciones, votos."""
    if not isinstance(r, dict):
        return None
    if r.get("tipo") == "Fresh Frozen / WPFF":
        r["tipo"] = "Fresh Frozen"
    pais = r.get("pais", "")
    if pais in MAP_PAIS:
        r["pais"] = MAP_PAIS[pais]

    r.setdefault("id", "")
    r.setdefault("fecha", "")
    r.setdefault("nombre", "Sin nombre")
    r.setdefault("productor", "")
    r.setdefault("pais", "")
    r.setdefault("anio", "")
    r.setdefault("temporada", "")
    r.setdefault("tipo", TIPOS_VALIDOS[0])
    r.setdefault("comentarios", "")
    r.setdefault("foto", "")
    r.setdefault("votos", [])
    r.setdefault("comentarios_usuarios", [])  # reviews de usuarios de confianza

    # Limpieza de votos: solo dicts válidos
    votos_limpios = []
    for v in r.get("votos", []):
        if not isinstance(v, dict) or not v.get("perfil_id"):
            continue
        v.setdefault("fecha", "")
        v.setdefault("puntuaciones_detalle", {})
        v.setdefault("notas_bloques", {})
        v.setdefault("nota_final", 0.0)
        det = v.get("puntuaciones_detalle")
        if isinstance(det, dict) and det:
            v["notas_bloques"], v["nota_final"] = _recalcular_notas_escala100(det)
        else:
            v["nota_final"] = round(_flotante(v.get("nota_final")) * 10, 2)
        votos_limpios.append(v)
    r["votos"] = votos_limpios

    # Campos v1 sobrantes (ya migrados a votos)
    r.pop("puntuaciones_detalle", None)
    r.pop("notas_bloques", None)
    r.pop("nota_final", None)
    return r


def guardar_datos(datos: dict) -> None:
    """Persiste la estructura v2/v3. En modo NUBE -> Supabase; si no, catas.json."""
    db = _db_nube()
    if db is not None:
        db.guardar_datos(datos)
        return
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)


def _recalcular_notas_escala100(detalle: dict) -> tuple:
    """Notas de bloque y final (sobre 100) desde puntuaciones de detalle (1-10)."""
    notas, final = calcular_notas(detalle)
    return {b: round(n * 10, 2) for b, n in notas.items()}, round(final * 10, 2)


def generar_id(ids_existentes, prefijo: str = "") -> str:
    """ID único: <prefijo><YYYYMMDDHHMMSS>; sufijo si colisiona en el mismo segundo."""
    base = prefijo + datetime.now().strftime("%Y%m%d%H%M%S")
    candidato, n = base, 2
    while candidato in ids_existentes:
        candidato = f"{base}-{n}"
        n += 1
    return candidato

# -----------------------------------------------------------------------------
# Lógica de puntuación (matemática pura)
# -----------------------------------------------------------------------------

def calcular_nota_bloque(scores: dict, bloque: str) -> float:
    pesos = PESOS_INTERNOS[bloque]
    return round(sum(_flotante(scores.get(sub)) * peso for sub, peso in pesos.items()), 2)


def calcular_nota_final(notas_bloques: dict) -> float:
    return round(sum(_flotante(notas_bloques.get(b)) * p for b, p in PESOS_GLOBALES.items()), 2)


def calcular_notas(score_dict: dict) -> tuple:
    notas = {b: calcular_nota_bloque(score_dict.get(b, {}), b) for b in PESOS_GLOBALES}
    return notas, calcular_nota_final(notas)

# -----------------------------------------------------------------------------
# Lógica multi-voto
# -----------------------------------------------------------------------------

def votos_validos(cata: dict) -> list:
    """Votos con puntuaciones reales (un voto por perfil, tras normalizar)."""
    return [v for v in cata.get("votos", [])
            if isinstance(v, dict) and v.get("puntuaciones_detalle")]


def nota_media(cata: dict) -> float:
    """Nota de la cata = media de las notas finales de sus votos (0.0 sin votos)."""
    votos = votos_validos(cata)
    if not votos:
        return 0.0
    return round(sum(_flotante(v.get("nota_final")) for v in votos) / len(votos), 2)


def ids_profesionales(datos: dict) -> set:
    """IDs de perfiles 'profesionales': gente de confianza + admins.
    Sus votos alimentan la nota profesional (evaluación objetiva)."""
    ids = set()
    for p in datos.get("perfiles", []):
        if p.get("es_confianza") or p.get("es_admin") or p.get("id") == "p_default":
            ids.add(p.get("id", ""))
    return ids


def nota_media_profesional(cata: dict, datos: dict):
    """Nota media SOLO de votos de profesionales (confianza + admin).
    None si no hay ninguno: la UI oculta la valoración profesional."""
    ids = ids_profesionales(datos)
    notas = [_flotante(v.get("nota_final"))
             for v in votos_validos(cata) if v.get("perfil_id") in ids]
    if not notas:
        return None
    return round(sum(notas) / len(notas), 1)


def n_votos_profesionales(cata: dict, datos: dict) -> int:
    """Nº de votos de profesionales (confianza + admin)."""
    ids = ids_profesionales(datos)
    return sum(1 for v in votos_validos(cata) if v.get("perfil_id") in ids)


def nota_media_bloques(cata: dict) -> dict:
    """Media por bloque de todos los votos de la cata (sobre 100)."""
    votos = votos_validos(cata)
    if not votos:
        return {b: 0.0 for b in PESOS_GLOBALES}
    return {b: round(sum(_flotante(v.get("notas_bloques", {}).get(b)) for v in votos) / len(votos), 1)
            for b in PESOS_GLOBALES}


def voto_de_perfil(cata: dict, perfil_id: str):
    """Devuelve el voto de un perfil en la cata, o None."""
    for v in cata.get("votos", []):
        if isinstance(v, dict) and v.get("perfil_id") == perfil_id:
            return v
    return None


def upsert_voto(cata: dict, perfil_id: str, score_dict: dict) -> str:
    """
    Añade o actualiza el voto del perfil en la cata (un voto por perfil).
    Devuelve 'nuevo' o 'actualizado'.
    """
    notas10, final10 = calcular_notas(score_dict)
    voto = {
        "perfil_id": perfil_id,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "puntuaciones_detalle": score_dict,
        "notas_bloques": {b: round(n * 10, 2) for b, n in notas10.items()},
        "nota_final": round(final10 * 10, 2),
    }
    votos = cata.setdefault("votos", [])
    for i, v in enumerate(votos):
        if isinstance(v, dict) and v.get("perfil_id") == perfil_id:
            votos[i] = voto
            return "actualizado"
    votos.append(voto)
    return "nuevo"


def quitar_voto(cata: dict, perfil_id: str) -> bool:
    """Elimina el voto de un perfil; True si existía."""
    votos = cata.get("votos", [])
    resto = [v for v in votos if not (isinstance(v, dict) and v.get("perfil_id") == perfil_id)]
    if len(resto) == len(votos):
        return False
    cata["votos"] = resto
    return True


# =============================================================================
# 2b. ASOCIACIONES / COFFEESHOPS (backend puro)
# =============================================================================

def normalizar_coffeeshop(r) -> dict:
    """Normaliza un coffeeshop: campos por defecto, votos y productores."""
    if not isinstance(r, dict):
        return None
    r.setdefault("id", "")
    r.setdefault("nombre", "Sin nombre")
    r.setdefault("pais_id", "")
    r.setdefault("ciudad_id", "")
    r.setdefault("direccion", "")
    r.setdefault("biografia", "")
    r.setdefault("creado", "")
    r.setdefault("foto_b64", "")
    r.setdefault("votos", [])
    r.setdefault("productores", [])
    # Limpieza de votos: solo dicts válidos con perfil_id
    r["votos"] = [v for v in r.get("votos", [])
                  if isinstance(v, dict) and v.get("perfil_id")]
    r["productores"] = [p for p in r.get("productores", []) if p]
    return r


def votos_coffeeshop_validos(cs: dict) -> list:
    """Votos válidos (dicts con perfil_id y nota numérica)."""
    out = []
    for v in cs.get("votos", []):
        if isinstance(v, dict) and v.get("perfil_id"):
            try:
                nota = float(v.get("nota", 0.0))
            except (TypeError, ValueError):
                nota = 0.0
            v = dict(v)
            v["nota"] = nota
            out.append(v)
    return out


def nota_media_coffeeshop(cs: dict) -> float:
    """Media de los votos del local (0.0 si no hay)."""
    votos = votos_coffeeshop_validos(cs)
    if not votos:
        return 0.0
    return round(sum(v["nota"] for v in votos) / len(votos), 1)


def voto_coffeeshop_de_perfil(cs: dict, perfil_id: str):
    """Voto de un perfil en el local (None si no votó)."""
    for v in votos_coffeeshop_validos(cs):
        if v.get("perfil_id") == perfil_id:
            return v
    return None


def upsert_voto_coffeeshop(cs: dict, perfil_id: str, nota: float,
                           comentario: str = "") -> str:
    """Añade/actualiza la valoración de un perfil al local (nota 0-10)."""
    try:
        nota = max(0.0, min(10.0, float(nota)))
    except (TypeError, ValueError):
        nota = 0.0
    voto = {"perfil_id": perfil_id,
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "nota": round(nota, 1), "comentario": comentario or ""}
    votos = cs.setdefault("votos", [])
    for i, v in enumerate(votos):
        if isinstance(v, dict) and v.get("perfil_id") == perfil_id:
            voto["fecha"] = v.get("fecha") or voto["fecha"]
            votos[i] = voto
            return "actualizado"
    votos.append(voto)
    return "nuevo"


def quitar_voto_coffeeshop(cs: dict, perfil_id: str) -> bool:
    """Elimina la valoración de un perfil; True si existía."""
    votos = cs.get("votos", [])
    resto = [v for v in votos
             if not (isinstance(v, dict) and v.get("perfil_id") == perfil_id)]
    if len(resto) == len(votos):
        return False
    cs["votos"] = resto
    return True


def upsert_pais(datos: dict, nombre: str) -> str:
    """Crea un país (si no existe por nombre) y devuelve su id."""
    nombre = str(nombre).strip()
    if not nombre:
        return ""
    for p in datos.setdefault("paises", []):
        if not isinstance(p, dict):  # defensa: datos anómalos en la BD
            continue
        if str(p.get("nombre", "")).strip().lower() == nombre.lower():
            return p["id"]
    pid = generar_id({p.get("id", "") for p in datos["paises"]
                      if isinstance(p, dict)}, prefijo="pais_")
    datos["paises"].append({"id": pid, "nombre": nombre})
    return pid


def upsert_ciudad(datos: dict, nombre: str, pais_id: str) -> str:
    """Crea una ciudad (si no existe por nombre+pais) y devuelve su id."""
    nombre = str(nombre).strip()
    if not nombre or not pais_id:
        return ""
    for c in datos.setdefault("ciudades", []):
        if not isinstance(c, dict):  # defensa: datos anómalos
            continue
        if (str(c.get("nombre", "")).strip().lower() == nombre.lower()
                and c.get("pais_id") == pais_id):
            return c["id"]
    cid = generar_id({c.get("id", "") for c in datos["ciudades"]
                      if isinstance(c, dict)}, prefijo="ciud_")
    datos["ciudades"].append({"id": cid, "nombre": nombre, "pais_id": pais_id})
    return cid


def upsert_coffeeshop(datos: dict, nombre: str, pais_id: str = "",
                      ciudad_id: str = "", direccion: str = "",
                      biografia: str = "", foto_b64: str = "") -> str:
    """Crea un coffeeshop (si no existe por nombre) y devuelve su id."""
    nombre = str(nombre).strip()
    if not nombre:
        return ""
    for cs in datos.setdefault("coffeeshops", []):
        if str(cs.get("nombre", "")).strip().lower() == nombre.lower():
            cs["pais_id"] = pais_id or cs.get("pais_id", "")
            cs["ciudad_id"] = ciudad_id or cs.get("ciudad_id", "")
            cs["direccion"] = direccion or cs.get("direccion", "")
            cs["biografia"] = biografia or cs.get("biografia", "")
            if foto_b64:
                cs["foto_b64"] = foto_b64
            return cs["id"]
    cid = generar_id({c.get("id", "") for c in datos["coffeeshops"]},
                     prefijo="cs_")
    datos["coffeeshops"].append({
        "id": cid, "nombre": nombre, "pais_id": pais_id,
        "ciudad_id": ciudad_id, "direccion": direccion,
        "biografia": biografia,
        "creado": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "foto_b64": foto_b64,
        "votos": [], "productores": []})
    return cid


def vincular_productor_cs(cs: dict, productor_id: str) -> bool:
    """Vincula un productor al menú del local; False si ya estaba."""
    if not productor_id:
        return False
    lista = cs.setdefault("productores", [])
    if productor_id in lista:
        return False
    lista.append(productor_id)
    return True


def desvincular_productor_cs(cs: dict, productor_id: str) -> bool:
    """Desvincula un productor del local; True si estaba."""
    lista = cs.get("productores", [])
    if productor_id not in lista:
        return False
    cs["productores"] = [p for p in lista if p != productor_id]
    return True


def productores_de_coffeeshop(datos: dict, cs: dict) -> list:
    """Entidades productor vinculadas al local (en orden de nombre)."""
    ids = set(cs.get("productores", []))
    return sorted([p for p in datos.get("productores", [])
                   if p.get("id") in ids], key=lambda p: p.get("nombre", ""))


def ciudad_por_id(datos: dict, ciudad_id: str) -> dict:
    for c in datos.get("ciudades", []):
        if c.get("id") == ciudad_id:
            return c
    return {}


def pais_por_id(datos: dict, pais_id: str) -> dict:
    for p in datos.get("paises", []):
        if p.get("id") == pais_id:
            return p
    return {}


def catas_de_productor(datos: dict, productor_nombre: str) -> list:
    """Catas del catálogo de un productor (para el menú del local)."""
    nombre = str(productor_nombre or "").strip()
    return sorted([c for c in datos.get("catas", [])
                   if str(c.get("productor", "")).strip().lower() == nombre.lower()],
                  key=lambda c: -nota_media(c))


def eliminar_pais(datos: dict, pais_id: str) -> int:
    """Elimina un país y en cascada sus ciudades y coffeeshops.
    Devuelve cuántos coffeeshops se eliminaron con él."""
    if not pais_id:
        return 0
    datos["paises"] = [p for p in datos.get("paises", [])
                       if p.get("id") != pais_id]
    datos["ciudades"] = [c for c in datos.get("ciudades", [])
                         if c.get("pais_id") != pais_id]
    cs_ids = {c["id"] for c in datos.get("coffeeshops", [])
              if c.get("pais_id") == pais_id}
    datos["coffeeshops"] = [c for c in datos.get("coffeeshops", [])
                            if c["id"] not in cs_ids]
    return len(cs_ids)


def eliminar_ciudad(datos: dict, ciudad_id: str) -> int:
    """Elimina una ciudad; los coffeeshops de esa ciudad se quedan sin
    ciudad asignada (conservan el país). Devuelve nº de afectados."""
    if not ciudad_id:
        return 0
    datos["ciudades"] = [c for c in datos.get("ciudades", [])
                         if c.get("id") != ciudad_id]
    afectados = 0
    for c in datos.get("coffeeshops", []):
        if c.get("ciudad_id") == ciudad_id:
            c["ciudad_id"] = ""
            afectados += 1
    return afectados


def eliminar_coffeeshop(datos: dict, cs_id: str) -> bool:
    """Elimina un coffeeshop (sus votos y vínculos caen en cascada)."""
    if not cs_id:
        return False
    n = len(datos.get("coffeeshops", []))
    datos["coffeeshops"] = [c for c in datos.get("coffeeshops", [])
                            if c.get("id") != cs_id]
    return len(datos["coffeeshops"]) < n


# =============================================================================
# 2c. DESCARTES ("No lo probé") + CADUCIDAD VISUAL (30 días)
# =============================================================================

def _parse_fecha(texto) -> datetime:
    """Parsea 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' / ISO; None si no se puede."""
    if not texto:
        return None
    texto = str(texto).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(texto[:19], fmt)
        except ValueError:
            continue
    return None


def dias_edad(cata: dict):
    """Días desde el alta de la cata (campo 'fecha' = fecha de creación).
    None si la fecha no se puede determinar (la cata no caduca nunca)."""
    dt = _parse_fecha(cata.get("fecha"))
    if dt is None:
        return None
    return (datetime.now() - dt).days


def es_reciente(cata: dict, dias: int = 30) -> bool:
    """True si la cata tiene <= 'dias' de vida (o no se puede determinar)."""
    edad = dias_edad(cata)
    return True if edad is None else edad <= dias


def descartado_por(datos: dict, cata_id: str, perfil_id: str) -> bool:
    """¿El perfil descartó esta cata ('No lo probé')?"""
    if not cata_id or not perfil_id:
        return False
    return any(d.get("cata_id") == cata_id and d.get("perfil_id") == perfil_id
               for d in datos.get("descartes", []))


def descartar_cata(datos: dict, cata_id: str, perfil_id: str) -> str:
    """Marca la cata como 'no probada' por el perfil.
    Devuelve 'nuevo' (se añadió) o 'ya' (ya estaba descartada)."""
    if not cata_id or not perfil_id:
        return "invalido"
    descartes = datos.setdefault("descartes", [])
    for d in descartes:
        if d.get("cata_id") == cata_id and d.get("perfil_id") == perfil_id:
            return "ya"
    descartes.append({"cata_id": cata_id, "perfil_id": perfil_id,
                      "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")})
    return "nuevo"


def quitar_descarte(datos: dict, cata_id: str, perfil_id: str) -> bool:
    """Elimina el descarte de la cata; True si existía."""
    descartes = datos.get("descartes", [])
    resto = [d for d in descartes
             if not (d.get("cata_id") == cata_id
                     and d.get("perfil_id") == perfil_id)]
    if len(resto) == len(descartes):
        return False
    datos["descartes"] = resto
    return True


def ids_descartados_por(datos: dict, perfil_id: str) -> set:
    """IDs de catas descartadas por un perfil."""
    if not perfil_id:
        return set()
    return {d.get("cata_id") for d in datos.get("descartes", [])
            if d.get("perfil_id") == perfil_id}


# =============================================================================
# 2d. IDENTIDADES OAuth (Google) — vínculo proveedor↔perfil
# =============================================================================

def perfil_por_identidad(datos: dict, proveedor: str, sub: str):
    """Perfil vinculado a una identidad OAuth (proveedor+sub); None si no hay."""
    if not proveedor or not sub:
        return None
    for i in datos.get("identidades", []):
        if i.get("proveedor") == proveedor and i.get("sub") == sub:
            return next((p for p in datos.get("perfiles", [])
                         if p.get("id") == i.get("perfil_id")), None)
    return None


def perfil_por_email(datos: dict, email: str):
    """Perfil vinculado a un email OAuth (comparación case-insensitive)."""
    if not email:
        return None
    e = str(email).strip().lower()
    for i in datos.get("identidades", []):
        if str(i.get("email", "")).strip().lower() == e:
            return next((p for p in datos.get("perfiles", [])
                         if p.get("id") == i.get("perfil_id")), None)
    return None


def vincular_identidad(datos: dict, proveedor: str, sub: str, email: str,
                       perfil_id: str) -> str:
    """Vincula una identidad OAuth a un perfil. Devuelve 'nuevo' o 'ya'."""
    if not proveedor or not sub or not perfil_id:
        return "invalido"
    identidades = datos.setdefault("identidades", [])
    for i in identidades:
        if i.get("proveedor") == proveedor and i.get("sub") == sub:
            if email and not i.get("email"):
                i["email"] = email
            return "ya"
    identidades.append({"proveedor": proveedor, "sub": sub,
                        "email": email or "", "perfil_id": perfil_id})
    return "nuevo"


# =============================================================================
# 2e. TOKENS DE SESIÓN (cookie "recordar sesión") — firma HMAC-SHA256
# =============================================================================

def crear_token_sesion(perfil_id: str, secreto: str, dias: int = 30) -> str:
    """Token firmado: <perfil_id>.<expiry_unix>.<hmac_sha256>.
    Sin secreto o sin perfil devuelve '' (la cookie no se emite)."""
    if not secreto or not perfil_id:
        return ""
    exp = int(time.time()) + dias * 86400
    payload = f"{perfil_id}.{exp}"
    firma = hmac.new(secreto.encode("utf-8"), payload.encode("utf-8"),
                     hashlib.sha256).hexdigest()
    return f"{payload}.{firma}"


def verificar_token_sesion(token: str, secreto: str):
    """Devuelve el perfil_id si la firma es válida y no ha expirado; None si no."""
    if not token or not secreto:
        return None
    partes = str(token).split(".")
    if len(partes) != 3:
        return None
    perfil_id, exp_txt, firma = partes
    esperado = hmac.new(secreto.encode("utf-8"),
                        f"{perfil_id}.{exp_txt}".encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, firma):
        return None
    try:
        if int(exp_txt) < time.time():
            return None
    except ValueError:
        return None
    return perfil_id

# =============================================================================
# 3. CAPA DE VISTAS (frontend)
# =============================================================================

