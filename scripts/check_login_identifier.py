from __future__ import annotations

import argparse
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser(description="Check whether login identifier exists in DB.")
    parser.add_argument("--role", choices=["patient", "doctor", "student"], required=True)
    parser.add_argument("--identifier", required=True, help="Email or phone")
    args = parser.parse_args()

    con = sqlite3.connect("ayush.db")
    cur = con.cursor()
    ident = args.identifier.strip().lower()

    if args.role == "patient":
        row = cur.execute(
            "SELECT patient_id, full_name, email, phone FROM patients WHERE lower(email)=? OR phone=?",
            (ident, ident),
        ).fetchone()
    elif args.role == "doctor":
        row = cur.execute(
            """
            SELECT d.doctor_id, d.full_name, d.email, p.phone
            FROM doctors d
            LEFT JOIN doctor_profiles p ON p.doctor_id=d.doctor_id
            WHERE lower(d.email)=? OR p.phone=?
            """,
            (ident, ident),
        ).fetchone()
    else:
        row = cur.execute(
            "SELECT student_id, college_id, official_email, phone FROM students WHERE lower(official_email)=? OR phone=?",
            (ident, ident),
        ).fetchone()

    if row:
        print("MATCH_FOUND:", row)
    else:
        print("NO_MATCH")
    con.close()


if __name__ == "__main__":
    main()
