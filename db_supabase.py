# -*- coding: utf-8 -*-
"""
===============================================================================
 db_supabase.py — Capa de persistencia en Supabase (Postgres) para Catas
===============================================================================
 Permite que el Sistema de Catas funcione en Streamlit Community Cloud (donde
 el filesystem es efímero y catas.json NO persiste). La interfaz es idéntica
 a la capa JSON del core:

     db_supabase.activo()        -> bool
     db_supabase.cargar_datos()  -> dict {perfiles, productores, catas}
     db_supabase.guardar_datos(datos) -> None

 ACTIVACIÓN:
   SUPABASE_URL = connection string completa de Project Settings → Database
   → Connection string → URI (ej. postgresql://postgres.<ref>:<pass>@...:5432/postgres)

   Se lee de st.secrets["supabase"]["url"] o de la variable de entorno
   SUPABASE_URL (Streamlit Community Cloud permite ambas).

 Si no hay configuración, activo() = False y la app usa catas.json (local).

 La conexión usa psycopg2 con SSL. En la nube se instala con requirements.txt.
===============================================================================
"""

import os
import json

# Último error de conexión/lectura (para mostrarlo en la UI de diagnóstico)
_ULTIMO_ERROR = ""

try:  # st.secrets solo existe dentro de una app Streamlit
    import streamlit as _st
except Exception:  # pragma: no cover
    _st = None


# -----------------------------------------------------------------------------
# Configuración
# -----------------------------------------------------------------------------

def _secrets_supabase() -> dict:
    """Lee el bloque [supabase] de st.secrets (o {}) sin crashear.
    IMPORTANTE: se usa el acceso por índice s['supabase'] (demostrado que
    funciona en Streamlit Cloud); .get() puede fallar silenciosamente y
    devolver vacío, dejando la app en modo local."""
    if _st is None:
        return {}
    try:
        s = getattr(_st, "secrets", None)
        if s is None:
            return {}
        val = None
        try:
            val = dict(s["supabase"])  # dict() explícito: en Cloud el bloque
        except Exception:             # es un objeto tipo-dict, no un dict real
            try:
                val = dict(s.get("supabase"))
            except Exception:
                val = None
        return val if isinstance(val, dict) and val else {}
    except Exception:
        return {}


def _config() -> str:
    """Devuelve la connection string (URL) desde secrets o variable de entorno."""
    cfg = _secrets_supabase()
    url = cfg.get("url") if isinstance(cfg, dict) else None
    return (url or os.environ.get("SUPABASE_URL", "")).strip()


def _conectar():
    """Crea la conexión psycopg2 (import perezoso). None si no es posible."""
    url = _config()
    if not url:
        return None
    try:
        import psycopg2
        if "sslmode" not in url:
            url = url + ("&" if "?" in url else "?") + "sslmode=require"
        return psycopg2.connect(url)
    except Exception as e:
        print(f"[db_supabase] ⚠️ Error de conexión a Supabase: {e}")
        global _ULTIMO_ERROR
        _ULTIMO_ERROR = f"Error de conexión: {e}"
        return None


def activo() -> bool:
    """True si hay SUPABASE_URL configurada (la conexión se prueba al usar)."""
    return bool(_config())


def _filtrar_nulos(d: dict) -> dict:
    """Quita claves con None (Postgres devuelve None; el JSON no usaba None)."""
    return {k: v for k, v in d.items() if v is not None}


# -----------------------------------------------------------------------------
# Lectura
# -----------------------------------------------------------------------------

