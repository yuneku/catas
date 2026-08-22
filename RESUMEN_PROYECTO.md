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
- **HTML inyectado**: UNA SOLA LÍNEA (sin `
`) — indentación desigual o `dedent` mal
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
- **POOL DE CONEXIONES (db_supabase, ago 2026)**: ThreadedConnectionPool
  (1-4) con validación SELECT 1 y reconexión automática. El handshake SSL al
  pooler se paga una vez → cargar 0.69 s / guardar 0.72 s en pool caliente
  (verificado con datos reales). `_devolver()` sustituye a conn.close().
- **SCROLL AL INICIO al cambiar de vista**: en main(), si la clave de vista
  (pagina, ficha_id, votar_id) cambia, se inyecta un st.html con
  `scrollTo(0)` (los scripts de st.html SÍ se ejecutan, a diferencia de
  st.markdown). Solo se inyecta al cambiar de vista, no en cada rerun.
- **FRAGMENTS GLOBALES (ago 2026)**: aplicado el patrón de recarga parcial a
  toda la app: `_catalogo_grid` (filtros+grid: cambiar filtro = rerun parcial),
  `_tarjeta_productor` (renombrar/foto/eliminar parciales), `_tarjeta_perfil`
  (toggles de rango, contraseña, eliminar, renombrar parciales) y
  `_votacion_cs` (valorar local parcial). Las acciones de CAMBIO DE VISTA
  (abrir ficha, volver) siguen reruneando la app. Regla de oro del fragmento
  con borrado: check de existencia al inicio (`if not any(...): return`).
  PITFALL verificado: st.dialog SÍ puede llamarse dentro de st.fragment.
- **IMÁGENES LIGERAS (ago 2026)**: en modo nube las tarjetas incrustaban el
  b64 ORIGINAL (31 KB medio por tarjeta → grid de 812 KB). Ahora `foto_base64`
  y `mostrar_foto` generan thumbs en cliente (`_foto_b64_thumb`/`_foto_b64_redim`,
  memoizados por md5+px, JPEG q80): 3.7 KB por tarjeta (grid 95 KB, -88%).
  `guardar_foto_upload` optimiza las subidas (max 1200px JPEG q82; RGBA → fondo
  #161A20). Migración de las 64 fotos existentes a max 600px (backup previo en
  backups/fotos_originales_*.json; ahorro BD 8% porque ya eran ligeras).
  PITFALL: fallback seguro a b64 original si PIL falla (nunca rompe el render).
- **RONDA 4 (ago 2026)**: rebranding a **TerpsXHunter** (page_title, sidebar,
  pantalla de acceso). "➕ Nueva Cata" ahora visible para ADMIN + GENTE DE
  CONFIANZA (`es_profesional` en `paginas_para` y en `seccion_nueva_cata`; el
  usuario normal ni la ve en su menú — el sidebar le redirige al Catálogo).
  Paginación "⬇️ Mostrar más" en Catálogo y Por votar (arranca en 12 tarjetas;
  botón en el fragmento → amplía con recarga parcial; claves cat_n / pv_n).
  Límite de memoria en _THUMBS (600 entradas, se vacía y regenera).
- **MEJORAS UX (ago 2026)**: (A) expander "🙈 Descartadas (N) — recuperar" en
  Por votar (botón ↩ por cata, quita el descarte con recarga parcial); (B)
  selector de orden en Por votar (⭐ Mejor nota / 🆕 Más reciente / 🔤
  Alfabético; vive en el fragmento → reordenar es recarga parcial); (C) filtro
  "Mi voto" en el Catálogo (Todos / Sin votar / Ya votado, solo con sesión).
  PITFALL AppTest: reutilizar un objeto widget entre runs devuelve un objeto
  stale — refrescar SIEMPRE `next(p for p in at.pills if p.key == ...)`.
- **NO optimizado a propósito**: `_df_notas` (evolución) sin cachear (args
  enormes → hashing caro); datos en session_state NO (rompe multi-usuario);
  payload de SELECT (foto_b64) sin tocar (el re-sync total lo necesita).

## 8. Credenciales

