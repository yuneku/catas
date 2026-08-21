-- =============================================================================
--  "Por votar": caducidad visual (30 días) + descartes "No lo probé"
--  MIGRACIÓN para la BD EXISTENTE → Supabase → SQL Editor → Run (una vez).
--  Para entornos nuevos ya está incluido en sql/schema.sql.
-- =============================================================================

create table if not exists public.descartes_usuarios (
    id        bigint generated always as identity primary key,
    cata_id   text not null references public.catas (id) on delete cascade,
    perfil_id text not null references public.perfiles (id) on delete cascade,
    fecha     text not null default '',
    unique (cata_id, perfil_id)
);

create index if not exists idx_descartes_cata on public.descartes_usuarios (cata_id);

-- Permisos para el rol de la app (ajustar el nombre si cambia)
grant all on public.descartes_usuarios to catas_app2;
grant usage, select on sequence public.descartes_usuarios_id_seq to catas_app2;
