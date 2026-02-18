from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text


def _sqlite_fallback_url() -> str:
    db_path = Path("ayush.db")
    if db_path.exists():
        return "sqlite:///./ayush.db"
    return ""


def _read_database_url() -> str:
    load_dotenv(".env", override=False)
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    return _sqlite_fallback_url()


def main() -> None:
    db_url = _read_database_url()
    if not db_url:
        print("No DATABASE_URL found and ayush.db missing.")
        return
    engine = create_engine(db_url, future=True, pool_pre_ping=True)
    insp = inspect(engine)
    tables = sorted(insp.get_table_names())
    print(f"Database: {db_url}")
    print("Tables:")
    with engine.connect() as conn:
        for t in tables:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            print(f"- {t}: {count} rows")
        print("\nSample rows (first 3):")
        for t in tables:
            if t in {"patients", "doctors", "students"}:
                rows = conn.execute(text(f'SELECT * FROM "{t}" ORDER BY id DESC LIMIT 3')).fetchall()
            else:
                rows = conn.execute(text(f'SELECT * FROM "{t}" LIMIT 3')).fetchall()
            print(f"\n[{t}]")
            for r in rows:
                print(tuple(r))

        print("\n\n=== FULL ROLE DETAILS ===")
        print("\n[patients_full]")
        patient_rows = conn.execute(
            text(
                """
                SELECT patient_id, uhid, full_name, email, phone, language, state, district, blood_group, created_at
                FROM patients
                ORDER BY created_at DESC
                """
            )
        ).fetchall()
        for r in patient_rows:
            print(tuple(r))

        print("\n[doctors_full]")
        doctor_rows = conn.execute(
            text(
                """
                SELECT d.doctor_id, d.full_name, d.government_license_id, d.email, p.phone, p.address, d.created_at
                FROM doctors d
                LEFT JOIN doctor_profiles p ON p.doctor_id = d.doctor_id
                ORDER BY d.created_at DESC
                """
            )
        ).fetchall()
        for r in doctor_rows:
            print(tuple(r))

        print("\n[students_full]")
        student_rows = conn.execute(
            text(
                """
                SELECT student_id, full_name, college_id, institute_name, official_email, phone, language_preference, rating_avg, rating_count, created_at
                FROM students
                ORDER BY created_at DESC
                """
            )
        ).fetchall()
        for r in student_rows:
            print(tuple(r))


if __name__ == "__main__":
    main()
