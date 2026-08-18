# -*- coding: utf-8 -*-
"""
===============================================================================
 migrar_a_supabase.py — Migra catas.json a Supabase (Postgres)
===============================================================================
 Requisitos:
   - SUPABASE_URL en el entorno (o en .env local): la connection string
     completa de Project Settings → Database → Connection string → URI.
   - psycopg2 instalado:  pip install psycopg2-binary

 Uso:
   python scripts/migrar_a_supabase.py [ruta_a_catas.json]

 Hace:
   1. Crea las tablas (ejecuta sql/schema.sql).
   2. Lee catas.json y lo vuelca con db_supabase.guardar_datos().
   3. Verifica leyendo de vuelta y comparando conteos.
===============================================================================
"""

import json
import os
import sys

RUTA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUTA)

import db_supabase  # noqa: E402


def _miniatura_b64(ruta: str, max_lado: int = 512) -> str:
    """Lee una imagen, la reduce a miniatura y la devuelve en base64.
    '' si no existe o no puede leerse."""
    if not ruta or not os.path.exists(ruta):
        return ""
    try:
        from PIL import Image
        import io
        import base64
        img = Image.open(ruta)
        img = img.convert("RGB")
        img.thumbnail((max_lado, max_lado), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def _incrustar_fotos(datos: dict) -> None:
    """Rellena foto_b64 con miniaturas de las fotos locales (solo si la
    entidad no tiene b64 ya). Así la nube no depende del filesystem y el
    repo público no necesita exponer las imágenes originales."""
    for c in datos.get("catas", []):
        if isinstance(c, dict) and not c.get("foto_b64") and c.get("foto"):
            c["foto_b64"] = _miniatura_b64(os.path.join(RUTA, c["foto"]))
    for p in datos.get("productores", []):
        if isinstance(p, dict) and not p.get("foto_b64") and p.get("foto"):
            p["foto_b64"] = _miniatura_b64(os.path.join(RUTA, p["foto"]))


def main():
    json_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RUTA, "catas.json")

    if not db_supabase.activo():
        print("❌ SUPABASE_URL no configurada.")
        print("   Exporta la variable o crea un .env antes de ejecutar.")
        return 1

    if not os.path.exists(json_path):
        print(f"❌ No existe {json_path}")
        return 1

    with open(json_path, encoding="utf-8") as f:
        datos = json.load(f)

    # 1) Esquema
    schema = os.path.join(RUTA, "sql", "schema.sql")
    conn = db_supabase._conectar()
    if conn is None:
        print("❌ No se pudo conectar (¿psycopg2 instalado? ¿URL/key correctas?)")
        return 1
    try:
        cur = conn.cursor()
        with open(schema, encoding="utf-8") as f:
            cur.execute(f.read())
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Tablas creadas/verificadas")
    except Exception as e:
        print(f"❌ Error creando tablas: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return 1

    # 2) Incrustar fotos (miniaturas b64) y vuelco de datos
    _incrustar_fotos(datos)
    n_fotos = sum(1 for c in datos.get("catas", []) if c.get("foto_b64"))
    n_fotos += sum(1 for p in datos.get("productores", []) if p.get("foto_b64"))
    try:
        db_supabase.guardar_datos(datos)
        print(f"✅ Vuelco completado: {len(datos.get('perfiles', []))} perfiles, "
              f"{len(datos.get('productores', []))} productores, "
              f"{len(datos.get('catas', []))} catas "
              f"({n_fotos} fotos incrustadas)")
    except Exception as e:
        print(f"❌ Error en el vuelco: {e}")
        return 1

    # 3) Verificación
    leidos = db_supabase.cargar_datos()
    n_catas = len(leidos.get("catas", []))
    n_perfiles = len(leidos.get("perfiles", []))
    n_productores = len(leidos.get("productores", []))
    print(f"🔍 Verificación (lectura de vuelta): {n_perfiles} perfiles · "
          f"{n_productores} productores · {n_catas} catas")

    if (n_catas == len(datos.get("catas", []))
            and n_perfiles == len(datos.get("perfiles", []))):
        print("🎉 Migración verificada correctamente.")
        return 0
    print("⚠️  Los conteos no coinciden exactamente — revisar manualmente.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
