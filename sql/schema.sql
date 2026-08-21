-- =============================================================================
--  Sistema de Catas — Esquema Supabase (Postgres)
--  Ejecutar en: Supabase Dashboard → SQL Editor → New query → Run
--  (o automáticamente desde scripts/migrar_a_supabase.py)
-- =============================================================================

create table if not exists public.perfiles (
    id             text primary key,
    nombre         text not null unique,
    password_hash  text not null default '',
    es_confianza   boolean not null default false,
    es_admin       boolean not null default false
);

create table if not exists public.productores (
    id        text primary key,
    nombre    text not null unique,
    foto      text not null default '',
    pais      text not null default '',
    foto_b64  text not null default ''
);

create table if not exists public.catas (
    id                 text primary key,
    fecha              text not null default '',
    nombre             text not null default 'Sin nombre',
    productor          text not null default '',
    tipo               text not null default 'Flor',
    comentarios        text not null default '',
    pais               text not null default '',
    foto               text not null default '',
    anio               text not null default '',
    temporada          text not null default '',
    foto_b64           text not null default ''
);

create table if not exists public.votos (
    id                    bigint generated always as identity primary key,
    cata_id               text not null references public.catas (id) on delete cascade,
    perfil_id             text not null references public.perfiles (id) on delete cascade,
    fecha                 text not null default '',
    puntuaciones_detalle  jsonb not null default '{}'::jsonb,
    notas_bloques         jsonb not null default '{}'::jsonb,
    nota_final            numeric(6,2) not null default 0,
    unique (cata_id, perfil_id)
);

create table if not exists public.comentarios_usuarios (
    id        bigint generated always as identity primary key,
    cata_id   text not null references public.catas (id) on delete cascade,
    perfil_id text not null references public.perfiles (id) on delete cascade,
    nombre    text not null default '',
    fecha     text not null default '',
    texto     text not null default ''
);

create index if not exists idx_votos_cata       on public.votos (cata_id);
create index if not exists idx_comentarios_cata on public.comentarios_usuarios (cata_id);

-- "No lo probé": un usuario descarta una cata (no la probó en esta ronda).
-- La caducidad visual (30 días) se calcula sobre catas.fecha (fecha de alta),
-- así que no necesita columna extra.
create table if not exists public.descartes_usuarios (
    id        bigint generated always as identity primary key,
    cata_id   text not null references public.catas (id) on delete cascade,
    perfil_id text not null references public.perfiles (id) on delete cascade,
    fecha     text not null default '',
    unique (cata_id, perfil_id)
);

create index if not exists idx_descartes_cata on public.descartes_usuarios (cata_id);

-- Identidades OAuth ("Continuar con Google"): vínculo proveedor↔perfil local.
-- El login tradicional (usuario/contraseña) es complementario, no se sustituye.
create table if not exists public.identidades_oauth (
    id        bigint generated always as identity primary key,
    proveedor text not null,
    sub       text not null,
    email     text not null default '',
    perfil_id text not null references public.perfiles (id) on delete cascade,
    unique (proveedor, sub)
);

create index if not exists idx_oauth_perfil on public.identidades_oauth (perfil_id);
