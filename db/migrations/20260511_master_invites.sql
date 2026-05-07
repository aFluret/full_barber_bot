-- Одноразовые приглашения мастеров (ссылка https://t.me/<bot>?start=mi_<token>)

create table if not exists public.master_invites (
    id bigserial primary key,
    token text not null unique,
    hint_name text,
    expires_at timestamptz not null,
    used_at timestamptz,
    used_by_user_id bigint,
    master_id bigint references public.masters(id) on delete set null,
    created_by_user_id bigint not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_master_invites_token_open
    on public.master_invites (token)
    where used_at is null;
