# 🌿 Sistema de Catas de Cannabis — RESUMEN PARA NUEVA CONVERSACIÓN

> Documento de contexto para continuar el proyecto desde cero. Leer completo antes de trabajar.

## 1. ¿Qué es?

App web multi-usuario (mobile-first) para registrar **catas de cannabis**: votar por bloques
(Visual 25% / Aroma 15% / Sabor 45% / Efectos 15%), notas sobre 100 (se guardan /10),
rankings (general, personal y profesional), evolución por año/temporada, productores con
foto/país, **asociaciones/coffeeshops** (directorio geolocalizado con votos, menú por
productor, fotos y ranking global) y perfiles con 4 rangos:
**invitado** (ve todo, login al actuar) < **sesión** (vota) < **confianza 🤝** (comenta;
su voto alimenta la nota profesional) < **admin 👑** (todo; `p_default` admin raíz).

## 2. Arquitectura (IMPORTANTE — estructura real, no hay db_manager.py)

| Archivo | Función |
|---|---|
| `app_streamlit.py` | App web (vistas, login, secciones, CSS mobile-first, caché) |
| `app_datos.py` | **Backend puro SIN tkinter** (datos, notas, votos, coffeeshops) — importable en la nube |
| `app_catas.py` | Escritorio CustomTkinter (re-exporta app_datos + GUI). NO se toca para la web |
| `db_supabase.py` | Driver Postgres/Supabase (misma API que el JSON: `activo()`, `cargar_datos()`, `guardar_datos()`) |
| `sql/schema.sql` + `sql/coffeeshops.sql` | Esquema Postgres completo |
| `sql/por_votar.sql` | Migración BD existente: tabla `descartes_usuarios` ("No lo probé") |
| `sql/oauth.sql` | Migración BD existente: tabla `identidades_oauth` (Google OAuth) |
| `scripts/migrar_a_supabase.py` | Migración catas.json → Supabase (incrusta fotos como miniaturas b64) |
| `panel_catas.py` + `panel.bat` | Panel de control local (estado del túnel, URLs) |
| `arrancar_web.py` / `.bat` | Servidor local bindeado a Tailscale |

**Regla de oro del guardado en Supabase**: `guardar_datos` hace **re-sync TOTAL**
(DELETE de hijos → DELETE de TODAS las entidades → INSERT de todo, en transacción).
Las entidades eliminadas del dict DESAPARECEN de la BD. FKs vacías → NULL (`or None`).

**Regla de oro de st.secrets en Cloud**: `st.secrets["supabase"]` devuelve un objeto
Mapping que NO es `isinstance(dict)` ni soporta bien `.get()` → usar SIEMPRE
`dict(st.secrets["supabase"])`. Y la caché de `cargar()` (st.cache_data) puede servir
vacíos: auto-recuperación `cargar.clear()` + `rerun` si la BD tiene datos.

## 3. Dependencias

- **requirements.txt**: `streamlit>=1.37`, `pandas>=2.0`, `Pillow>=10.0`, `psycopg2-binary>=2.9`
- Local (PC): Python 3.11.15 (venv hermes), Streamlit 1.61.1, customtkinter 6.0.0,
  ffmpeg en PATH, cloudflared en `C:\Program Files (x86)\cloudflared\`
- El repo **NO contiene** `catas.json` (hashes de contraseñas) ni `imagenes/`
  (las fotos viajan como b64 en la BD); las fotos de las catas ya están incrustadas

## 4. Acceso a la web (PRODUCCIÓN)

- **App pública**: `https://terpsxhunter.streamlit.app` (Streamlit Community Cloud, desplegada
  desde GitHub; redeploy automático con cada push a `main`)
- **Repo GitHub**: `https://github.com/yuneku/catas` (PÚBLICO — requisito: Streamlit Cloud
  NO despliega repos privados sin OAuth; NO subir secretos al repo)
- **Base de datos Supabase**: proyecto "yuneku's Project" (ref `tznjunbhebzgnzhxlrkm`,
  región eu-west-1, Postgres 17). Conexión: rol `catas_app2` + pooler
  `aws-1-eu-west-1.pooler.supabase.com:5432` (el host directo `db.<ref>.supabase.co`
  es IPv6-only). **Secretos en Settings → Secrets de Streamlit** (`[supabase] url=...`),
  NUNCA en el repo. La password de la BD está en `C:\Users\Yunes\Desktop\Catas\.env_db_password`
  y el secrets local en `Catas\.streamlit\secrets.toml`
