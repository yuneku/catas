#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meter_productores_lote.py — Insertar TODOS los productores pendientes de una carpeta en Supabase (proyecto CATAS), SIN tocar código.

Uso:
  python meter_productores_lote.py "C:/carpeta" [--dry-run]

Estructura esperada: archivos "Nombre - Pais.jpeg" (o .jpg/.png/.webp).
Omitidos automáticamente: los que ya existen en la BD (por nombre) y los que no sigan el patrón "X - Y".
Backup único + re-sync total único + verificación final.
"""
import argparse, base64, io, json, os, re, shutil, sys
from datetime import datetime

PROY = r"C:/Users/Yunes/Desktop/Catas"
sys.path.insert(0, PROY)

PAISES_VALIDOS = ["España", "Marruecos", "USA", "Tailandia"]


def generar_id(ids, prefijo=""):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    pid = f"{prefijo}{ts}"
    n = 2
    while pid in ids:
        pid = f"{prefijo}{ts}-{n}"
        n += 1
    return pid


def foto_a_b64(ruta):
    from PIL import Image
    with Image.open(ruta) as im:
        im = im.convert("RGB")
        im.thumbnail((512, 512))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parsear_nombre(stem):
    """'BadBerrerFarm - Marruecos' -> (BadBerrerFarm, Marruecos). Tolera espacios extra."""
    m = re.match(r'^(.*?)\s+-\s+(.+?)\s*$', stem.strip())
    if not m:
        return None, None
    nombre, pais = m.group(1).strip(), m.group(2).strip()
    if not nombre or not pais:
        return None, None
    return nombre, pais


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpeta", help="Carpeta con las fotos 'Nombre - Pais.ext'")
    ap.add_argument("--dry-run", action="store_true", help="Solo ensayo, no toca la BD")
    args = ap.parse_args()

    if not os.path.isdir(args.carpeta):
        print(f"❌ ERROR: la carpeta no existe: {args.carpeta}")
        sys.exit(2)

    # 1. Estado actual desde Supabase
    try:
        from db_supabase import cargar_datos, guardar_datos
        d = cargar_datos()
    except Exception as e:
        print(f"❌ ERROR al cargar de Supabase: {e}")
        sys.exit(1)
    productores = d.get("productores", [])
    ids_existentes = set(d.get("ids_existentes", []))
    nombres_existentes = {p["nombre"].strip().lower() for p in productores}
    print(f"📡 BD: {len(productores)} productores · {len(d.get('catas', []))} catas")

    # 2. Escanear carpeta
    pendientes, omitidos = [], []
    for f in sorted(os.listdir(args.carpeta)):
        ruta = os.path.join(args.carpeta, f)
        if not os.path.isfile(ruta) or not f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        nombre, pais = parsear_nombre(os.path.splitext(f)[0])
        if not nombre or not pais:
            omitidos.append((f, "no sigue el patrón 'Nombre - Pais'"))
            continue
        if nombre.lower() in nombres_existentes:
            omitidos.append((f, f"ya existe en la BD ({nombre})"))
            continue
        if pais not in PAISES_VALIDOS:
            omitidos.append((f, f"país '{pais}' no válido (válidos: {', '.join(PAISES_VALIDOS)})"))
            continue
        pendientes.append({"nombre": nombre, "pais": pais, "ruta": ruta, "f": f})

    # 3. Resumen
    print(f"\n📁 {len(pendientes)} productor(es) pendiente(s), {len(omitidos)} omitido(s)")
    if args.dry_run:
        print("\n🧪 DRY-RUN (no se ha tocado nada):")
        for e in pendientes:
            print(f"   + {e['nombre']} | {e['pais']} | {e['f']}")
        for f, razon in omitidos:
            print(f"   - {f} → {razon}")
        print("\n✅ Ensayo OK. Ejecutar sin --dry-run para guardar.")
        return

    # 4. Construir entidades + backup + guardar (re-sync TOTAL)
    nuevas = []
    for e in pendientes:
        pid = generar_id(ids_existentes, "pr_")
        ids_existentes.add(pid)
        try:
            b64 = foto_a_b64(e["ruta"])
        except Exception as ex:
            print(f"❌ ERROR leyendo foto {e['f']}: {ex}")
            sys.exit(1)
        ext = os.path.splitext(e["f"])[1].lower()
        destino = os.path.join(PROY, "imagenes", f"{pid}{ext}")
        shutil.copy(e["ruta"], destino)
        nuevas.append({"id": pid, "nombre": e["nombre"], "pais": e["pais"],
                       "foto": f"imagenes/{os.path.basename(destino)}", "foto_b64": b64,
                       "votos": [], "comentarios_usuarios": []})
    d["productores"].extend(nuevas)
    d["ids_existentes"] = sorted(ids_existentes)

    backup = os.path.join(PROY, "backups", f"catas_backup_pre_productores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in d.items() if k != "ids_existentes"}, fh, ensure_ascii=False, indent=2)
    print(f"💾 Backup: {os.path.basename(backup)}")

    guardar_datos(d)
    print(f"💾 Guardado en Supabase: {len(nuevas)} productor(es)")

    # 5. Verificación (recargar desde Supabase)
    try:
        d2 = cargar_datos()
        names2 = {p["nombre"].strip().lower() for p in d2.get("productores", [])}
        ok = [e["nombre"] for e in nuevas if e["nombre"].lower() in names2]
        faltan = [e["nombre"] for e in nuevas if e["nombre"].lower() not in names2]
        print(f"\n✅ VERIFICACIÓN: {len(d2.get('productores', []))} productores en BD ahora")
        for n in ok:
            print(f"   + {n} → OK")
        if faltan:
            print(f"   ❌ NO encontrados: {faltan}")
            sys.exit(1)
    except Exception as e:
        print(f"⚠️ No se pudo verificar recargando: {e}")


if __name__ == "__main__":
    main()
