from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> None:
    db = Path("ayush.db")
    if not db.exists():
        print("Database not found:", db)
        return
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = OFF")

    # Order matters for foreign-key-like dependencies.
    trim_order = [
        "chat_messages",
        "consultation_sessions",
        "diagnosis",
        "recommendations",
        "visits",
        "doctor_profiles",
        "student_verifications",
        "alerts",
        "patients",
        "doctors",
        "students",
        "doctor_license_registry",
        "student_registry",
    ]

    for table in trim_order:
        exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        cur.execute(
            f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT 3)"
        )
        print(f"trimmed {table}")

    con.commit()
    cur.execute("PRAGMA foreign_keys = ON")
    con.close()
    print("Trim complete (max 3 rows per table).")


if __name__ == "__main__":
    main()
