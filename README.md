# Campus-NavBot: Module 1 — Web Authentication, Server & Database

Backend for the Campus-NavBot delivery robot. It creates delivery orders, reserves one of the robot's two compartments, authenticates recipients with a short-lived QR code plus a university login, and unlocks **only** the compartment that holds the recipient's order.

Built with **FastAPI**, **SQLite**, **PyJWT** and **bcrypt**. The ROS 2 bridge (Module 2) connects to this server over a WebSocket.

## Project structure

```
Delivery-Robot/
├── README.md
├── .gitignore
└── server/
    ├── app/
    │   ├── __init__.py
    │   ├── db.py              # SQLite schema and helpers
    │   ├── security.py        # password hashing, JWT creation and validation
    │   ├── main.py            # FastAPI app: auth, orders, unlock, WebSocket
    │   └── static/
    │       ├── dispatch.html  # admin/staff page: create and track orders
    │       └── login.html     # sign-in page opened from the robot's QR code
    ├── seed.py                # creates sample users
    ├── test_flow.py           # pytest suite (10 tests)
    └── requirements.txt
```

## Roles

| Role | Who | What they can do |
|---|---|---|
| `recipient` | Students, lecturers and university employees | Have orders delivered by the robot and collect them by scanning the QR code and signing in |
| `staff` | Canteen staff | Mark orders as loaded and as collected on the dispatch page |
| `admin` | Dispatcher | Create orders, monitor orders, read the audit log |

Anyone in the university community uses the `recipient` role. An order can only be created for a `recipient`, and only that recipient can open the matching compartment. Staff and admin accounts cannot collect orders.

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
| staff01 | staff123 | staff (canteen staff) |
| 10423005 | pass001 | recipient (student) |
| 10423114 | pass002 | recipient (student) |
| 10423076 | pass003 | recipient (student) |
| LEC001 | lecturer123 | recipient (lecturer) |
| EMP001 | employee123 | recipient (university employee) |

These are demo credentials only. Running `python seed.py` again adds or resets the sample users. To reset all data, stop the server, delete `navbot.db`, and run `python seed.py`.

### States

| Compartment | Meaning |
|---|---|
| `EMPTY` | No order assigned |
| `RESERVED` | Order assigned, food not loaded yet |
| `LOADED` | Food loaded and locked |
| `UNLOCKED` | Opened for the authenticated recipient |

Order status: `CREATED` → `LOADED` → `UNLOCKED` → `COMPLETED` (`CANCELLED` is reserved for later use).

Normal compartment cycle: `EMPTY` → `RESERVED` → `LOADED` → `UNLOCKED` → `EMPTY` (after "Mark collected").

## Step 2: Security (`app/security.py`)

- Passwords are hashed with `bcrypt` (used directly, avoiding passlib compatibility issues).
- There are two kinds of JWT, which must never be mixed:
  - **QR token** (`typ=qr`): valid for 60 s, single use, bound to `DELIVERY_POINT`. The robot gets it from `GET /api/qr/token` with the `X-Robot-Key` header.
  - **Session token** (`typ=session`): issued after login. Recipient sessions last only 120 s and carry the `jti` of the QR token used to log in. Staff and admin sessions last 30 minutes.
- Only recipients must present a QR token to sign in. Staff and admin sign in without one because they use the dispatch page.

| Measure | Threat addressed |
|---|---|
| bcrypt password hashing | Stolen database |
| Short-lived, single-use QR token bound to the delivery point | Replay, photographed QR codes, use at another location |
| Recipient session carries the QR token's `jti` | Unlocking without scanning the QR |
| Order matching on the authenticated user ID | Opening someone else's compartment |
| Login rate limit (20 requests/min per IP) | Brute-force and request flooding |
| Lockout (5 wrong passwords lock the account for 5 minutes) | Password guessing |
| Role checks on every protected endpoint | Privilege escalation |
| Robot key for `/api/qr/token` and `/ws/robot` | Unauthorized QR minting or command channel access |
| Audit log of every attempt | Disputes and forensic analysis |

## Step 3: API (`app/main.py`)

