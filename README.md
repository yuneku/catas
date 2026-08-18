# 🌿 Sistema de Catas de Cannabis

App web multi-usuario (Streamlit, mobile-first) para registrar catas, votar por
bloques (Visual / Aroma / Sabor / Efectos), consultar rankings y evolución,
gestionar productores y perfiles con jerarquía de rangos:

| Rango | Puede |
|---|---|
| 👀 Invitado | Ver las 7 secciones; login forzado para actuar |
| 🔓 Sesión | Votar |
| 🤝 Gente de confianza | Comentar + su voto alimenta la **nota profesional** |
| 👑 Admin | Todo (editar catas, productores, perfiles) |

## Modos de ejecución

### 1) Local (Windows + Tailscale) — datos en `catas.json`
```bash
python arrancar_web.py        # servidor bindeado a la IP Tailscale (100.x:8501)
python arrancar_publico.bat   # + túnel público Cloudflare y panel de control
```

### 2) Nube (Streamlit Community Cloud) — datos en Supabase (Postgres)
El filesystem de la nube es **efímero**: si hay `SUPABASE_URL` configurada, la
app lee/escribe en Postgres automáticamente (misma interfaz). Sin ella, usa
`catas.json` (solo útil como modo local/demo).

## Despliegue en Streamlit Community Cloud

1. **Supabase** (https://supabase.com → New project):
   - Copia la connection string: *Project Settings → Database → Connection
     string → URI*.
   - Crea las tablas: *SQL Editor* → pega el contenido de `sql/schema.sql` →
     Run.
2. **Migra tus datos actuales** (desde este PC):
   ```bash
   set SUPABASE_URL=postgresql://postgres.TU-PROYECTO:TUPASS@.../postgres
   pip install psycopg2-binary
   python scripts/migrar_a_supabase.py
   ```
   Verás la verificación de conteos (perfiles/productores/catas).
3. **GitHub**: sube el proyecto (incluye `imagenes/` si quieres conservar las
   fotos actuales). **Ojo**: `catas.json` está en `.gitignore` porque contiene
   hashes de contraseñas — los datos reales viven en Supabase.
4. **Streamlit Cloud** (https://share.streamlit.io):
   - *Create app* → conecta tu repo de GitHub → rama `main` → archivo
     `app_streamlit.py` → Deploy.
   - *Settings → Secrets* → pega:
     ```toml
     [supabase]
     url = "postgresql://postgres.TU-PROYECTO:TU-PASSWORD@aws-0-TU-REGION.pooler.supabase.com:5432/postgres"
     ```
   - *Manage app → Reboot* para que tome los secretos.
5. ¡Listo! Tu app vive en `https://<tu-app>.streamlit.app`.

## Notas

- **Fotos**: las imágenes de `imagenes/` viajan con el repo. Las subidas nuevas
  en la nube se guardan como base64 en la propia base de datos (columna
  `foto_b64`), así no dependen del filesystem.
- **Backups**: en local, `guardar()` hace copia automática en `backups/`
  (rotación 15). En la nube, la base de datos es tu respaldo.
- **Escritorio**: `app_catas.py` conserva la versión CustomTkinter (3 capas,
  notas /100); la web usa el mismo backend (`import app_catas as core`).

## Estructura

```
app_streamlit.py        App web (7 secciones, UI mobile-first)
app_catas.py            Core de datos/lógica (backend puro + vistas escritorio)
db_supabase.py          Capa Supabase/Postgres (activa con SUPABASE_URL)
sql/schema.sql          Esquema de la base de datos
scripts/migrar_a_supabase.py  Migración catas.json → Supabase
panel_catas.py          Panel de control de escritorio (estado/túnel)
arrancar_web.py(.bat)   Servidor local bindeado a Tailscale
arrancar_publico.bat    Servidor + túnel Cloudflare + panel
.streamlit/config.toml  Tema oscuro (portátil)
```
# Fuerza redeploy para recargar secrets
