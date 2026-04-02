/**
 * @file: SUPABASE_WORK_SCHEDULE_LUNCH_MIGRATION.sql
 * @description: Добавляет единое поле обеда (lunch_time) в public.work_schedule.
 * @created: 2026-04-02
 */

alter table public.work_schedule
  add column if not exists lunch_time time null;

-- Если ранее создавались lunch_start/lunch_end — переносим start в единое поле.
do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public' and table_name = 'work_schedule' and column_name = 'lunch_start'
  ) then
    execute 'update public.work_schedule set lunch_time = lunch_start where lunch_time is null and lunch_start is not null';
  end if;
end $$;

-- Техническая проверка:
select id, weekdays, start_time, end_time, lunch_time, created_at
from public.work_schedule
order by created_at desc
limit 5;