- **GitHub**: usuario `yuneku` (gh CLI autenticado en el PC)
- **Supabase**: access token en `~/.supabase/access-token` (CLI); login con `--agent no`
- **Streamlit**: cuenta de GitHub del usuario (login web)
- No hay API keys en el proyecto; los secrets de BD van solo por Settings de Streamlit
- **MEJORAS UX 2 (ago 2026)** — sensación de "no se pulsa bien":
  (A) Tarjetas del Catálogo 100% clicables: el botón 'Abrir' es un overlay
  invisible (position:absolute; opacity:0) que cubre TODA la tarjeta → tocar
  foto/nombre/chips abre la ficha sin zona muerta; el 🗑 del admin queda por
  encima (z-index) y sigue pulsable. (B) Tarjetas 'Por votar': botones
  apilados a ancho completo (🗳 Votar + 🙈 No lo probé) en vez del [2,1]
  apretado. (C) Botones min-height 48px. (D) Menú ☰ móvil con botón ✕ para
  cerrar. (E) Login-check sidebar: el diagnóstico de BD directa se lee UNA vez
  (flag _diag_supabase) para no alargar cada rerun cuando la caché viene vacía.
- **REDISEÑO UI + BACKEND (ago 2026)**:
  UI: tipografía Plus Jakarta Sans, fondo con degradados radiales, scrollbar y
  selección personalizadas, encabezados de sección con acento (barra verde +
  peso 800), botones primarios en degradado, tarjetas con elevación/transición
  al pasar el ratón, métricas (st.metric) en tarjeta de vidrio, pestañas de
  voto con estado activo resaltado, y banda de marca `hero()` en la parte
  superior (título + nº de catas + estado 🟢/💾 + usuario) centrada sin texto
  técnico. BACKEND: `guardar()` ahora reintenta una vez ante errores
  transitorios de red/pooler, solo invalida la caché en éxito, devuelve bool y
  muestra un toast de error si no se pudo guardar (antes el fallo pasaba
  callado). No se tocó la conexión a Supabase ni el login.
- **FIX CLIQUEO TARJETAS (ago 2026, verificado en navegador real)**: en
  Streamlit 1.61 NO existe `data-testid="stVerticalBlockBorderWrapper"` (era de
  versiones viejas), así que el `position:relative` del overlay era un no-op y el
  botón 'Abrir' (posicionado absolute) se estiraba a TODO el área de contenido
  (1092x605), interceptando clics. Solución: dar `key="card_<id>"` al contenedor
  de cada tarjeta (Catálogo y Por votar) para usarlo como ancestro posicionado,
  y hacer que el <button> del overlay llene la tarjeta (`inset:0;width:100%;
  height:100%`). Se verificó en navegador real que el clic sobre la tarjeta abre
  la ficha (falla previa del click_at_xy era por coordenada, no por el mecanismo).
  LECCIÓN: en Streamlit 1.61 los contenedores con borde son `stVerticalBlock`
  (con `st-key-*`), NO `stVerticalBlockBorderWrapper`.
- **BACKEND: guard multi-usuario contra pérdida de datos (ago 2026)**:
  nueva tabla `meta_estado` (id=1, version, updated_at) creada en Supabase.
  `cargar_datos()` incluye `_version` en los datos; `guardar_datos()` bloquea
  (SELECT FOR UPDATE) la fila, y si la versión del cliente difiere de la BD
  lanza `ErrorVersionAntigua` ANTES de escribir nada (rollback). La app lo
  captura: toast "🔄 Otro usuario guardó cambios más recientes", limpia caché
  y recarga (nadie pisa votos ajenos en silencio). El bump de versión va en la
  MISMA transacción que los datos. Degradación elegante si la tabla no existe.
  Además: `connect_timeout=15` en el pool (si el pooler no responde, falla en
  15 s en vez de colgar el rerun). Verificado: re-sync idempotente (35 catas,
  28 productores, 3 perfiles, 11 votos intactos tras el guardado de prueba).
