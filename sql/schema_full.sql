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
-- =============================================================================
--  Asociaciones / Coffeeshops — tablas (añadir a sql/schema.sql)
-- =============================================================================

create table if not exists public.paises (
    id     text primary key,
    nombre text not null unique
);

create table if not exists public.ciudades (
    id      text primary key,
    nombre  text not null,
    pais_id text not null references public.paises (id) on delete cascade
);

create table if not exists public.coffeeshops (
    id         text primary key,
    nombre     text not null,
    pais_id    text references public.paises (id) on delete set null,
    ciudad_id  text references public.ciudades (id) on delete set null,
    direccion  text not null default '',
    biografia  text not null default '',
    creado     text not null default ''
);

create table if not exists public.votos_coffeeshops (
    id            bigint generated always as identity primary key,
    coffeeshop_id text not null references public.coffeeshops (id) on delete cascade,
    perfil_id     text not null references public.perfiles (id) on delete cascade,
    fecha         text not null default '',
    nota          numeric(6,2) not null default 0,
    comentario    text not null default '',
    unique (coffeeshop_id, perfil_id)
);

create table if not exists public.coffeeshop_productores (
    coffeeshop_id text not null references public.coffeeshops (id) on delete cascade,
    productor_id  text not null references public.productores (id) on delete cascade,
    primary key (coffeeshop_id, productor_id)
);

create index if not exists idx_ciudades_pais     on public.ciudades (pais_id);
create index if not exists idx_cs_ciudad         on public.coffeeshops (ciudad_id);
create index if not exists idx_votos_cs          on public.votos_coffeeshops (coffeeshop_id);
create index if not exists idx_cs_prod_prod      on public.coffeeshop_productores (productor_id);

-- =============================================================================
--  Seed data (datos iniciales por defecto)
-- =============================================================================

insert into public.paises (id, nombre) values
    ('pais_nl', 'Holanda'),
    ('pais_es', 'España'),
    ('pais_de', 'Alemania')
on conflict (id) do nothing;

insert into public.ciudades (id, nombre, pais_id) values
    ('ciud_es_bcn', 'Barcelona', 'pais_es'),
    ('ciud_es_mlg', 'Málaga',    'pais_es'),
    ('ciud_es_mad', 'Madrid',    'pais_es')
on conflict (id) do nothing;
