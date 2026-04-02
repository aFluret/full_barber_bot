/**
 * @file: SUPABASE_SERVICES_SEED.sql
 * @description: Наполнение таблицы public.services услугами из docs/TZ_MARK.
 * @created: 2026-04-02
 */

-- Безопасный повторный запуск:
-- если услуга уже есть (по уникальному name), обновим цену/длительность.
insert into public.services (name, price_byn, duration_minutes)
values
  ('Мужская стрижка', 45, 60),
  ('Мужская удлинённая', 50, 60),
  ('Детская стрижка', 40, 60),
  ('Отец + Сын', 75, 120),
  ('Комплекс', 65, 90),
  ('Тонировка бороды и усов', 20, 30),
  ('Удаление волос воском (3 зоны)', 10, 30),
  ('Оформление бороды и усов', 30, 30),
  ('Укладка волос (без стрижки)', 10, 30)
on conflict (name) do update
set
  price_byn = excluded.price_byn,
  duration_minutes = excluded.duration_minutes;

-- Проверка результата:
select id, name, price_byn, duration_minutes
from public.services
order by id;