def cargar_datos() -> dict:
    """Reconstruye {perfiles, productores, catas} desde Postgres.
    Ante cualquier error de conexión/lectura, lo registra en los logs y
    devuelve estructura vacía (la app no crashea; el log dice la causa)."""
    conn = _conectar()
    if conn is None:
        print("[db_supabase] ⚠️ Sin conexión: devolviendo datos vacíos")
        return {"perfiles": [], "productores": [], "catas": []}
    try:
        return _leer_desde(conn)
    except Exception as e:
        print(f"[db_supabase] ⚠️ Error leyendo datos: {e}")
        global _ULTIMO_ERROR
        _ULTIMO_ERROR = f"Error leyendo datos: {e}"
        return {"perfiles": [], "productores": [], "catas": []}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _leer_desde(conn) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT id, nombre, password_hash, es_confianza, es_admin "
                "FROM perfiles")
    perfiles = [_filtrar_nulos(dict(zip(
        ["id", "nombre", "password_hash", "es_confianza", "es_admin"], row)))
        for row in cur.fetchall()]

    cur.execute("SELECT id, nombre, foto, pais, foto_b64 FROM productores")
    productores = []
    for row in cur.fetchall():
        p = dict(zip(["id", "nombre", "foto", "pais", "foto_b64"], row))
        if not p.get("foto_b64"):
            p.pop("foto_b64", None)
        productores.append(_filtrar_nulos(p))

    cur.execute("SELECT id, fecha, nombre, productor, tipo, comentarios, "
                "pais, foto, anio, temporada, foto_b64 FROM catas")
    catas = []
    for row in cur.fetchall():
        c = dict(zip(["id", "fecha", "nombre", "productor", "tipo",
                      "comentarios", "pais", "foto", "anio", "temporada",
                      "foto_b64"], row))
        if not c.get("foto_b64"):
            c.pop("foto_b64", None)
        c["votos"] = []
        c["comentarios_usuarios"] = []
        catas.append(_filtrar_nulos(c))

    cur.execute("SELECT cata_id, perfil_id, fecha, puntuaciones_detalle, "
                "notas_bloques, nota_final FROM votos")
    por_cata = {c["id"]: c for c in catas}
    for cata_id, perfil_id, fecha, detalle, notas, final in cur.fetchall():
        cata = por_cata.get(cata_id)
        if cata is None:
            continue
        voto = {"perfil_id": perfil_id, "fecha": fecha or ""}
        if isinstance(detalle, dict) and detalle:
            voto["puntuaciones_detalle"] = detalle
        if isinstance(notas, dict) and notas:
            voto["notas_bloques"] = notas
        voto["nota_final"] = float(final) if final is not None else 0.0
        cata["votos"].append(voto)

    cur.execute("SELECT cata_id, perfil_id, nombre, fecha, texto "
                "FROM comentarios_usuarios ORDER BY id")
    for cata_id, perfil_id, nombre, fecha, texto in cur.fetchall():
        cata = por_cata.get(cata_id)
        if cata is None:
            continue
        cata["comentarios_usuarios"].append({
            "perfil_id": perfil_id, "nombre": nombre or "",
            "fecha": fecha or "", "texto": texto or ""})

    # ---- Asociaciones / Coffeeshops ----
    cur.execute("SELECT id, nombre FROM paises ORDER BY nombre")
    paises = [_filtrar_nulos(dict(zip(["id", "nombre"], row)))
              for row in cur.fetchall()]

    cur.execute("SELECT id, nombre, pais_id FROM ciudades ORDER BY nombre")
    ciudades = [_filtrar_nulos(dict(zip(["id", "nombre", "pais_id"], row)))
                for row in cur.fetchall()]

    cur.execute("SELECT id, nombre, pais_id, ciudad_id, direccion, biografia, "
                "creado, foto_b64 FROM coffeeshops ORDER BY nombre")
    coffeeshops = []
    cs_por_id = {}
    for row in cur.fetchall():
        cs = dict(zip(["id", "nombre", "pais_id", "ciudad_id", "direccion",
                       "biografia", "creado", "foto_b64"], row))
        if not cs.get("foto_b64"):
            cs.pop("foto_b64", None)
        cs["votos"] = []
        cs["productores"] = []
        coffeeshops.append(_filtrar_nulos(cs))
        cs_por_id[cs["id"]] = cs

    cur.execute("SELECT coffeeshop_id, perfil_id, fecha, nota, comentario "
                "FROM votos_coffeeshops")
    for cs_id, perfil_id, fecha, nota, comentario in cur.fetchall():
        cs = cs_por_id.get(cs_id)
        if cs is None:
            continue
        cs["votos"].append({
            "perfil_id": perfil_id, "fecha": fecha or "",
            "nota": float(nota) if nota is not None else 0.0,
            "comentario": comentario or ""})

    cur.execute("SELECT coffeeshop_id, productor_id FROM coffeeshop_productores")
    for cs_id, productor_id in cur.fetchall():
        cs = cs_por_id.get(cs_id)
        if cs is not None:
            cs["productores"].append(productor_id)

    # ---- Descartes "No lo probé" ----
    cur.execute("SELECT cata_id, perfil_id, fecha FROM descartes_usuarios")
    descartes = [dict(zip(["cata_id", "perfil_id", "fecha"], row))
                 for row in cur.fetchall()]

    cur.close()
    return {"perfiles": perfiles, "productores": productores, "catas": catas,
            "paises": paises, "ciudades": ciudades, "coffeeshops": coffeeshops,
            "descartes": descartes}


