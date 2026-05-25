"""Загрузка реального EdTech-датасета в тестовый ClickHouse.

Источник — три CSV из data.zip, распакованные в `/seed-data` (см. compose mount):
    cdm.teachers_events_daily.csv  (~784 МБ, ~80 колонок, факты по дням)
    dict.teachers.csv              (~91  МБ, справочник учителей)
    dict.schools.csv               (~23  МБ, справочник школ)

Загрузчик:
    1. ждёт пока ClickHouse поднимется
    2. применяет schema.sql (создаёт базы cdm/dict и таблицы заново)
    3. стримит каждый CSV в CH через HTTP с input_format_csv_use_default_on_bad_input=1
    4. прогревает system.query_log набором аналитических запросов
    5. печатает итог по строкам

Большие файлы льются чанками — память не зависит от размера файла.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path
from typing import Iterator

import clickhouse_connect

DATA_DIR = Path(os.environ.get("CH_SEED_DATA_DIR", "/seed-data"))

# (CSV file, target db, target table). Порядок важен: справочники грузим раньше факта.
SOURCES: list[tuple[str, str, str]] = [
    ("dict.schools.csv", "dict", "schools"),
    ("dict.teachers.csv", "dict", "teachers"),
    ("cdm.teachers_events_daily.csv", "cdm", "teachers_events_daily"),
]

# Stream each CSV in 32 MiB blocks. The HTTP server side of CH happily handles
# chunked Transfer-Encoding so this stays constant memory even for the 784 MB
# fact table.
READ_CHUNK = 32 * 1024 * 1024

INSERT_SETTINGS = {
    # Пустые поля → DEFAULT (нули / NULL для Nullable).
    "input_format_null_as_default": 1,
    "input_format_defaults_for_omitted_fields": 1,
    # CSV из выгрузки содержит дата-форматы вида 'YYYY-MM-DD HH:MM:SS' и пустые
    # строки — best_effort даёт нам послабление парсера.
    "date_time_input_format": "best_effort",
    # Размер блока — наш fact-stream идёт чанками HTTP.
    "max_insert_block_size": 200_000,
    "min_insert_block_size_rows": 100_000,
}


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def wait_for_ch(client_kwargs: dict, attempts: int = 60) -> "clickhouse_connect.driver.Client":
    print(
        f"Подключаюсь к ClickHouse {client_kwargs['host']}:{client_kwargs['port']}…",
        flush=True,
    )
    last: Exception | None = None
    for _ in range(attempts):
        try:
            client = clickhouse_connect.get_client(**client_kwargs)
            client.command("SELECT 1")
            print("OK\n", flush=True)
            return client
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1)
    print(f"Не удалось подключиться: {last}", file=sys.stderr)
    raise SystemExit(1)


def apply_schema(client) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    print(f"Применяю {schema_path.name}…", flush=True)
    sql = schema_path.read_text(encoding="utf-8")
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        try:
            client.command(stmt)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! statement failed: {exc}\n  {stmt[:160]}", file=sys.stderr)
            raise
    print("OK\n", flush=True)


def file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as f:
        while True:
            chunk = f.read(READ_CHUNK)
            if not chunk:
                break
            yield chunk


def load_csv(client, csv_path: Path, db: str, table: str) -> int:
    """Стрим CSV в указанную таблицу через raw_insert / HTTP chunked.

    Возвращает количество вставленных строк (по count() после).
    """
    size_mb = csv_path.stat().st_size / (1024 * 1024)
    print(f"  → {db}.{table}  ({size_mb:.1f} МБ из {csv_path.name})", flush=True)

    # clickhouse-connect не умеет «стримить» произвольный байтовый итератор
    # напрямую, но у его HTTP-клиента есть `raw_insert(stream=...)` который
    # принимает file-like объект. Открываем raw файл и отдаём ему — драйвер
    # сам пишет тело чанками в сокет.
    started = time.time()
    with csv_path.open("rb") as f:
        client.raw_insert(
            table=f"{db}.{table}",
            insert_block=f,
            fmt="CSVWithNames",
            settings=INSERT_SETTINGS,
        )
    elapsed = time.time() - started

    n = int(client.command(f"SELECT count() FROM {db}.{table}"))
    print(
        f"     ✓ {n:>11,} строк  ({elapsed:.1f}s, {size_mb / max(elapsed, 0.01):.1f} МБ/с)",
        flush=True,
    )
    return n


def run_analytical_warmup(client) -> None:
    """Аналитические запросы — фактовая + два справочника. Наполняют system.query_log."""
    queries = [
        # taxonomy / volumes
        "SELECT count() FROM cdm.teachers_events_daily",
        "SELECT count() FROM dict.teachers",
        "SELECT count() FROM dict.schools",
        "SELECT min(ptn_date), max(ptn_date) FROM cdm.teachers_events_daily",
        # daily activity dynamics
        "SELECT ptn_date, sum(registered_students_count) FROM cdm.teachers_events_daily GROUP BY ptn_date ORDER BY ptn_date DESC LIMIT 30",
        "SELECT toStartOfMonth(ptn_date) m, sum(amount_sum) FROM cdm.teachers_events_daily GROUP BY m ORDER BY m DESC LIMIT 12",
        # parallel-group breakdowns
        "SELECT group_parallel, sum(start_lessons_count) FROM cdm.teachers_events_daily GROUP BY group_parallel ORDER BY 2 DESC",
        "SELECT region_type, count() FROM cdm.teachers_events_daily GROUP BY region_type",
        "SELECT reg_name, sum(activated_students_count) FROM cdm.teachers_events_daily GROUP BY reg_name ORDER BY 2 DESC LIMIT 20",
        # device mix
        "SELECT sum(desktop_flag), sum(mobile_app_flag), sum(mobile_web_flag) FROM cdm.teachers_events_daily",
        "SELECT sum(app_android_flag), sum(app_ios_flag) FROM cdm.teachers_events_daily",
        # trial / paid funnel
        "SELECT sum(trial), sum(arrayCount(x -> 1, JSONExtract(replaceAll(replaceAll(payed_student_ids, '{', '['), '}', ']'), 'Array(String)'))) FROM cdm.teachers_events_daily LIMIT 1",
        "SELECT ptn_date, sum(amount_sum_5_8), sum(amount_sum_10_11) FROM cdm.teachers_events_daily GROUP BY ptn_date ORDER BY ptn_date DESC LIMIT 30",
        # joins teachers ↔ events
        "SELECT t.segment, sum(e.start_lessons_count) FROM cdm.teachers_events_daily e JOIN dict.teachers t ON t.teacher_id = e.teacher_id GROUP BY t.segment ORDER BY 2 DESC",
        "SELECT t.boarding_state, count() FROM dict.teachers t GROUP BY t.boarding_state ORDER BY 2 DESC",
        "SELECT t.payment_type, count() FROM dict.teachers t GROUP BY t.payment_type ORDER BY 2 DESC",
        # joins schools
        "SELECT s.mun_type, count() FROM dict.schools s GROUP BY s.mun_type ORDER BY 2 DESC LIMIT 10",
        "SELECT s.school_type, sum(s.students_count) FROM dict.schools s GROUP BY s.school_type ORDER BY 2 DESC",
        "SELECT s.mun_name, count(distinct t.teacher_id) AS teachers FROM dict.schools s JOIN dict.teachers t ON t.school_id = s.school_id GROUP BY s.mun_name ORDER BY teachers DESC LIMIT 15",
        # rare event shape
        "SELECT count() FROM cdm.teachers_events_daily WHERE teacher_authorization_flag = 1",
        "SELECT count() FROM cdm.teachers_events_daily WHERE amount_sum > 0",
        "SELECT min(created_at), max(created_at) FROM dict.teachers",
    ]
    print("Прогреваю query_log…", flush=True)
    ok = 0
    for q in queries:
        try:
            client.query(q)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! query failed: {exc}\n    {q[:140]}", flush=True)
    print(f"  ✓ {ok}/{len(queries)} аналитических запросов выполнено\n", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка EdTech-датасета в ClickHouse")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument(
        "--data-dir",
        default=str(DATA_DIR),
        help="Каталог с CSV (по умолчанию /seed-data, монтируется compose'ом)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(
            f"Каталог с данными не найден: {data_dir}.\n"
            "Ожидается mount ./docker/seed/data → /seed-data (см. docker-compose.yml).",
            file=sys.stderr,
        )
        return 1
    missing = [name for name, _, _ in SOURCES if not (data_dir / name).exists()]
    if missing:
        print(
            "В каталоге данных не хватает файлов:\n  - "
            + "\n  - ".join(missing)
            + "\nРаспакуйте data.zip в docker/seed/data/",
            file=sys.stderr,
        )
        return 1

    client = wait_for_ch(
        dict(
            host=env("CH_HOST", "clickhouse"),
            port=int(env("CH_PORT", "8123")),
            username=env("CH_USER", "demo"),
            password=env("CH_PASS", "demo"),
            database=env("CH_DATABASE", "default"),
            send_receive_timeout=900,
            query_limit=0,
        )
    )

    apply_schema(client)

    print("Загружаю CSV…", flush=True)
    totals: dict[str, int] = {}
    for csv_name, db, table in SOURCES:
        totals[f"{db}.{table}"] = load_csv(client, data_dir / csv_name, db, table)
    print(flush=True)

    if not args.no_warmup:
        for db, table in (("cdm", "teachers_events_daily"),):
            try:
                client.command(f"OPTIMIZE TABLE {db}.{table} FINAL")
            except Exception:  # noqa: BLE001
                pass
        run_analytical_warmup(client)

    print("Итог по строкам:")
    for key, n in totals.items():
        print(f"  {key:<30} {n:>11,}")

    print("\nГотово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
