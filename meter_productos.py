#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meter_productos.py — Insertar productos/catas en Supabase (proyecto CATAS) desde una carpeta, SIN tocar código.

Estructura esperada (subcarpeta = NOMBRE del productor, ya debe existir en la BD):
  CARPETA/
    ProductorA/
      Nombre 2026 Frozen.jpeg
      Otro 2025 Frozen.png
    ProductorB/
      ...

Nombre de archivo: admite CUALQUIER formato (tipo antes o después del año, guiones o espacios):
  "Variedad AÑO TIPO" | "Variedad - TIPO - AÑO" | "Variedad - TIPO" | "Variedad AÑO" | "Variedad"
Tipos válidos: Flor, Dry, Static, Frozen, Fresh Frozen, WPFF, Rosin, BHO, Live Resin.

Uso:
  python meter_productos.py "C:/.../Productos" [--tipo X] [--dry-run]  (lote)
  python meter_productos.py "C:/.../Productos/Zkittles" [--dry-run]     (carpeta plana)
  python meter_productos.py "C:/.../Productos" --check                  (auditoría: NO toca nada,
                                                                         comprueba que todo corresponde)

Reglas que cumple solo:
  - Lee la url de secrets.toml SIN imprimirla
  - Carga el estado actual desde Supabase
  - El productor debe existir ya en la BD (si falta -> avisa, no inventa)
  - No duplica: omite productos con mismo productor+nombre ya existentes
  - Verificación de sanidad: nombre vacío, año raro, tipo por defecto -> avisos claros
  - Backup antes de guardar; guardar_datos() = re-sync total
  - foto_b64 = miniatura JPEG 512px q80 REAL (nunca inventada)
  - Verificación final releyendo Supabase + mirror local catas.json