- **Acceso local (PC)**: Tailscale `http://100.67.184.36:8501` (bind solo a Tailscale,
  localhost NO responde); túnel público rápido Cloudflare (cambia en cada reinicio) vía
  `arrancar_publico.bat` o el panel (verifica HTTP real, los túneles quick caducan)

## 5. Estado actual (datos en Supabase)

- **9 catas** (Strawberry HashHut, MelaVerde, Z-Uncle...) con votos, comentarios y notas
- **12 productores** (foto: HashHut, DoctorOfMontain, Zkittles)
- **3 perfiles**: Yunes 👑 (p_default), sosio, Cheluariasmoh — registro abierto
- **Asociaciones**: tablas listas (países: Alemania, España, Holanda, Portugal;
  ciudades: Barcelona, Madrid, Málaga) — sin coffeeshops creados aún
- Nota de referencia: Strawberry **62.6 general** (2 votos) — los 66.3/70.7 del histórico
  eran de una versión anterior con perfil de prueba eliminado

## 6. Patrones críticos al modificar

- **Backup ANTES de tocar**: `app_*_backup_pre_*.py` + QA con backup/restore del JSON
- **HTML inyectado**: UNA SOLA LÍNEA (sin `\n`) — indentación desigual o `dedent` mal
  aplicado → `<pre><code>` (HTML en crudo)
- **QA**: AppTest (`at.session_state` sin `.get()`, clics reales, keys de widgets únicas
  por tab), borrar `_qa_*.py` y `__pycache__` al final
