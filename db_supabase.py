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
import hashlib
import threading

# Último error de conexión/lectura (para mostrarlo en la UI de diagnóstico)
_ULTIMO_ERROR = ""

# Pool de conexiones persistente: el handshake SSL contra el pooler se paga
# UNA vez por conexión (ahorro ~0.3-0.5 s por operación frente a abrir y
# cerrar en cada cargar()/guardar()).
_POOL = None
_POOL_LOCK = threading.Lock()

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
    """Conexión del pool persistente (thread-safe). Reconecta si está rota."""
    global _POOL
    url = _config()
    if not url:
        return None
    try:
        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool
        if "sslmode" not in url:
            url = url + ("&" if "?" in url else "?") + "sslmode=require"
    except Exception as e:
        global _ULTIMO_ERROR
        _ULTIMO_ERROR = f"Error de conexión: {e}"
        print(f"[db_supabase] ⚠️ {e}")
        return None

    def _crear_pool():
        global _POOL
        _POOL = ThreadedConnectionPool(1, 4, url)

    with _POOL_LOCK:
        if _POOL is None:
            try:
                _crear_pool()
            except Exception as e:
                _ULTIMO_ERROR = f"Error de conexión: {e}"
                print(f"[db_supabase] ⚠️ Error de conexión a Supabase: {e}")
                return None
        try:
            conn = _POOL.getconn()
            cur = conn.cursor()
            cur.execute("SELECT 1")  # validación: detecta conexiones muertas
            cur.close()
            return conn
        except Exception:
            # Conexión caída (p. ej. timeout del pooler): descartarla y recrear
            try:
                _POOL.putconn(conn, close=True)
            except Exception:
                pass
            try:
                _POOL.closeall()
            except Exception:
                pass
            _POOL = None
            try:
                _crear_pool()
                return _POOL.getconn()
            except Exception as e:
                _ULTIMO_ERROR = f"Error de conexión: {e}"
                print(f"[db_supabase] ⚠️ Error de conexión a Supabase: {e}")
                return None


def _devolver(conn):
    """Devuelve la conexión al pool (putconn hace rollback implícito)."""
    if conn is None:
        return
    try:
        if _POOL is not None:
            _POOL.putconn(conn)
        else:
            conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


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
        _devolver(conn)


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

    # ---- Identidades OAuth (Google) ----
    cur.execute("SELECT proveedor, sub, email, perfil_id FROM identidades_oauth")
    identidades = [dict(zip(["proveedor", "sub", "email", "perfil_id"], row))
                   for row in cur.fetchall()]

    cur.close()
    return {"perfiles": perfiles, "productores": productores, "catas": catas,
            "paises": paises, "ciudades": ciudades, "coffeeshops": coffeeshops,
            "descartes": descartes, "identidades": identidades}


# -----------------------------------------------------------------------------
# Escritura (re-sync total: simple y correcto para volúmenes pequeños)
# -----------------------------------------------------------------------------

