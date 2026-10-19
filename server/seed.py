"""Create sample users. Run: python seed.py

Development (default): the demo passwords listed below.
Production (NAVBOT_ENV=production): the password comes from SEED_PW_<ID> if set
(for example SEED_PW_ADMIN01), otherwise a random one is generated and printed once.
"""
import os
import secrets

from app import security as sec
from app.db import get_db, init_db
from app.security import hash_password

USERS = [
    ("admin01", "Dispatcher", "admin123", "admin"),
    ("staff01", "Canteen Staff", "staff123", "staff"),
    ("10423005", "Le Nguyen Viet Anh", "pass001", "recipient"),
    ("10423114", "Do Minh Triet", "pass002", "recipient"),
    ("10423076", "Nguyen Nhat Minh", "pass003", "recipient"),
    ("LEC001", "Demo Lecturer", "lecturer123", "recipient"),
    ("EMP001", "Demo University Employee", "employee123", "recipient"),
]


def password_for(uid, demo_pw, generated):
    if sec.ENV != "production":
        return demo_pw
    pw = os.getenv(f"SEED_PW_{uid.upper()}")
    if not pw:
        pw = secrets.token_urlsafe(9)
        generated[uid] = pw
    return pw


def seed():
    init_db()
    generated = {}
    with get_db() as db:
        for uid, name, pw, role in USERS:
            db.execute(
                "INSERT OR REPLACE INTO users(id,name,password_hash,role) VALUES (?,?,?,?)",
                (uid, name, hash_password(password_for(uid, pw, generated)), role),
            )
    return generated


if __name__ == "__main__":
    new_passwords = seed()
    print("Seeded:", ", ".join(u[0] for u in USERS))
    for uid, pw in new_passwords.items():
        print(f"  generated password for {uid}: {pw}")
    if new_passwords:
        print("Write these down now: they are not stored anywhere in plain text.")