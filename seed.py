"""Create sample users. Run: python seed.py"""
from app.db import get_db, init_db
from app.security import hash_password

USERS = [
    ("admin01", "Dispatcher", "admin123", "admin"),
    ("staff01", "Canteen Staff", "staff123", "staff"),
    ("10423005", "Le Nguyen Viet Anh", "pass001", "recipient"),
    ("10423114", "Do Minh Triet", "pass002", "recipient"),
    ("10423076", "Nguyen Nhat Minh", "pass003", "recipient")
]


def seed():
    init_db()
    with get_db() as db:
        for uid, name, pw, role in USERS:
            db.execute(
                "INSERT OR REPLACE INTO users(id,name,password_hash,role) VALUES (?,?,?,?)",
                (uid, name, hash_password(pw), role),
            )


if __name__ == "__main__":
    seed()
    print("Seeded:", ", ".join(u[0] for u in USERS))
