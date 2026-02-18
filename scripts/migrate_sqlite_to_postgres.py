from __future__ import annotations

import os
import sys
from typing import Iterable

from sqlalchemy import MetaData, create_engine, delete, insert, select, text
from sqlalchemy.engine import Engine


def _build_engine(url: str) -> Engine:
    return create_engine(url, future=True, pool_pre_ping=True)


def _tables_in_order(metadata: MetaData) -> Iterable:
    # Keep deterministic order and honor FK dependencies.
    return metadata.sorted_tables


def _reset_pg_sequences(conn, metadata: MetaData) -> None:
    for table in metadata.tables.values():
        if "id" in table.c:
            try:
                conn.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:tbl, 'id'), "
                        "COALESCE((SELECT MAX(id) FROM " + table.name + "), 1), true)"
                    ),
                    {"tbl": table.name},
                )
            except Exception:
                # Safe best-effort: not every table has serial/identity id.
                pass


def migrate(sqlite_url: str, postgres_url: str) -> None:
    src = _build_engine(sqlite_url)
    dst = _build_engine(postgres_url)

    src_meta = MetaData()
    src_meta.reflect(bind=src)
    if not src_meta.tables:
        raise RuntimeError("No tables found in source SQLite database.")

    dst_meta = MetaData()
    dst_meta.reflect(bind=dst)

    print(f"[INFO] Source tables: {len(src_meta.tables)}")
    print(f"[INFO] Target tables: {len(dst_meta.tables)}")

    with src.connect() as src_conn, dst.begin() as dst_conn:
        # Delete in reverse dependency order to avoid FK issues.
        for table in reversed(list(_tables_in_order(src_meta))):
            if table.name in dst_meta.tables:
                dst_conn.execute(delete(dst_meta.tables[table.name]))

        # Insert in dependency order.
        for table in _tables_in_order(src_meta):
            if table.name not in dst_meta.tables:
                print(f"[WARN] Skipping table not present in target: {table.name}")
                continue
            rows = src_conn.execute(select(table)).mappings().all()
            if not rows:
                print(f"[OK] {table.name}: 0 rows")
                continue
            dst_table = dst_meta.tables[table.name]
            payload = [dict(r) for r in rows]
            dst_conn.execute(insert(dst_table), payload)
            print(f"[OK] {table.name}: {len(payload)} rows copied")

        _reset_pg_sequences(dst_conn, dst_meta)

    print("[DONE] SQLite -> PostgreSQL migration completed.")


def main() -> int:
    sqlite_url = os.getenv("SQLITE_URL", "sqlite:///./ayush.db").strip()
    postgres_url = os.getenv("POSTGRES_URL", "").strip()
    if not postgres_url:
        postgres_url = os.getenv("DATABASE_URL", "").strip()

    if not sqlite_url.startswith("sqlite"):
        print("[FAIL] SQLITE_URL must point to sqlite database.")
        return 1
    if not postgres_url.startswith("postgresql"):
        print("[FAIL] Provide POSTGRES_URL (or DATABASE_URL) with postgresql://...")
        return 1

    try:
        migrate(sqlite_url, postgres_url)
        return 0
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

