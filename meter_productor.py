#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meter_productor.py — Insertar un productor en Supabase (proyecto CATAS) SIN tocar código.

Uso:
  python meter_productor.py "NOMBRE" "PAIS" ["C:/ruta/foto.jpg"] [--dry-run]

Ejemplos:
  python meter_productor.py "Chosen" "USA" "C:/Users/Yunes/Desktop/foto_chosen.jpg"
  python meter_productor.py "Chosen" "USA"            (sin foto: avisa que no se verá imagen)
  python meter_productor.py "Chosen" "USA" "foto.jpg" --dry-run   (ensayo: no toca la BD)

Reglas que cumple solo (no las toques):
  - Lee la url de secrets.toml SIN imprimirla
  - Carga el estado actual desde Supabase (nunca del catas.json local)
  - Backup antes de guardar en backups/
  - guardar_datos() = re-sync total (cargar -> añadir -> guardar)
  - foto_b64 = miniatura JPEG 512px q80 real (nunca inventada)
  - Verificación final releyendo Supabase + mirror local catas.json
"""
import argparse, base64, io, json, os, re, shutil, sys
from datetime import datetime
from PIL import Image

PROY = r"C:/Users/Yunes/Desktop/Catas"
sys.path.insert(0, PROY)

PAISES_VALIDOS = ["España", "Marruecos", "USA", "Tailandia"]
MAPA_PAISES = {
    "españa": "España", "espana": "España", "es": "España",
    "marruecos": "Marruecos", "ma": "Marruecos", "maroc": "Marruecos",
    "usa": "USA", "eeuu": "USA", "ee.uu": "USA", "us": "USA", "estados unidos": "USA",
    "tailandia": "Tailandia", "thai": "Tailandia", "th": "Tailandia",
}


def normalizar_pais(p):
    return MAPA_PAISES.get((p or "").strip().lower(), None)


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


def main():
    ap = argparse.ArgumentParser(description="Meter productor en CATAS/Supabase")
    ap.add_argument("nombre", help="nombre del productor, ej: Chosen")
    ap.add_argument("pais", help="España | Marruecos | USA | Tailandia (acepta variantes)")
    ap.add_argument("foto", nargs="?", default="", help="ruta de la foto (opcional)")
    ap.add_argument("--dry-run", action="store_true", help="ensayo: NO toca la BD ni hace backup")
    args = ap.parse_args()

    nombre = args.nombre.strip()
    pais = normalizar_pais(args.pais)
    if not nombre:
        sys.exit("❌ Error: el nombre no puede estar vacío.")
    if not pais:
        sys.exit(f"❌ Error: país '{args.pais}' no válido. Válidos: {', '.join(PAISES_VALIDOS)}")

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

    if any((p.get("nombre") or "").strip().lower() == nombre.lower() for p in datos["productores"]):
        sys.exit(f"❌ Ya existe un productor '{nombre}' en Supabase. No hago nada (evito duplicar).")

    # 2. Backup (solo si no es ensayo)
    if not args.dry_run:
        os.makedirs(os.path.join(PROY, "backups"), exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_") or "productor"
        bak = os.path.join(PROY, "backups",
                           f"catas_backup_pre_{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(bak, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=4)
        print(f"💾 Backup: {bak}")

    # 3. Foto real -> copia + base64
    pid = generar_id(ids, "pr_")
    foto = (args.foto or "").strip()
    foto_b64, foto_rel = "", ""
    if foto:
        if not os.path.exists(foto):
            sys.exit(f"❌ No existe la foto: {foto}. Pasa la ruta correcta o ejecuta sin foto.")
        ext = os.path.splitext(foto)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            sys.exit(f"❌ Extensión no soportada: {ext}")
        os.makedirs(os.path.join(PROY, "imagenes"), exist_ok=True)
        destino = os.path.join(PROY, "imagenes", pid + ext)
        if not args.dry_run:
            os.makedirs(os.path.join(PROY, "imagenes"), exist_ok=True)
            shutil.copy(foto, destino)
        foto_b64 = foto_a_b64_y_thumb(foto)
        foto_rel = f"imagenes/{pid}{ext}"
        print(f"🖼️ Foto -> {foto_rel} · base64 OK ({len(foto_b64)} chars)" + (" (dry-run: no copiada)" if args.dry_run else " y copiada"))
    else:
        print("⚠️ Sin foto: el productor se verá SIN imagen en la nube (foto_b64 vacío).")

    entidad = {"id": pid, "nombre": nombre, "pais": pais, "foto": foto_rel, "foto_b64": foto_b64}
    datos["productores"].append(entidad)

    if args.dry_run:
        ent_mostrar = dict(entidad)
        if ent_mostrar.get("foto_b64"):
            ent_mostrar["foto_b64"] = ent_mostrar["foto_b64"][:40] + f"... ({len(entidad['foto_b64'])} chars)"
        print("\n🧪 DRY-RUN (no se ha tocado nada): entidad que se guardaría:")
        print(json.dumps(ent_mostrar, ensure_ascii=False, indent=2))
        print(f"   Total productores tras insertar: {len(datos['productores'])}")
        sys.exit(0)

    # 4. Guardar (re-sync total) + mirror local
    db_supabase.guardar_datos(datos)
    print("✅ guardar_datos() OK (re-sync total)")
    with open(os.path.join(PROY, "catas.json"), "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)
    print("✅ catas.json local sincronizado")

    # 5. Verificación releyendo Supabase
    leidos = db_supabase.cargar_datos()
    encontrado = [p for p in leidos["productores"] if p.get("id") == pid]
    if encontrado:
        ok_b64 = bool(encontrado[0].get("foto_b64"))
        if foto and not ok_b64:
            print("⚠️ Insertado pero foto_b64 vacío — revisar.")
        else:
            print(f"✅ VERIFICADO: '{nombre}' ({pais}) está en Supabase con id {pid}"
                  + (" y foto_b64" if ok_b64 else " (sin foto)"))
    else:
        print("⚠️ No se encontró tras releer — revisar logs.")

    print(f"\n📋 Total productores en Supabase: {len(leidos['productores'])}")
    print("👉 Refresca la app (terpsxhunter.streamlit.app) para verlo.")


if __name__ == "__main__":
    main()