| Endpoint | Caller | What it does |
|---|---|---|
| `POST /api/orders/create` | admin | Finds an EMPTY compartment, sets it to RESERVED, creates the order, notifies the mission manager over WebSocket |
| `POST /api/orders/{id}/loaded` | staff/admin | Order becomes LOADED, compartment becomes LOADED (simplified UC03) |
| `POST /api/orders/{id}/complete` | staff/admin | Temporary: order becomes COMPLETED and the compartment returns to EMPTY (only for UNLOCKED orders) |
| `GET /api/qr/token` | robot | Issues a new QR token |
| `POST /api/auth/login` | anyone | Checks the password and lockout; recipients must also send a valid QR token |
| `POST /api/unlock` | recipient | Finds the LOADED order of the exact user, consumes the `jti` (anti-replay), unlocks the compartment, sends `{"cmd":"UNLOCK"}` |
| `GET /api/orders` | staff/admin | Latest 50 orders (UC06) |
| `GET /api/audit` | admin | Latest 100 audit log entries (UC06) |
| `WS /ws/robot?key=...` | ROS 2 bridge node | Receives UNLOCK / ORDER_CREATED messages |

Protected endpoints expect the header `Authorization: Bearer <access_token>`.

Anti-replay flow: a QR token is written to `used_tokens` only when the unlock succeeds. Using it a second time is rejected at login (401).

Typical error codes: `400` missing QR token, `401` invalid/expired/reused token or bad credentials, `403` wrong role or wrong delivery point, `404` no matching order, `409` no free compartment, `423` account locked, `429` too many requests, `503` robot not connected (only when `REQUIRE_ROBOT=1`).

### Web pages

| Page | Used by | Purpose |
|---|---|---|
| `/dispatch.html` | admin, staff | Create orders (admin), mark orders as loaded and collected |
| `/login.html?t=<qr_token>` | students, lecturers, employees | Sign in after scanning the robot's QR code; the matching compartment opens |

On `login.html`, a signed-in `staff` account sees a message that loading by QR is not available yet, and an `admin` account sees that admins do not collect orders. Neither can open a compartment from this page.

## Step 4: Run the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Add `--reload` if you are editing code on your own machine. It can be very slow in GitHub Codespaces, so leave it out there.

- Automatic API docs: http://localhost:8000/docs
- Dispatch page: http://localhost:8000/dispatch.html
- Sign-in page: http://localhost:8000/login.html?t=<qr_token>

