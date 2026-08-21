-- =============================================================================
--  Sesión persistente (cookie "recordar sesión") + Google OAuth
--  MIGRACIÓN para la BD EXISTENTE → Supabase → SQL Editor → Run (una vez).
--  Para entornos nuevos ya está incluido en sql/schema.sql.
-- =============================================================================

-- Identidades OAuth: vincula un proveedor (p. ej. Google) a un perfil local.
-- El login tradicional (usuario/contraseña) no se toca: es complementario.
create table if not exists public.identidades_oauth (
    id        bigint generated always as identity primary key,
    proveedor text not null,
    sub       text not null,
    email     text not null default '',
    perfil_id text not null references public.perfiles (id) on delete cascade,
    unique (proveedor, sub)
);

create index if not exists idx_oauth_perfil on public.identidades_oauth (perfil_id);

-- Permisos para el rol de la app (ajustar el nombre si cambia)
grant all on public.identidades_oauth to catas_app2;
grant usage, select on sequence public.identidades_oauth_id_seq to catas_app2;