def guardar_datos(datos: dict) -> None:
    """Persiste la estructura completa en Postgres (transacción atómica).
    OPTIMIZADO (ago 2026): el re-sync fila a fila (~100 round-trips al pooler,
    ~5-7 s) se sustituyó por:
      - DELETE total SOLO de tablas hijo (votos, comentarios, descartes...).
      - DELETE condicional de entidades: únicamente las que ya NO están en el
        dict (misma semántica que el re-sync total: lo eliminado desaparece).
      - UPSERT en BATCH (execute_values: 1 round-trip por tabla).
      - Fotos b64: se re-suben SOLO si cambiaron (md5 calculado en el servidor;
        si no cambiaron, el CASE conserva la existente)."""
    conn = _conectar()
    if conn is None:
        raise RuntimeError("Supabase no configurado (url/key) o psycopg2 ausente")
    try:
        from psycopg2.extras import execute_values
        cur = conn.cursor()

        # ---- 1) Limpieza (un solo round-trip) ----
        cur.execute(
            "DELETE FROM votos; DELETE FROM comentarios_usuarios; "
            "DELETE FROM votos_coffeeshops; DELETE FROM coffeeshop_productores; "
            "DELETE FROM descartes_usuarios; DELETE FROM identidades_oauth")

        # Un solo round-trip para los 6 DELETEs condicionales
        _ids = lambda tabla, clave: [e[clave] for e in datos.get(tabla, [])
                                     if isinstance(e, dict) and e.get(clave)]
        _cs = _ids("coffeeshops", "id")
        _ci = _ids("ciudades", "id")
        _pa = _ids("paises", "id")
        _ca = _ids("catas", "id")
        _pr = _ids("productores", "id")
        _pe = _ids("perfiles", "id")
        cur.execute(
            "DELETE FROM coffeeshops WHERE id <> ALL(%s); "
            "DELETE FROM ciudades WHERE id <> ALL(%s); "
            "DELETE FROM paises WHERE id <> ALL(%s); "
            "DELETE FROM catas WHERE id <> ALL(%s); "
            "DELETE FROM productores WHERE id <> ALL(%s); "
            "DELETE FROM perfiles WHERE id <> ALL(%s)",
            (_cs or [""], _ci or [""], _pa or [""],
             _ca or [""], _pr or [""], _pe or [""]))
        # Nota: listas vacías -> [''] -> `<> ALL([''])` borra todo (semántica
        # de re-sync: sin entidades en el dict, la tabla queda vacía).

        # ---- 2) Hash de fotos existentes (md5 calculado en el servidor: no se
        #        transfieren los MB, solo los hashes) — 1 solo round-trip ----
        cur.execute(
            "SELECT 'catas' AS t, id, md5(foto_b64) FROM catas "
            "UNION ALL SELECT 'productores', id, md5(foto_b64) FROM productores "
            "UNION ALL SELECT 'coffeeshops', id, md5(foto_b64) FROM coffeeshops")
        md5_catas = {}
        md5_prod = {}
        md5_cs = {}
        for t, eid, h in cur.fetchall():
            (md5_catas if t == "catas" else md5_prod if t == "productores"
             else md5_cs)[eid] = h

        def _b64_si_cambio(foto_b64: str, md5_previo: str) -> str:
            """Devuelve la foto SOLO si cambió; '' si es igual (conservar)."""
            if not foto_b64:
                return ""
            if md5_previo == hashlib.md5(foto_b64.encode("utf-8")).hexdigest():
                return ""
            return foto_b64

        # ---- 3) Perfiles (batch) ----
        filas = [(p.get("id"), p.get("nombre", ""), p.get("password_hash", ""),
                  bool(p.get("es_confianza")), bool(p.get("es_admin")))
                 for p in datos.get("perfiles", [])
                 if isinstance(p, dict) and p.get("id")]
        if filas:
            execute_values(
                cur,
                "INSERT INTO perfiles (id, nombre, password_hash, es_confianza, es_admin) "
                "VALUES %s ON CONFLICT (id) DO UPDATE SET "
                "nombre = EXCLUDED.nombre, password_hash = EXCLUDED.password_hash, "
                "es_confianza = EXCLUDED.es_confianza, es_admin = EXCLUDED.es_admin",
                filas)

        # ---- 4) Productores (batch + fotos solo si cambian) ----
        filas = []
        for pr in datos.get("productores", []):
            if not isinstance(pr, dict) or not pr.get("id"):
                continue
            filas.append((pr["id"], pr.get("nombre", ""), pr.get("foto", ""),
                          pr.get("pais", ""),
                          _b64_si_cambio(pr.get("foto_b64", "") or "",
                                         md5_prod.get(pr["id"], ""))))
        if filas:
            execute_values(
                cur,
                "INSERT INTO productores (id, nombre, foto, pais, foto_b64) "
                "VALUES %s ON CONFLICT (id) DO UPDATE SET "
                "nombre = EXCLUDED.nombre, foto = EXCLUDED.foto, "
                "pais = EXCLUDED.pais, "
                "foto_b64 = CASE WHEN EXCLUDED.foto_b64 = '' "
                "THEN productores.foto_b64 ELSE EXCLUDED.foto_b64 END",
                filas)

        # ---- 5) Catas + votos + comentarios (batch) ----
        filas = []
        for c in datos.get("catas", []):
            if not isinstance(c, dict) or not c.get("id"):
                continue
            filas.append((c["id"], c.get("fecha", ""), c.get("nombre", ""),
                          c.get("productor", ""), c.get("tipo", ""),
                          c.get("comentarios", ""), c.get("pais", ""),
                          c.get("foto", ""), c.get("anio", ""),
                          c.get("temporada", ""),
                          _b64_si_cambio(c.get("foto_b64", "") or "",
                                         md5_catas.get(c["id"], ""))))
        if filas:
            execute_values(
                cur,
                "INSERT INTO catas (id, fecha, nombre, productor, tipo, "
                "comentarios, pais, foto, anio, temporada, foto_b64) "
                "VALUES %s ON CONFLICT (id) DO UPDATE SET "
                "fecha = EXCLUDED.fecha, nombre = EXCLUDED.nombre, "
                "productor = EXCLUDED.productor, tipo = EXCLUDED.tipo, "
                "comentarios = EXCLUDED.comentarios, pais = EXCLUDED.pais, "
                "foto = EXCLUDED.foto, anio = EXCLUDED.anio, "
                "temporada = EXCLUDED.temporada, "
                "foto_b64 = CASE WHEN EXCLUDED.foto_b64 = '' "
                "THEN catas.foto_b64 ELSE EXCLUDED.foto_b64 END",
                filas)

        filas_votos = []
        filas_coment = []
        for c in datos.get("catas", []):
            if not isinstance(c, dict) or not c.get("id"):
                continue
            for v in c.get("votos", []):
                if not isinstance(v, dict) or not v.get("perfil_id"):
                    continue
                filas_votos.append(
                    (c["id"], v.get("perfil_id"), v.get("fecha", ""),
                     json.dumps(v.get("puntuaciones_detalle", {}), ensure_ascii=False),
                     json.dumps(v.get("notas_bloques", {}), ensure_ascii=False),
                     float(v.get("nota_final", 0.0))))
            for cm in c.get("comentarios_usuarios", []):
                if not isinstance(cm, dict) or not cm.get("perfil_id"):
                    continue
                filas_coment.append(
                    (c["id"], cm.get("perfil_id"), cm.get("nombre", ""),
                     cm.get("fecha", ""), cm.get("texto", "")))
        if filas_votos:
            execute_values(
                cur,
                "INSERT INTO votos (cata_id, perfil_id, fecha, "
                "puntuaciones_detalle, notas_bloques, nota_final) VALUES %s",
                filas_votos)
        if filas_coment:
            execute_values(
                cur,
                "INSERT INTO comentarios_usuarios "
                "(cata_id, perfil_id, nombre, fecha, texto) VALUES %s",
                filas_coment)

        # ---- 6) Asociaciones / Coffeeshops (batch) ----
        filas = [(p.get("id"), p.get("nombre", ""))
                 for p in datos.get("paises", [])
                 if isinstance(p, dict) and p.get("id")]
        if filas:
            execute_values(
                cur,
                "INSERT INTO paises (id, nombre) VALUES %s "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre",
                filas)

        filas = [(c.get("id"), c.get("nombre", ""), c.get("pais_id") or None)
                 for c in datos.get("ciudades", [])
                 if isinstance(c, dict) and c.get("id")]
        if filas:
            execute_values(
                cur,
                "INSERT INTO ciudades (id, nombre, pais_id) VALUES %s "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "pais_id = EXCLUDED.pais_id",
                filas)

        filas = []
        filas_votos_cs = []
        filas_cs_prod = []
        for cs in datos.get("coffeeshops", []):
            if not isinstance(cs, dict) or not cs.get("id"):
                continue
            filas.append((cs["id"], cs.get("nombre", ""),
                          cs.get("pais_id") or None, cs.get("ciudad_id") or None,
                          cs.get("direccion", ""), cs.get("biografia", ""),
                          cs.get("creado", ""),
                          _b64_si_cambio(cs.get("foto_b64", "") or "",
                                         md5_cs.get(cs["id"], ""))))
            for v in cs.get("votos", []):
                if not isinstance(v, dict) or not v.get("perfil_id"):
                    continue
                filas_votos_cs.append(
                    (cs["id"], v.get("perfil_id"), v.get("fecha", ""),
                     float(v.get("nota", 0.0)), v.get("comentario", "")))
            for pr_id in cs.get("productores", []):
                if pr_id:
                    filas_cs_prod.append((cs["id"], pr_id))
        if filas:
            execute_values(
                cur,
                "INSERT INTO coffeeshops (id, nombre, pais_id, ciudad_id, "
                "direccion, biografia, creado, foto_b64) VALUES %s "
                "ON CONFLICT (id) DO UPDATE SET nombre = EXCLUDED.nombre, "
                "pais_id = EXCLUDED.pais_id, ciudad_id = EXCLUDED.ciudad_id, "
                "direccion = EXCLUDED.direccion, biografia = EXCLUDED.biografia, "
                "creado = EXCLUDED.creado, "
                "foto_b64 = CASE WHEN EXCLUDED.foto_b64 = '' "
                "THEN coffeeshops.foto_b64 ELSE EXCLUDED.foto_b64 END",
                filas)
        if filas_votos_cs:
            execute_values(
                cur,
                "INSERT INTO votos_coffeeshops "
                "(coffeeshop_id, perfil_id, fecha, nota, comentario) VALUES %s",
                filas_votos_cs)
        if filas_cs_prod:
            execute_values(
                cur,
                "INSERT INTO coffeeshop_productores "
                "(coffeeshop_id, productor_id) VALUES %s ON CONFLICT DO NOTHING",
                filas_cs_prod)

        # ---- 7) Descartes "No lo probé" (batch) ----
        filas = [(d.get("cata_id"), d.get("perfil_id"), d.get("fecha", ""))
                 for d in datos.get("descartes", [])
                 if isinstance(d, dict) and d.get("cata_id") and d.get("perfil_id")]
        if filas:
            execute_values(
                cur,
                "INSERT INTO descartes_usuarios (cata_id, perfil_id, fecha) "
                "VALUES %s ON CONFLICT (cata_id, perfil_id) "
                "DO UPDATE SET fecha = EXCLUDED.fecha",
                filas)

        # ---- 8) Identidades OAuth (batch) ----
        filas = [(i.get("proveedor"), i.get("sub"), i.get("email", ""),
                  i.get("perfil_id"))
                 for i in datos.get("identidades", [])
                 if isinstance(i, dict) and i.get("proveedor")
                 and i.get("sub") and i.get("perfil_id")]
        if filas:
            execute_values(
                cur,
                "INSERT INTO identidades_oauth (proveedor, sub, email, perfil_id) "
                "VALUES %s ON CONFLICT (proveedor, sub) DO UPDATE SET "
                "email = EXCLUDED.email, perfil_id = EXCLUDED.perfil_id",
                filas)

        conn.commit()
        cur.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _devolver(conn)
