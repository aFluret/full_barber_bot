-- Роли users + привязка мастера к Telegram. Идемпотентно.

-- Нормализация устаревших значений
update public.users
set role = 'master'
where lower(trim(coalesce(role, ''))) = 'barber';

update public.users
set role = 'client'
where role is null or trim(role) = '';

update public.users
set role = 'client'
where lower(trim(role)) not in ('admin', 'client', 'master');

alter table public.masters
    add column if not exists telegram_user_id bigint;

alter table public.masters
    drop constraint if exists masters_telegram_user_id_key;

alter table public.masters
    add constraint masters_telegram_user_id_key unique (telegram_user_id);

alter table public.users
    drop constraint if exists users_role_check;

alter table public.users
    add constraint users_role_check
    check (role in ('admin', 'client', 'master'));

-- Назначить администратора вручную (замените user id):
-- update public.users set role = 'admin' where user_id = 123456789;