- **RANKINGS + backend (ago 2026, revisado con visión local)**: (a) el botón
  'Ver ficha' de cada fila de ranking (Top General/Personal/Confianza) pasó de
  colgar a ancho completo DEBAJO de la fila a una 4ª columna compacta integrada
  ('👁 Ver', min-height 40px) que en móvil apila a ancho completo (CSS
  rk_lista flex-wrap) — filas más bajas y alineadas; (b) más aire entre filas
  (gap 0.55rem), captions de productor/votos más legibles (12.5px #A5AEB8);
  (c) podium: fotos con esquinas redondeadas, 'N votos' 11px más legible,
  padding interno mayor. (d) Backend: statement_timeout=30s por conexión (el
  pooler Supavisor IGNORA options de libpq → se hace SET en runtime al coger
  cada conexión del pool; sobrevive a los resets del pool). Desplegado en
  1d65481 (Rankings) y eec5ac4 (timeout).
- **EVOLUCIÓN + theme global (ago 2026)**: (a) listas de catas del gráfico y
  del ranking por época agrupadas en TARJETAS (container border con gap 0.5rem)
  y conteo en negrita; (b) .streamlit/config.toml alineado al tema (base dark,
  primaryColor #4ADE80, backgroundColor #0E1116, secondaryBackgroundColor
  #171C23, textColor #EAF0F6) para que los CHARTS (line_chart/bar_chart) y
  widgets nativos usen fondo oscuro coherente (antes renderizaban con el tema
  claro/default → desentonaban); (c) TTL de cargar() 300->120s (más frescura
  multi-usuario). Desplegado en b5f1f33. QA AppTest 3/3.
- **ACABADO PROFESIONAL + núcleo (ago 2026)**: Design System v2 global
  (oscuro + verde neón): fondo con 3 degradados radiales + más profundidad,
  sidebar con degradado vertical y navegación en filas limpias (hover, opción
  activa resaltada), tabs con subrayado verde y hover, inputs/textarea con
  focus ring verde, botones con micro-transición (lift), pills activos con más
  presencia, expanders y avisos en tarjeta suave, métricas de vidrio. Aplica a
  TODAS las secciones vía CSS global. Dato clave de Evolución: solo 10/35 catas
  tienen voto (la nota requiere votos) → la sección parecía vacía; se añadió un
  chart de ACTIVIDAD (catas registradas por año, TODAS) antes del de notas.
  Desplegado en 33d8818. QA AppTest 7/7.
- **DESIGN SYSTEM v3 (ago 2026)**: la CSS acumulada en capas (250+ líneas con
  parches de parches, degradados y valores contradictorios — el botón primario
  estaba definido 3 veces con 3 degradados, pills con 2 juegos, h2 con 2
  tamaños) se consolidó en UN único sistema visual coherente (~24 secciones,
  -368 líneas): paleta unificada (oscuro #0A0E14 + verde neón #4ADE80),
  tipografía Plus Jakarta Sans aplicada a encabezados/markdown/widgets
  (antes los h2 iban en Source Sans → inconsistencia), grids 3-2-1, tarjetas
  con elevación/hover/overlay, sticky CTA, focus ring verde, sidebar con
  degradado. Conserva TODOS los selectores funcionales (st-key-*, overlay de
  tarjeta clicable, grid, sticky). Verificado OBJETIVAMENTE en producción:
  acento #4ADE80 en h2 ✓, input #171D27 ✓, Plus Jakarta en body/h2/input ✓,
  12 tarjetas + 15 pills renderizados ✓. QA AppTest 10/10 (contenido, no solo
  ausencia de excepciones). Desplegado en c6f3295 (DSv3) + 65a5b47
  tipografía encabezados).
- **CHARTS EN VERDE + FICHA REORDENADA (ago 2026)**: Streamlit NO usa el
  primaryColor en los charts (usa su paleta default AZUL), lo que rompía el
  sistema verde de Evolución. Se pasa `color` explícito (bar_chart y line_chart)
  con `_PALETA_CHART` (verde neón #34D97B + violeta) — verificado objetivamente
  en producción (`rgb(52,217,123)`). La ficha de presentación se reordenó: el
  título del producto y la nota grande van ARRIBA (antes la foto enorme
  enterraba el título); imagen en tarjeta con altura contenida; cajas de
  Votaciones/Comentarios pulidas (tarjeta con hover, menos "post de foro").
  QA AppTest 6/6. Desplegado en faea2c2.
- **FOTOS EN LA FICHA/RANKING + CSS BASE CAJAS (ago 2026)**: bug real — en modo
  nube la foto del producto es un b64 de la BD (no un archivo local), así que
  `ficha_premium` y las filas de ranking usaban `st.image(resolver_ruta_foto(...))`
  sin el b64 → caían al placeholder verde. Se pasó a `foto_base64`/`foto_base64_fluid`
  (que omiten el archivo y usan el b64). Verificado objetivamente: la ficha ahora
  muestra un `<img src="data:image/jpeg;base64,...">` de 46KB a 420×320. Además se
  unificó un CSS BASE para TODOS los contenedores con borde (`stVerticalBlockBorderWrapper`)
  que antes heredaban el borde gris por defecto de Streamlit (leía como "post de foro").
  Revisadas visualmente también login y perfiles (professionales). QA AppTest 7/7.
  Desplegado en 40ecbb3.
