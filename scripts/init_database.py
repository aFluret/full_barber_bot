#!/usr/bin/env python3
"""
Применяет SQL-миграции из db/migrations к PostgreSQL (Supabase direct connection или локальный Postgres).

Требуется клиент psql в PATH и переменная окружения DATABASE_URL, например:
  postgresql://postgres:ПАРОЛЬ@db.<project-ref>.supabase.co:5432/postgres

Для пустого локального кластера сначала создайте БД (один раз):
  createdb -h localhost -U postgres barber_mark
  set DATABASE_URL=postgresql://postgres:...@localhost:5432/barber_mark

Опционально после миграций — справочник услуг:
  python scripts/init_database.py --with-services-seed

Не храните пароли в аргументах командной строки; используйте .env только у себя локально.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _migration_files(migrations_dir: Path) -> list[Path]:
    files = sorted(migrations_dir.glob("*.sql"))
    return [p for p in files if p.is_file()]


def _run_psql(database_url: str, sql_file: Path) -> None:
    psql = shutil.which("psql")
    if not psql:
        sys.stderr.write(
            "Не найден psql. Установите PostgreSQL client tools "
            "или добавьте psql в PATH.\n"
        )
        sys.exit(1)
    cmd = [
        psql,
        database_url,
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(sql_file),
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Инициализация схемы БД: поочерёдно выполняет db/migrations/*.sql",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=None,
        help="Каталог с .sql (по умолчанию <корень>/db/migrations)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Строка подключения Postgres; иначе берётся из DATABASE_URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать порядок файлов, без выполнения",
    )
    parser.add_argument(
        "--with-services-seed",
        action="store_true",
        help="После миграций выполнить docs/SUPABASE_SERVICES_SEED.sql",
    )
    args = parser.parse_args()

    root = _repo_root()
    migrations_dir = args.migrations_dir or (root / "db" / "migrations")
    if not migrations_dir.is_dir():
        sys.stderr.write(f"Нет каталога миграций: {migrations_dir}\n")
        sys.exit(1)

    database_url = args.database_url or os.environ.get("DATABASE_URL", "").strip()
    if not args.dry_run and not database_url:
        sys.stderr.write(
            "Укажите DATABASE_URL или передайте --database-url "
            "(строка подключения PostgreSQL).\n"
        )
        sys.exit(1)

    files = _migration_files(migrations_dir)
    if not files:
        sys.stderr.write(f"В {migrations_dir} нет .sql файлов.\n")
        sys.exit(1)

    if args.dry_run:
        print("Порядок применения:")
        for p in files:
            print(f"  {p.name}")
        if args.with_services_seed:
            print("  + docs/SUPABASE_SERVICES_SEED.sql")
        return

    for p in files:
        print(f"→ {p.relative_to(root)}")
        _run_psql(database_url, p)

    if args.with_services_seed:
        seed = root / "docs" / "SUPABASE_SERVICES_SEED.sql"
        if not seed.is_file():
            sys.stderr.write(f"Нет файла: {seed}\n")
            sys.exit(1)
        print(f"→ {seed.relative_to(root)} (seed)")
        _run_psql(database_url, seed)

    print("Готово.")


if __name__ == "__main__":
    main()
