"""Campus-NavBot server: orders, authentication, unlock."""
import hmac
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
from fastapi import (Depends, FastAPI, Header, HTTPException, Request,
                     WebSocket, WebSocketDisconnect)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import security as sec
from .db import audit, get_db, init_db

def same_secret(a: str, b: str) -> bool:
    """Constant-time comparison that also works with non-ASCII text."""
    return hmac.compare_digest(a.encode(), b.encode())

MAX_FAILS = 5
LOCK_SECONDS = 300
LOGIN_LIMIT, LOGIN_WINDOW = 20, 60          # requests per IP per window
REQUIRE_ROBOT = os.getenv("REQUIRE_ROBOT", "0") == "1"   # 503 if no robot is connected


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Campus-NavBot Server", lifespan=lifespan)


# ---------- helpers ----------
def fail(db, code, msg, user=None, action="ERROR"):
    """Audit the failure, persist it, then raise (get_db would not commit on exception)."""
    audit(db, user, action, False, msg)
    db.commit()
    raise HTTPException(code, msg)


_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request):
    ip = request.client.host if request.client else "?"
    q, now = _hits[ip], time.time()
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_LIMIT:
        raise HTTPException(429, "Too many requests, slow down")
    q.append(now)


def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    try:
        return sec.decode(authorization[7:], "session")
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


def require_role(*roles):
    def dep(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "Not allowed")
        return user
    return dep


class RobotHub:
    """WebSocket clients = the ROS 2 web-bridge node (Module 2)."""
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.clients.discard(ws)


hub = RobotHub()


# ---------- auth ----------
class LoginIn(BaseModel):
    user_id: str
    password: str
    qr_token: str | None = None      # required for recipients


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request):
    rate_limit(request)
    now = time.time()
    with get_db() as db:
        u = db.execute("SELECT * FROM users WHERE id=?", (body.user_id,)).fetchone()
        if u and u["locked_until"] > now:
            fail(db, 423, "Account temporarily locked", body.user_id, "LOGIN")

        if not u or not sec.verify_password(body.password, u["password_hash"]):
            if u:
                fails = u["failed_attempts"] + 1
                locked = now + LOCK_SECONDS if fails >= MAX_FAILS else 0
                db.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                           (0 if locked else fails, locked, u["id"]))
            fail(db, 401, "Wrong user ID or password", body.user_id, "LOGIN")

        qr_jti = None
        if u["role"] == "recipient":
            if not body.qr_token:
                fail(db, 400, "Scan the QR code on the robot first", u["id"], "LOGIN")
            try:
                q = sec.decode(body.qr_token, "qr")
            except jwt.PyJWTError:
                fail(db, 401, "QR code expired or invalid, scan again", u["id"], "LOGIN")
            if q.get("dp") != sec.DELIVERY_POINT:
                fail(db, 403, "QR code belongs to another delivery point", u["id"], "LOGIN")
            if db.execute("SELECT 1 FROM used_tokens WHERE jti=?", (q["jti"],)).fetchone():
                fail(db, 401, "QR code already used, scan again", u["id"], "LOGIN")
            qr_jti = q["jti"]

        db.execute("UPDATE users SET failed_attempts=0, locked_until=0 WHERE id=?", (u["id"],))
        audit(db, u["id"], "LOGIN", True, u["role"])
    return {"access_token": sec.create_session_token(u["id"], u["role"], qr_jti), "role": u["role"]}


# ---------- QR token (robot only) ----------
@app.get("/api/qr/token")
def qr_token(x_robot_key: str = Header(default="")):
    if not same_secret(x_robot_key, sec.ROBOT_KEY):
        raise HTTPException(403, "Robot key required")
    return {"token": sec.create_qr_token(), "ttl": sec.QR_TTL}


# ---------- orders ----------
class OrderIn(BaseModel):
    recipient_id: str
    details: str = ""


@app.post("/api/orders/create")
async def create_order(body: OrderIn, user=Depends(require_role("admin"))):
    with get_db() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=? AND role='recipient'",
                          (body.recipient_id,)).fetchone():
            fail(db, 404, "Unknown recipient ID", user["sub"], "ORDER_CREATE")
        comp = db.execute("SELECT id FROM compartments WHERE state='EMPTY' ORDER BY id LIMIT 1").fetchone()
        if not comp:
            fail(db, 409, "No free compartment", user["sub"], "ORDER_CREATE")
        # reserve first: the WHERE clause makes sure nobody took this box in the meantime
        taken = db.execute("UPDATE compartments SET state='RESERVED' WHERE id=? AND state='EMPTY'",
                           (comp["id"],)).rowcount
        if taken != 1:
            fail(db, 409, "Compartment was just taken, please try again", user["sub"], "ORDER_CREATE")
        cur = db.execute(
            "INSERT INTO orders(recipient_id,compartment,details,created_at) VALUES (?,?,?,?)",
            (body.recipient_id, comp["id"], body.details, time.time()))
        audit(db, user["sub"], "ORDER_CREATE", True, f"order {cur.lastrowid} -> box {comp['id']}")
        result = {"order_id": cur.lastrowid, "compartment": comp["id"], "status": "CREATED"}
    await hub.broadcast({"event": "ORDER_CREATED", **result})   # tells the mission manager
    return result