- **Enrutamiento**: `st.session_state` (`ficha_id`, `cs_ficha`, `pagina` + `st.rerun()`)
- **Tema oscuro** (#161A20 tarjetas, #E8E6E1 texto, acentos #8AB4F8); notas con
  `core.color_nota(nota/10)`

## 7. Próximos pasos posibles (pendientes del usuario)

- Coffeeshops de ejemplo con fotos y productores vinculados (panel admin)
- Ranking de asociaciones en la sección "🏆 Rankings" principal
- Verificar/corregir que el "Ver Asociación" cruce con el catálogo (ya enruta a ficha)
- Migración a `width='stretch'` (deprecación de `use_container_width` — funciona hoy)

## 7b. Por votar (v2 — caducidad + descartes, agosto 2026)

- **Caducidad visual 30 días**: los productos con `fecha` (alta) > 30 días se
  archivan al Catálogo (filtro en `seccion_por_votar` con `core.es_reciente`,
  `DIAS_CADUCIDAD = 30` en app_streamlit.py). Fecha dinámica: `datetime.now()`.
- **Descartes "No lo probé"**: tabla `descartes_usuarios` (cata_id, perfil_id,
  fecha, unique por par). Backend: `app_datos` sección 2c (`descartar_cata`,
  `quitar_descarte`, `ids_descartados_por`, `descartado_por`). La capa Supabase
  hace DELETE+INSERT de descartes en el re-sync total (como el resto de entidades).
- **UI**: grid 3-2-1 (CSS compartido con el catálogo), tarjeta con badge
  "⏳ N días" (verde/ámbar/rojo) y botones "🗳 Votar" + "🙈 No lo probé"
  (st.toast + rerun). Formulario a ancho completo con 4 pestañas
  (👁️👃👅✨) en `render_sliders_blocks` — elimina los expanders anidados;
  nota en vivo + "Guardar mi voto" sticky al fondo en móvil
  (`.st-key-pv_nota_guardar`).
- **OJO permisos**: `catas_app2` NO es owner de `catas` (ALTER imposible) →
  la caducidad usa `fecha` (fecha de alta, no editable en la ficha), sin columna
  nueva. La tabla nueva la crea el propio rol app (owner) → sin necesidad de postgres.

## 7c. Sesión persistente + Google OAuth (agosto 2026)

- **Realidad**: la app NO usa Supabase Auth (solo Postgres vía psycopg2). El
  login es propio (password_hash PBKDF2 en `perfiles`). Por eso:
  - Cookie "Recordarme" = token propio `<perfil_id>.<expiry>.<hmac_sha256>`
    (funciones `core.crear_token_sesion`/`verificar_token_sesion`, secreto en
    `st.secrets["session"]["secret"]` o env `SESSION_SECRET`). Librería
    `streamlit-cookies-controller` (JS, no HttpOnly: firma HMAC evita forjar;
    `_auto_login_cookie()` en main()). Sin secreto → no se emite cookie (no rompe).
  - Google = OAuth 2.0 puro (Authorization Code + PKCE) contra Google:
    `_url_autorizacion_google()` (redirect_uri = base URL + "/", state + verifier
    en session_state), `_intercambiar_codigo_google()` (token → userinfo),
    `_manejar_retorno_oauth()` captura `?code=`/`?state=` y limpia query_params.
  - Vínculos en tabla `identidades_oauth` (proveedor, sub, email, perfil_id);
    primer login con Google crea perfil automáticamente (sin password).
- **Regla estricta cumplida**: login/registro tradicional intacto (solo se
  añadieron `autocomplete="username/current-password/new-password"` y el
  checkbox "Recordarme"; el registro y el login emiten cookie al entrar).
- **Secrets necesarios en Streamlit Cloud** (Settings → Secrets):
  ```toml
  [session]
  secret = "<token_hex_32>"
  [google_oauth]
  client_id = "...apps.googleusercontent.com"
  client_secret = "..."
  ```
  O en `.env_google_oauth` local (`client_id=...` / `client_secret=...`).
  Redirect URI a registrar en Google: `https://terpsxhunter.streamlit.app/`.
- **PITFALL**: al reescribir `.streamlit/secrets.toml` NUNCA inventar la URL:
  reconstruir SIEMPRE con la password real de `.env_db_password`
  (`postgresql://catas_app2.<ref>:<pw>@aws-1-eu-west-1.pooler.supabase.com:5432/postgres`).

## 7d. Optimización de rendimiento (agosto 2026)

- **Caché**: `cargar()` ya era `@st.cache_data` → ahora `ttl=300` (5 min) +
  `clear()` en `guardar()` (instantáneo). `st.cache_data` devuelve COPIAS por
  sesión (pickle): mutar el dict NO contamina la caché ni otras sesiones
  (verificado en laboratorio). Rankings/Evolución/Asociaciones NO hacen
  SELECTs propios: operan sobre el mismo dict cacheado → una sola lectura de
  Supabase cada 5 min como máximo.
- **st.form en votación**: `formulario_voto` envuelve tabs+sliders+comentarios
  +nota en `with st.form(key=f"form_voto_{id}")` → mover sliders = 0 recargas.
  La nota se recalcula con el botón "👁 Ver mi nota" (submit); "🙈 No lo probé"
  es otro form_submit_button. TRADE-OFF: la nota ya no es 100% en vivo (el form
  impide el rerun por slider) — es el coste de cero recargas.
- **st.fragment**: `lista_por_votar` (grid completo: el cálculo de pendientes
  vive DENTRO para que la tarjeta desaparezca) y `seccion_comentarios`.
  PITFALLS descubiertos:
  - El render del fragmento usa el estado del INICIO del rerun: mutar datos en
    el mismo pase no refresca la vista → hace falta `st.rerun(scope="fragment")`
    explícito tras mutar (segundo pase).
  - `st.rerun(scope="fragment")` lanza StreamlitAPIException en AppTest (el
    click no dispara fragment rerun aislado) → envuelto en try/except (en el
    navegador nunca lanza). En AppTest, un run extra o un AppTest nuevo equivale
    al segundo pase.
  - NO se puede modificar session_state de un widget ya instanciado en el mismo
    run → limpiar un text_area requiere flag + pop ANTES de instanciarlo.
- **GUARDAR OPTIMIZADO (db_supabase, ago 2026)**: el re-sync fila a fila
  (~100 round-trips, 5.7 s) se sustituyó por DELETE condicional de entidades
  (solo las que ya no están en el dict) + UPSERT en BATCH (execute_values, 1
  round-trip por tabla) + fotos b64 re-subidas SOLO si cambiaron (md5 calculado
  en el servidor, CASE conserva la existente) → **~1.0 s** verificado con datos
  reales y rollback. Semántica idéntica al re-sync total (lo eliminado
  desaparece); NO cambia la estructura de la BD.
- **NO optimizado a propósito**: `_df_notas` (evolución) sin cachear (args
  enormes → hashing caro); datos en session_state NO (rompe multi-usuario);
  payload de SELECT (foto_b64) sin tocar (el re-sync total lo necesita).

## 8. Credenciales

- **GitHub**: usuario `yuneku` (gh CLI autenticado en el PC)
- **Supabase**: access token en `~/.supabase/access-token` (CLI); login con `--agent no`
- **Streamlit**: cuenta de GitHub del usuario (login web)
- No hay API keys en el proyecto; los secrets de BD van solo por Settings de Streamlit
