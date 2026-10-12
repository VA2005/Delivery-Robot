# Module 1: Web Authentication, Server & Database

## Step 0: Setup

Requires Python 3.10 or newer (the code uses the `str | None` syntax).

```bash
cd server
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Step 1: Database (SQLite)

`app/db.py` creates 5 tables: `users`, `compartments`, `orders`, `used_tokens` (anti-replay), `audit_log`.

```bash
python seed.py        # creates navbot.db + sample users
```

| ID | Password | Role |
|---|---|---|
| admin01 | admin123 | admin |
| staff01 | staff123 | staff |
| 10423005 | pass001 | recipient |
| 10423114 | pass002 | recipient |
| 10423076 | pass003 | recipient |

## Step 2: Security (`app/security.py`)

- Passwords are hashed with `bcrypt` (used directly, avoiding passlib compatibility issues).
- There are two kinds of JWT, which must never be mixed:
  - **QR token** (`typ=qr`): valid for 60 s, single use, bound to `DELIVERY_POINT`. The robot gets it from `GET /api/qr/token` with the `X-Robot-Key` header.
  - **Session token** (`typ=session`): issued after login. Recipient sessions last only 120 s and carry the `jti` of the QR token used to log in. Staff and admin sessions last 30 minutes.

## Step 3: API (`app/main.py`)

| Endpoint | Caller | What it does |
|---|---|---|
| `POST /api/orders/create` | admin | Finds an EMPTY compartment, sets it to RESERVED, creates the order, notifies the mission manager over WebSocket |
| `POST /api/orders/{id}/loaded` | staff/admin | Order becomes LOADED, compartment becomes LOADED (simplified UC03) |
| `GET /api/qr/token` | robot | Issues a new QR token |
| `POST /api/auth/login` | anyone | Checks the password and lockout; recipients must also send a valid QR token |
| `POST /api/unlock` | recipient | Finds the LOADED order of the exact user, consumes the `jti` (anti-replay), unlocks the compartment, sends `{"cmd":"UNLOCK"}` |
| `GET /api/orders` | staff/admin | Latest 50 orders (UC06) |
| `GET /api/audit` | admin | Latest 100 audit log entries (UC06) |
| `WS /ws/robot?key=...` | ROS 2 bridge node | Receives UNLOCK / ORDER_CREATED messages |

Anti-replay flow: a QR token is written to `used_tokens` only when the unlock succeeds. Using it a second time is rejected at login (401).

## Step 4: Run the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Automatic API docs: http://localhost:8000/docs
- Dispatch page: http://localhost:8000/dispatch.html
- Recipient login page: http://localhost:8000/login.html?t=<qr_token>

## Step 5: Manual test with curl

```bash
# 1. Log in as admin and copy the access_token
curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"user_id":"admin01","password":"admin123"}'
# 2. Create an order (replace <ADMIN_TOKEN>)
curl -s localhost:8000/api/orders/create -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <ADMIN_TOKEN>' -d '{"recipient_id":"10423005","details":"chicken rice"}'
# 3. Log in as staff, then mark the order as loaded (replace <STAFF_TOKEN>)
curl -s localhost:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"user_id":"staff01","password":"staff123"}'
curl -s -X POST localhost:8000/api/orders/1/loaded -H 'Authorization: Bearer <STAFF_TOKEN>'
# 4. Simulate the robot requesting a QR token
curl -s localhost:8000/api/qr/token -H 'X-Robot-Key: dev-robot-key'
# 5. Within 60 seconds, open http://localhost:8000/login.html?t=<TOKEN>
#    on a phone or browser and sign in as 10423005 / pass001
```

If you lose track of what happened, check the `audit_log` table (`GET /api/audit`).

## Step 6: Run the unit tests

```bash
pytest -q
```

8 tests: happy path, QR reuse, wrong user, expired QR, missing QR, account lockout, role permissions, each recipient gets their own compartment.

## Step 7: Self-signed HTTPS (demo)

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 -subj "/CN=navbot.local"
uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

## Environment variables (set these for real use)

`JWT_SECRET`, `ROBOT_KEY`, `DELIVERY_POINT` (default DP1), `DB_PATH`, `REQUIRE_ROBOT=1` (returns 503 if no robot is connected, keeping compartments locked as required by UC04).

## Handover to Module 2 (ROS 2)

- The QR node calls `GET /api/qr/token` every 50 s, then draws a QR code for the URL `https://<ip>:8443/login.html?t=<token>`.
- The bridge node connects to `wss://<ip>:8443/ws/robot?key=<ROBOT_KEY>` and receives JSON messages:
  - `{"cmd":"UNLOCK","compartment":1,"order_id":3}` is converted into a UART UNLOCK frame
  - `{"event":"ORDER_CREATED",...}` is forwarded to the mission manager

## Remaining work

- Order completion endpoint: the compartment returns to EMPTY and the order becomes COMPLETED after pickup (needs the LOCK_STATUS signal from ECE). It does not exist yet, so after 2 orders the database must be reset (delete `navbot.db`, then run `python seed.py`).
- Staff must scan the robot's QR code before the loading compartment opens (full UC03).
- The server does not know the robot's mission state: unlock should only be valid while the robot is in `AWAITING_PICKUP`.
- Simultaneous order creation could pick the same compartment (the UPDATE does not check that the compartment is still EMPTY).
- Account lockout can be abused: anyone who knows a student ID can lock that account for 5 minutes.
- Overdue and cancelled orders are not handled yet.
- `JWT_SECRET` and `ROBOT_KEY` have development defaults; set environment variables for a real demo.