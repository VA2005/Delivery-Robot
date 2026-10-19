"""Password hashing and the two kinds of JWT.

QR token  (typ="qr")      : issued to the robot, 60 s, single use, bound to a delivery point.
Session   (typ="session") : issued after login. Recipient sessions are short (120 s)
                            and carry the jti of the QR token they logged in with.
"""
import os
import time
import uuid

import bcrypt
import jwt

ENV = os.getenv("NAVBOT_ENV", "dev")        # "dev" (default) or "production"
_DEV_SECRET = "dev-only-secret-change-me-before-deploying-0123456789"
_DEV_ROBOT_KEY = "dev-robot-key"
SECRET = os.getenv("JWT_SECRET", _DEV_SECRET)
ROBOT_KEY = os.getenv("ROBOT_KEY", _DEV_ROBOT_KEY)
DELIVERY_POINT = os.getenv("DELIVERY_POINT", "DP1")
ALGO = "HS256"
QR_TTL = 60            # seconds (spec: 30-60 s)
RECIPIENT_SESSION_TTL = 120
STAFF_SESSION_TTL = 1800

def check_config():
    """Run at startup. Production refuses development defaults and weak secrets."""
    problems = []
    if SECRET == _DEV_SECRET or len(SECRET) < 32:
        problems.append("JWT_SECRET must be a random value of at least 32 characters")
    if ROBOT_KEY == _DEV_ROBOT_KEY or len(ROBOT_KEY) < 16:
        problems.append("ROBOT_KEY must be a random value of at least 16 characters")
    if not problems:
        return
    if ENV == "production":
        raise RuntimeError("Unsafe configuration: " + "; ".join(problems))
    print("WARNING (development mode): " + "; ".join(problems))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _encode(payload: dict, ttl: int, typ: str) -> str:
    now = int(time.time())
    claims = {**payload, "typ": typ, "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, SECRET, algorithm=ALGO)


def decode(token: str, typ: str) -> dict:
    """Raises jwt.PyJWTError if invalid, expired or of the wrong type."""
    data = jwt.decode(token, SECRET, algorithms=[ALGO], options={"require": ["exp", "jti"]})
    if data.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return data


def create_qr_token() -> str:
    return _encode({"dp": DELIVERY_POINT}, QR_TTL, "qr")


def create_session_token(user_id: str, role: str, qr_jti: str | None = None) -> str:
    ttl = RECIPIENT_SESSION_TTL if role == "recipient" else STAFF_SESSION_TTL
    payload = {"sub": user_id, "role": role}
    if qr_jti:
        payload["qr"] = qr_jti
    return _encode(payload, ttl, "session")