"""
import argparse, base64, io, json, os, re, shutil, sys
from datetime import datetime
from PIL import Image

PROY = r"C:/Users/Yunes/Desktop/Catas"
sys.path.insert(0, PROY)

TIPOS_VALIDOS = ["Flor", "Dry", "Static", "Frozen", "Fresh Frozen",
                 "WPFF", "Rosin", "BHO", "Live Resin"]
EXT_VALIDAS = (".jpg", ".jpeg", ".png", ".webp")
# Restos que sugieren que el nombre quedó mal parseado (números sueltos, paréntesis...)
_RESTOS_RAROS = re.compile(r"\b\d{2,}\b|[()\[\]{}¿?¡!]")


def generar_id(ids, prefijo):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    pid = f"{prefijo}{ts}"
    n = 2
    while pid in ids:
        pid = f"{prefijo}{ts}-{n}"
        n += 1
    return pid


def foto_a_b64_y_thumb(ruta):
    with Image.open(ruta) as im:
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def normalizar(s):
    """'Variedad - Frozen - 2026' -> 'Variedad Frozen 2026'.
    Quita guiones (incl. en-dash), paréntesis, guiones bajos; colapsa espacios."""
    s = s.replace("_", " ")
    s = re.sub(r"[()\[\]{}]", " ", s)
    s = re.sub(r"[-–—]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parsear_nombre(stem):
    """Admite cualquier orden: tipo antes/después del año, con guiones o espacios.

    'BlackGuava 2026 Frozen' | 'Hungover Limez - Frozen - 2026'
    | 'Variedad - Frozen' | 'Variedad 2026' | 'Variedad'
    -> (nombre, año, tipo). tipo=None si no se reconoce ninguno.
    """
    s = normalizar(stem)
    # Año en cualquier posición (solo 20xx válidos)
    m_anio = re.search(r"\b(20\d{2})\b", s)
    anio = m_anio.group(1) if m_anio else None

    # Tipo: compuestos primero (Fresh Frozen > Frozen); palabra delimitada
    tipo = None
    resto = s
    for t in sorted(TIPOS_VALIDOS, key=len, reverse=True):
        mt = re.search(r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])", s, re.IGNORECASE)
        if mt:
            tipo = t
            resto = s[:mt.start()] + " " + s[mt.end():]
            break

    if anio:
        resto = resto.replace(anio, " ")
    resto = re.sub(r"\s+", " ", resto).strip()
    nombre = re.sub(r"[-–—]+", " ", resto)
    nombre = re.sub(r"\s+", " ", nombre).strip(" -–—")
    if not nombre and not anio and not tipo:
        nombre = stem.strip()  # no se reconoció nada: usa el nombre original tal cual
    return nombre, anio, tipo


def verificar_sanidad(nombre, anio, tipo, tipo_por_defecto):
    """Devuelve lista de avisos; lista vacía = todo correcto."""
    avisos = []
    if not nombre or not nombre.strip():
        avisos.append("nombre vacío")
    if anio is not None and not (2000 <= int(anio) <= 2099):
        avisos.append(f"año raro: {anio}")
    if tipo_por_defecto:
        avisos.append("tipo por defecto (no venía en el nombre)")
    if nombre and _RESTOS_RAROS.search(nombre):
        avisos.append(f"nombre con restos raros: '{nombre}'")
    return avisos


def main():
    ap = argparse.ArgumentParser(description="Meter productos en CATAS/Supabase")
    ap.add_argument("carpeta", help="carpeta con subcarpetas de productores (o una carpeta de productor plana)")
    ap.add_argument("--tipo", default="", help="tipo por defecto si el nombre no lo indica (ej: Frozen)")
    ap.add_argument("--dry-run", action="store_true", help="ensayo: NO toca la BD ni copia fotos")
    ap.add_argument("--check", action="store_true", help="auditoría: NO toca nada, comprueba que todo corresponde")
    args = ap.parse_args()

    if not os.path.isdir(args.carpeta):
        sys.exit(f"❌ No existe la carpeta: {args.carpeta}")
    if args.tipo and args.tipo not in TIPOS_VALIDOS:
        sys.exit(f"❌ --tipo '{args.tipo}' no válido. Válidos: {', '.join(TIPOS_VALIDOS)}")

    # 1. Conexión (sin imprimir secretos)
    s = open(os.path.join(PROY, ".streamlit", "secrets.toml"), encoding="utf-8").read()
    m = re.search(r'^\s*url\s*=\s*"([^"]+)"', s, re.M)
    if not m:
        sys.exit("❌ No se encontró la url en secrets.toml.")
    os.environ["SUPABASE_URL"] = m.group(1)
    print("✅ Conexión preparada (credenciales enmascaradas)")

    import db_supabase
    datos = db_supabase.cargar_datos()
    claves = ("perfiles", "productores", "catas", "paises", "ciudades", "coffeeshops")
    print("📊 Estado actual:", {k: len(datos.get(k, [])) for k in claves})

    ids = set()
    for k in claves:
        for e in datos.get(k, []):
            if isinstance(e, dict) and e.get("id"):
                ids.add(e["id"])

    productores = {p.get("nombre", "").strip().lower(): p for p in datos["productores"]}
    catas_existentes = {(c.get("productor", "").strip().lower(), c.get("nombre", "").strip().lower())
                        for c in datos["catas"]}

    # 2. Explorar carpeta: subcarpetas = productores; si no hay, la propia carpeta es un productor
    sub = sorted(d for d in os.listdir(args.carpeta)
                 if os.path.isdir(os.path.join(args.carpeta, d)))
    if sub:
        grupos = [(d, os.path.join(args.carpeta, d)) for d in sub]
    else:
        grupos = [(os.path.basename(args.carpeta.rstrip("/\\")), args.carpeta)]

    # 2b. Recorrer archivos una sola vez y clasificar
    nuevos, omitidos, faltan = [], [], []
    for nombre_prod, carpeta_prod in grupos:
        prod = productores.get(nombre_prod.strip().lower())
        if not prod:
            faltan.append(nombre_prod)
            continue
        for fn in sorted(os.listdir(carpeta_prod)):
            ruta = os.path.join(carpeta_prod, fn)
            if not os.path.isfile(ruta) or os.path.splitext(fn)[1].lower() not in EXT_VALIDAS:
                continue
            stem = os.path.splitext(fn)[0]
            nombre, anio, tipo = parsear_nombre(stem)
            if (nombre_prod.strip().lower(), nombre.lower()) in catas_existentes:
                omitidos.append((nombre_prod, nombre, "ya existe"))
                continue
            tipo_original = tipo  # None si no venía en el nombre
            if not tipo:
                tipo = args.tipo or "Flor"
            avisos = verificar_sanidad(nombre, anio, tipo, tipo_por_defecto=(tipo_original is None))
            nuevos.append({"productor": prod, "nombre": nombre, "anio": anio,
                           "tipo": tipo, "tipo_default": tipo_original is None,
                           "ruta": ruta, "fn": fn, "avisos": avisos})

    # 3. Informes comunes (check y dry-run)
    if faltan:
        print("\n⚠️ PRODUCTORES QUE FALTAN EN LA BD (no se inventa nada, se omiten sus productos):")
        for f in faltan:
            print(f"   - {f}  → créalo primero con meter_productores_lote.py")
    if omitidos:
        print("\nℹ️ OMITIDOS:")
        for prod, nom, motivo in omitidos:
            print(f"   - {prod} / {nom}: {motivo}")

    # 4. Modo AUDITORÍA (--check): no construye entidades, no guarda nada
    if args.check:
        print("\n🔍 AUDITORÍA (--check): comprobando que todo corresponde...")
        n_avisos = 0
        if nuevos:
            print(f"   📦 {len(nuevos)} pendiente(s) de insertar:")
            for n in nuevos:
                marca = " ⚠️ " + "; ".join(n["avisos"]) if n["avisos"] else " ✅"
                if n["avisos"]:
                    n_avisos += 1
                print(f"      - {n['productor']['nombre']} / {n['nombre']} · {n['tipo']} · {n['anio'] or 's/a'}{marca}")
        else:
            print("   ✅ No hay productos pendientes (todo insertado o falta productor).")
        print()
        problemas = [f"⚠️ {len(faltan)} productor(es) faltan en BD", f"⚠️ {n_avisos} archivo(s) con avisos"] if (faltan or n_avisos) else []
        if problemas:
            print("❌ NO TODO CORRESPONDE:")
            for p in problemas:
                print(f"   - {p}")
            print("   → Resolver antes de insertar (productores primero; revisar nombres).")
            sys.exit(1)
        print("✅ TODO CORRESPONDE: los archivos están bien formados y listos para insertar.")
        sys.exit(0)

    if not nuevos:
        sys.exit("\n❌ No hay productos nuevos que insertar (todo existe o falta el productor).")

    # 5. Construir entidades (fotos reales)
    print(f"\n📦 Productos a insertar: {len(nuevos)}")
    for n in nuevos:
        if n["avisos"]:
            print(f"   ⚠️ {n['productor']['nombre']} / {n['nombre']}: {'; '.join(n['avisos'])}")
    entidades = []
    for n in nuevos:
        cid = generar_id(ids, "")
        ext = os.path.splitext(n["ruta"])[1].lower()
        foto_b64 = foto_a_b64_y_thumb(n["ruta"])
        ent = {
            "id": cid,
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "nombre": n["nombre"],
            "productor": n["productor"]["nombre"],
            "tipo": n["tipo"],
            "pais": n["productor"].get("pais", ""),
            "anio": n["anio"] or "",
            "temporada": "",
            "comentarios": "",
            "foto": f"imagenes/{cid}{ext}",
            "foto_b64": foto_b64,
            "votos": [],
            "comentarios_usuarios": [],
        }
        if not args.dry_run:
            shutil.copy(n["ruta"], os.path.join(PROY, "imagenes", f"{cid}{ext}"))
        entidades.append(ent)
        ids.add(cid)

    if args.dry_run:
        print("\n🧪 DRY-RUN (no se ha tocado nada):")
        for e in entidades:
            aviso = " ⚠️ tipo por defecto" if e.get("tipo_default") else ""
            print(f"   - {e['productor']} / {e['nombre']} · {e['tipo']} · {e['anio'] or 's/a'} · pais={e['pais']} · foto_b64 {len(e['foto_b64'])} chars{aviso}")
        print(f"\n   Total catas tras insertar: {len(datos['catas']) + len(entidades)}")
        sys.exit(0)

    # 6. Backup + guardar (re-sync total) + mirror local
    os.makedirs(os.path.join(PROY, "backups"), exist_ok=True)
    bak = os.path.join(PROY, "backups",
                       f"catas_backup_pre_productos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    print(f"💾 Backup: {bak}")

    datos["catas"].extend(entidades)
    db_supabase.guardar_datos(datos)
    print("✅ guardar_datos() OK (re-sync total)")
    with open(os.path.join(PROY, "catas.json"), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    print("✅ catas.json local sincronizado")

    # 7. Verificación releyendo Supabase
    leidos = db_supabase.cargar_datos()
    nuevos_ids = {e["id"] for e in entidades}
    encontrados = [c for c in leidos["catas"] if c.get("id") in nuevos_ids]
    print(f"\n✅ VERIFICADO: {len(encontrados)}/{len(entidades)} productos en Supabase")
    for c in encontrados:
        ok = "foto_b64 OK" if c.get("foto_b64") else "⚠️ SIN foto_b64"
        print(f"   - {c['productor']} / {c['nombre']} · {c.get('tipo')} · {ok}")
    if len(encontrados) != len(entidades):
        print("⚠️ Faltan productos tras releer — revisar logs.")

    print(f"\n📋 Total catas en Supabase: {len(leidos['catas'])}")
    print("👉 Refresca la app (terpsxhunter.streamlit.app) para verlo.")


if __name__ == "__main__":
    main()
