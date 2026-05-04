-- Stage 2.2: branches + masters + appointment links
-- Safe for repeated execution.

create table if not exists public.branches (
    id bigserial primary key,
    name text not null unique,
    address text not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.masters (
    id bigserial primary key,
    master_key text not null unique,
    name text not null unique,
    work_start time not null,
    work_end time not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.master_branches (
    master_id bigint not null references public.masters(id) on delete cascade,
    branch_id bigint not null references public.branches(id) on delete cascade,
    primary key (master_id, branch_id)
);

create table if not exists public.master_services (
    master_id bigint not null references public.masters(id) on delete cascade,
    service_id bigint not null references public.services(id) on delete cascade,
    primary key (master_id, service_id)
);

alter table public.appointments
    add column if not exists branch_id bigint references public.branches(id) on delete set null,
    add column if not exists master_id bigint references public.masters(id) on delete set null,
    add column if not exists branch_name text,
    add column if not exists master_name text,
    add column if not exists master_key text,
    add column if not exists comment text;

create index if not exists idx_appointments_master_key_date_status
    on public.appointments (master_key, date, status);

create index if not exists idx_appointments_branch_id on public.appointments (branch_id);
create index if not exists idx_appointments_master_id on public.appointments (master_id);

insert into public.branches(name, address)
values
    ('Рокоссовского 145', 'Рокоссовского 145'),
    ('Плеханова 68', 'Плеханова 68')
on conflict (name) do update set
    address = excluded.address,
    is_active = true;

insert into public.masters(master_key, name, work_start, work_end)
values
    ('ilya', 'Илья', '10:00', '18:00'),
    ('maksim', 'Максим', '09:00', '17:00'),
    ('zhenya', 'Женя', '12:00', '20:00')
on conflict (master_key) do update set
    name = excluded.name,
    work_start = excluded.work_start,
    work_end = excluded.work_end,
    is_active = true;

insert into public.master_branches(master_id, branch_id)
select m.id, b.id
from public.masters m
cross join public.branches b
on conflict do nothing;

insert into public.master_services(master_id, service_id)
select m.id, s.id
from public.masters m
cross join public.services s
on conflict do nothing;