@app.post("/api/orders/{order_id}/loaded")
def mark_loaded(order_id: int, user=Depends(require_role("staff", "admin"))):
    """UC03 (simplified): staff confirms the food is in the box and the door is closed."""
    with get_db() as db:
        o = db.execute("SELECT * FROM orders WHERE id=? AND status='CREATED'", (order_id,)).fetchone()
        if not o:
            fail(db, 404, "No order waiting to be loaded", user["sub"], "LOAD")
        db.execute("UPDATE orders SET status='LOADED' WHERE id=?", (order_id,))
        db.execute("UPDATE compartments SET state='LOADED' WHERE id=?", (o["compartment"],))
        audit(db, user["sub"], "LOAD", True, f"order {order_id} box {o['compartment']}")
    return {"order_id": order_id, "status": "LOADED"}

@app.post("/api/orders/{order_id}/complete")
def complete_order(order_id: int, user=Depends(require_role("staff", "admin"))):
    """Temporary: free the compartment after pickup.
    Later this will be triggered by LOCK_STATUS from the ECE microcontroller."""
    with get_db() as db:
        o = db.execute("SELECT * FROM orders WHERE id=? AND status='UNLOCKED'", (order_id,)).fetchone()
        if not o:
            fail(db, 404, "No unlocked order with this ID", user["sub"], "COMPLETE")
        db.execute("UPDATE orders SET status='COMPLETED' WHERE id=?", (order_id,))
        db.execute("UPDATE compartments SET state='EMPTY' WHERE id=?", (o["compartment"],))
        audit(db, user["sub"], "COMPLETE", True, f"order {order_id} box {o['compartment']}")
    return {"order_id": order_id, "status": "COMPLETED", "compartment": o["compartment"]}


@app.get("/api/orders")
def list_orders(user=Depends(require_role("staff", "admin"))):
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 50")]


@app.get("/api/audit")
def list_audit(user=Depends(require_role("admin"))):
    with get_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100")]


# ---------- unlock ----------
@app.post("/api/unlock")
async def unlock(user=Depends(require_role("recipient"))):
    jti = user.get("qr")
    with get_db() as db:
        if not jti:
            fail(db, 401, "Session was not created from a QR scan", user["sub"], "UNLOCK")
        if REQUIRE_ROBOT and not hub.clients:
            fail(db, 503, "Robot not reachable, compartments stay locked", user["sub"], "UNLOCK")

        order = db.execute(
            "SELECT * FROM orders WHERE recipient_id=? AND status='LOADED' ORDER BY id LIMIT 1",
            (user["sub"],)).fetchone()
        if not order or order["recipient_id"] != user["sub"]:   # User_ID == Recipient_ID
            fail(db, 404, "No order ready for you", user["sub"], "UNLOCK")

        try:                                                    # anti-replay: consume the QR token
            db.execute("INSERT INTO used_tokens(jti,used_at) VALUES (?,?)", (jti, time.time()))
        except sqlite3.IntegrityError:
            fail(db, 409, "QR code already used", user["sub"], "UNLOCK")

        db.execute("UPDATE orders SET status='UNLOCKED' WHERE id=?", (order["id"],))
        db.execute("UPDATE compartments SET state='UNLOCKED' WHERE id=?", (order["compartment"],))
        audit(db, user["sub"], "UNLOCK", True, f"order {order['id']} box {order['compartment']}")
        comp = order["compartment"]
    await hub.broadcast({"cmd": "UNLOCK", "compartment": comp, "order_id": order["id"]})
    return {"compartment": comp, "message": f"Compartment {comp} is open"}


# ---------- WebSocket for the ROS 2 bridge ----------
@app.websocket("/ws/robot")
async def ws_robot(ws: WebSocket, key: str = ""):
    if not same_secret(key, sec.ROBOT_KEY):
        await ws.close(code=1008)
        return
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()      # status messages from the robot (extend later)
    except WebSocketDisconnect:
        hub.clients.discard(ws)


# static pages last, so they never shadow /api
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