**Running in GitHub Codespaces:** `localhost` in your own browser does not reach the Codespace. Open the **Ports** tab, find port 8000, and use its forwarded address (`https://<name>-8000.app.github.dev`) in place of `http://localhost:8000`. Make the port **Public** only while demonstrating on another device (for example a phone), then set it back to **Private**.

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
# 6. After pickup, free the compartment
curl -s -X POST localhost:8000/api/orders/1/complete -H 'Authorization: Bearer <STAFF_TOKEN>'
```

If you lose track of what happened, check the audit log (`GET /api/audit`, admin only).

### Demo walkthrough (browser only)

1. Open `/dispatch.html`, sign in as `admin01`, create orders for two recipients (for example `10423005` and `LEC001`), and click **Mark loaded** on each.
2. Open `/docs`, run `GET /api/qr/token` with the header `x-robot-key: dev-robot-key`, and copy the token value (the text starting with `eyJ`, without quotes).
3. In a new tab open `<server-address>/login.html?t=<TOKEN>` and sign in as one of the recipients. Only that recipient's compartment opens.
4. Back on the dispatch page, click **Mark collected** to free the compartment, then create the next order.

Try these cases to show the security rules:

| Try | Expected result |
|---|---|
| Sign in as a recipient who has no order | "No order ready for you" |
| Reuse a QR token that was already used | "QR code already used, scan again" |
| Wait more than 60 s before signing in | "QR code expired or invalid, scan again" |
| Open `login.html` without `?t=` as a recipient | "No QR code found. Scan the code on the robot." |
| Enter a wrong password 5 times | "Account temporarily locked" |
| Sign in as `staff01` on `login.html` | "Signed in as canteen staff. Loading by QR is not available yet..." |
| Sign in as `admin01` on `login.html` | "Admins do not collect orders. Please use the dispatch page." |

## Step 6: Run the unit tests

```bash
pytest -q
```

Expected result: `10 passed` (a deprecation warning about `httpx` is harmless). The tests use a temporary database and cover: happy path, QR reuse, wrong user, expired QR, missing QR, account lockout, role permissions, each recipient gets their own compartment, completing an order frees the compartment, and an order cannot be completed before it is unlocked.

## Step 7: Self-signed HTTPS (demo)

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 -subj "/CN=navbot.local"
uvicorn app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Phones show a certificate warning because the certificate is self-signed. Accept it for the demo. Never commit `key.pem` or `cert.pem` (they are listed in `.gitignore`).

## Environment variables (set these for real use)

| Variable | Default | Description |
|---|---|---|
| `JWT_SECRET` | development placeholder | Secret used to sign all JWTs. **Change it.** |
| `ROBOT_KEY` | `dev-robot-key` | Shared key for `/api/qr/token` and `/ws/robot`. **Change it.** |
| `DELIVERY_POINT` | `DP1` | Delivery point ID embedded in QR tokens |
| `DB_PATH` | `navbot.db` | Path to the SQLite file |
| `REQUIRE_ROBOT` | `0` | Set to `1` to return 503 from `/api/unlock` when no robot is connected, keeping compartments locked as required by UC04 |

## Handover to Module 2 (ROS 2)

- The QR node calls `GET /api/qr/token` (with `X-Robot-Key`) every 50 s, then draws a QR code for the URL `https://<ip>:8443/login.html?t=<token>`. The address must be the robot's real IP or host name, not `localhost`.
- The bridge node connects to `wss://<ip>:8443/ws/robot?key=<ROBOT_KEY>` and receives JSON messages:
  - `{"cmd":"UNLOCK","compartment":1,"order_id":3}` is converted into a UART UNLOCK frame (`[Header][Command][Timestamp][Checksum]`)
  - `{"event":"ORDER_CREATED","order_id":3,"compartment":1,"status":"CREATED"}` is forwarded to the mission manager

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `localhost refused to connect` in your browser while using Codespaces | The server runs on the Codespace, not on your PC. Use the forwarded address from the **Ports** tab |
| `{"detail":"Not Found"}` on `login.html` | The URL has extra text in the path, for example `.../.dev//localhost:8000/login.html`. Use `<address>/login.html?t=<TOKEN>` with a single `/` |
| Chrome searches Google instead of opening the link | The pasted text contains a space, a line break or quotes. Type the address part by hand, then paste only the token (the text starting with `eyJ`) |
| "QR code expired or invalid" | Tokens last 60 s. Request a new one from `GET /api/qr/token` |
| `409 No free compartment` | Both boxes are taken. Click **Mark collected** on finished orders, or reset the database |
| Login always fails after deleting `navbot.db` | Run `python seed.py` again before starting the server |
| Server starts but every page returns an empty 404 in Codespaces | The port forward is stale. In the **Ports** tab stop forwarding port 8000, add it again, and restart `uvicorn` if needed |
| `role is not defined` on the sign-in page | In `login.html` the login result must be read as `const { access_token, role } = await call(...)` |

## Remaining work

- Pickup is currently confirmed manually through `POST /api/orders/{id}/complete` (the "Mark collected" button on the dispatch page). Replace it with the `LOCK_STATUS` signal from the ECE closure sensor when it is available.
- Canteen staff must scan the robot's QR code before the loading compartment opens (full UC03). Loading is currently confirmed from the dispatch page.
- The server does not know the robot's mission state: unlock should only be valid while the robot is in `AWAITING_PICKUP`.
- Simultaneous order creation could pick the same compartment (the UPDATE does not check that the compartment is still EMPTY).
- Account lockout can be abused: anyone who knows a student, lecturer or employee ID can lock that account for 5 minutes.
- Overdue and cancelled orders are not handled yet.
- `JWT_SECRET` and `ROBOT_KEY` have development defaults; set environment variables for a real demo.