-- Персональный обед мастера (совпадает по длительности с глобальным графиком: 60 мин).
alter table public.masters
    add column if not exists lunch_time time;