# -----------------------------------------------------------------------------
# Escritura (re-sync total: simple y correcto para volúmenes pequeños)
# -----------------------------------------------------------------------------

def guardar_datos(datos: dict) -> None:
    """Persiste la estructura completa en Postgres (transacción atómica)."""
    conn = _conectar()
    if conn is None:
        raise RuntimeError("Supabase no configurado (url/key) o psycopg2 ausente")
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM votos")
        cur.execute("DELETE FROM comentarios_usuarios")
        cur.execute("DELETE FROM votos_coffeeshops")
        cur.execute("DELETE FROM coffeeshop_productores")
        cur.execute("DELETE FROM descartes_usuarios")
        # Re-sync total de entidades: se borran y reinsertan TODAS, de modo
        # que las eliminadas del dict también desaparezcan de la BD (el
        # upsert por sí solo no borra). Orden: hijos ya borrados arriba.
        cur.execute("DELETE FROM coffeeshops")
        cur.execute("DELETE FROM ciudades")
        cur.execute("DELETE FROM paises")
        cur.execute("DELETE FROM catas")
        cur.execute("DELETE FROM productores")
        cur.execute("DELETE FROM perfiles")

        for p in datos.get("perfiles", []):
            if not isinstance(p, dict) or not p.get("id"):
                continue
            cur.execute(
                "INSERT INTO perfiles (id, nombre, password_hash, es_confianza, es_admin) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "password_hash = EXCLUDED.password_hash, "
                "es_confianza = EXCLUDED.es_confianza, "
                "es_admin = EXCLUDED.es_admin",
                (p.get("id"), p.get("nombre", ""), p.get("password_hash", ""),
                 bool(p.get("es_confianza")), bool(p.get("es_admin"))))

        for pr in datos.get("productores", []):
            if not isinstance(pr, dict) or not pr.get("id"):
                continue
            cur.execute(
                "INSERT INTO productores (id, nombre, foto, pais, foto_b64) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "foto = EXCLUDED.foto, pais = EXCLUDED.pais, "
                "foto_b64 = EXCLUDED.foto_b64",
                (pr.get("id"), pr.get("nombre", ""), pr.get("foto", ""),
                 pr.get("pais", ""), pr.get("foto_b64", "")))

        for c in datos.get("catas", []):
            if not isinstance(c, dict) or not c.get("id"):
                continue
            cur.execute(
                "INSERT INTO catas (id, fecha, nombre, productor, tipo, "
                "comentarios, pais, foto, anio, temporada, foto_b64) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET fecha = EXCLUDED.fecha, "
                "nombre = EXCLUDED.nombre, productor = EXCLUDED.productor, "
                "tipo = EXCLUDED.tipo, comentarios = EXCLUDED.comentarios, "
                "pais = EXCLUDED.pais, foto = EXCLUDED.foto, "
                "anio = EXCLUDED.anio, temporada = EXCLUDED.temporada, "
                "foto_b64 = EXCLUDED.foto_b64",
                (c.get("id"), c.get("fecha", ""), c.get("nombre", ""),
                 c.get("productor", ""), c.get("tipo", ""),
                 c.get("comentarios", ""), c.get("pais", ""),
                 c.get("foto", ""), c.get("anio", ""), c.get("temporada", ""),
                 c.get("foto_b64", "")))

            for v in c.get("votos", []):
                if not isinstance(v, dict) or not v.get("perfil_id"):
                    continue
                cur.execute(
                    "INSERT INTO votos (cata_id, perfil_id, fecha, "
                    "puntuaciones_detalle, notas_bloques, nota_final) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (c["id"], v.get("perfil_id"), v.get("fecha", ""),
                     json.dumps(v.get("puntuaciones_detalle", {}), ensure_ascii=False),
                     json.dumps(v.get("notas_bloques", {}), ensure_ascii=False),
                     float(v.get("nota_final", 0.0))))

            for cm in c.get("comentarios_usuarios", []):
                if not isinstance(cm, dict) or not cm.get("perfil_id"):
                    continue
                cur.execute(
                    "INSERT INTO comentarios_usuarios "
                    "(cata_id, perfil_id, nombre, fecha, texto) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (c["id"], cm.get("perfil_id"), cm.get("nombre", ""),
                     cm.get("fecha", ""), cm.get("texto", "")))

        # ---- Asociaciones / Coffeeshops ----
        for p in datos.get("paises", []):
            if not isinstance(p, dict) or not p.get("id"):
                continue
            cur.execute(
                "INSERT INTO paises (id, nombre) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre",
                (p.get("id"), p.get("nombre", "")))

        for ci in datos.get("ciudades", []):
            if not isinstance(ci, dict) or not ci.get("id"):
                continue
            cur.execute(
                "INSERT INTO ciudades (id, nombre, pais_id) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "pais_id = EXCLUDED.pais_id",
                (ci.get("id"), ci.get("nombre", ""),
                 ci.get("pais_id") or None))

        for cs in datos.get("coffeeshops", []):
            if not isinstance(cs, dict) or not cs.get("id"):
                continue
            cur.execute(
                "INSERT INTO coffeeshops (id, nombre, pais_id, ciudad_id, "
                "direccion, biografia, creado, foto_b64) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "pais_id = EXCLUDED.pais_id, ciudad_id = EXCLUDED.ciudad_id, "
                "direccion = EXCLUDED.direccion, biografia = EXCLUDED.biografia, "
                "creado = EXCLUDED.creado, foto_b64 = EXCLUDED.foto_b64",
                (cs.get("id"), cs.get("nombre", ""), cs.get("pais_id") or None,
                 cs.get("ciudad_id") or None, cs.get("direccion", ""),
                 cs.get("biografia", ""), cs.get("creado", ""),
                 cs.get("foto_b64", "")))

            for v in cs.get("votos", []):
                if not isinstance(v, dict) or not v.get("perfil_id"):
                    continue
                cur.execute(
                    "INSERT INTO votos_coffeeshops "
                    "(coffeeshop_id, perfil_id, fecha, nota, comentario) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (cs["id"], v.get("perfil_id"), v.get("fecha", ""),
                     float(v.get("nota", 0.0)), v.get("comentario", "")))

            for pr_id in cs.get("productores", []):
                if not pr_id:
                    continue
                cur.execute(
                    "INSERT INTO coffeeshop_productores "
                    "(coffeeshop_id, productor_id) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (cs["id"], pr_id))

        # ---- Descartes "No lo probé" ----
        for d in datos.get("descartes", []):
            if not isinstance(d, dict) or not d.get("cata_id") \
                    or not d.get("perfil_id"):
                continue
            cur.execute(
                "INSERT INTO descartes_usuarios "
                "(cata_id, perfil_id, fecha) VALUES (%s, %s, %s) "
                "ON CONFLICT (cata_id, perfil_id) DO UPDATE SET fecha = EXCLUDED.fecha",
                (d.get("cata_id"), d.get("perfil_id"), d.get("fecha", "")))

        conn.commit()
        cur.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
