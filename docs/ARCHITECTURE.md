# Backend Architecture

FastAPI backend for the MSRF admin/coach portal (Vite SPA) and the public website (Next.js).

---

## 1. System context

```
  Website (Next.js)                         Admin + Coach portal (Vite SPA)
  www.<domain>                              admin.<domain>
   │  server-side fetch /public/* (ISR)      │  browser fetch, Bearer access token
   │  browser POST public forms              │  httpOnly refresh cookie (same-site)
   ▼                                         ▼
 ┌──────────────────────── api.<domain> ─────────────────────────┐
 │  Reverse proxy (Caddy / nginx / cloud LB): TLS, body size cap  │
 │  FastAPI (uvicorn workers)                                     │
 └───────┬──────────────┬───────────────┬──────────────┬─────────┘
         │              │               │              │
   PostgreSQL 16     Redis 7       Object storage    Celery worker + beat
   (data)            (rate limits,  (S3 / R2 /        (email, monthly fees,
                     Celery broker)  MinIO in dev)     cleanup jobs)
                                                          │
                                                        SMTP
```

All three apps sit under one registrable domain (`<domain>`). That makes `admin.` ↔ `api.` **same-site**, which lets the refresh cookie use `SameSite=Strict` and still work with cross-origin `fetch`. This is the main reason for the subdomain layout. **[Q17]** confirm domains.

---

## 2. Technology choices

| Concern | Choice | Notes |
|---------|--------|-------|
| Runtime | Python 3.12 | |
| Web | FastAPI + uvicorn | |
| Validation | Pydantic v2, `pydantic-settings` | |
| ORM | SQLAlchemy 2.0 **async** + `asyncpg` | Celery tasks use a separate **sync** engine (`psycopg`), because Celery workers are synchronous |
| Migrations | Alembic (async `env.py`) | |
| Password hashing | `pwdlib[argon2]` (Argon2id) | `passlib` is unmaintained; bcrypt truncates at 72 bytes |
| JWT | `PyJWT` | HS256, one secret, a single service. Asymmetric keys are unnecessary while no other service verifies tokens |
| Rate limiting | `limits` (Redis storage, moving window) wrapped in a FastAPI dependency | Avoids `slowapi`'s decorator magic and its `request` parameter requirement |
| Background jobs | Celery 5 + Redis broker, `celery beat` | See §8 |
| Email | `smtplib` / `aiosmtplib` + Jinja2 templates (HTML + text) | Mailpit in dev |
| Files | `boto3` (S3 API) + `filetype` (magic bytes) + Pillow | One code path for S3, R2 and MinIO |
| Logging | stdlib `logging` + `structlog`-style JSON formatter | Request ID per request |
| Errors to an external tracker | Sentry SDK (optional, env-gated) | |
| Tests | pytest, pytest-asyncio, httpx `AsyncClient`, testcontainers or a compose Postgres | Real Postgres, not SQLite (enums, `citext`, `pg_trgm`, `FOR UPDATE`) |
| Lint/format/types | ruff, mypy (strict on `app/`) | |
| Dependencies | `requirements.txt` + `requirements-dev.txt`, pinned | As requested. `uv pip compile` can generate them |

---

## 3. Project structure

**Recommendation: organise by feature module, not by layer.**

The brief proposes `models/`, `schemas/`, `services/`, `api/` folders, each holding one file per domain. With about 15 domains, every feature change touches four distant folders, and each folder grows to 15+ files. Grouping by feature keeps a domain's model, schema, service and router together, which makes them easier to find, review and delete. Shared infrastructure stays in `core/`. This is also the closest analogue to Django apps, without Django's implicit wiring.

**No repository layer.** SQLAlchemy's `AsyncSession` already is a unit of work and a repository (`session.get`, `select()`). Wrapping it in `StudentRepository.get_by_id()` adds a file per model and a layer of pass-through methods without adding a seam that is used. Services query directly. If a query is reused, it becomes a function in that module's `queries.py`.

```
Backend/
├── app/
│   ├── main.py                    # create_app(): middleware, routers, exception handlers, lifespan
│   ├── core/
│   │   ├── config.py              # Settings (pydantic-settings), env-specific validation
│   │   ├── database.py            # async engine, session factory, get_db dependency
│   │   ├── db_sync.py             # sync engine/session for Celery tasks
│   │   ├── base.py                # DeclarativeBase, naming convention, UUID/timestamp mixins
│   │   ├── security.py            # hash/verify password, JWT encode/decode, opaque token helpers
│   │   ├── errors.py              # AppError hierarchy + exception handlers
│   │   ├── logging.py             # JSON logging, request-id contextvar
│   │   ├── middleware.py          # request id, security headers, timing
│   │   ├── rate_limit.py          # RateLimit dependency factory (Redis)
│   │   ├── pagination.py          # PageParams dependency, Page[T] schema, paginate()
│   │   ├── schemas.py             # CamelModel base (alias_generator=to_camel)
│   │   ├── storage.py             # ObjectStorage (put/presign/delete), upload validation
│   │   ├── email.py               # EmailMessage builder + SMTP sender (used by tasks)
│   │   ├── counters.py            # next_number("receipt:2026")
│   │   ├── timeutils.py           # today_ist(), within_days()
│   │   └── redis.py               # Redis client lifecycle
│   ├── api/
│   │   ├── deps.py                # DbSession, CurrentUser, AdminUser, CoachUser, CurrentCoach
│   │   └── v1.py                  # includes every module router with prefix + tags
│   ├── modules/
│   │   ├── auth/                  # router, schemas, service, models (RefreshToken, AuthToken)
│   │   ├── users/                 # models (User, Role), service (create_admin), cli helpers
│   │   ├── coaches/               # models, schemas, service, router (admin)
│   │   ├── reference/             # categories, program types, training centers (one generic CRUD service)
│   │   ├── students/              # models, schemas, service, router, csv_io.py
│   │   ├── sessions/              # training sessions + attendance: service, router_coach, router_admin
│   │   ├── performance/           # reports + skills config: service, router_coach, router_admin
│   │   ├── fees/                  # ledger, payments, allocations: service, router, ledger_jobs.py
│   │   ├── payment_submissions/   # service, router_admin, router_public
│   │   ├── website/               # programmes, team, gallery, jobs, applications, enquiries
│   │   ├── notifications/         # model, service.notify_admins(), router
│   │   ├── files/                 # StoredFile model, upload service, /uploads, /files/{id}
│   │   ├── dashboard/             # summary + global search (read-only aggregate queries)
│   │   ├── coach_portal/          # /coach/dashboard, /coach/students (composes other services)
│   │   └── audit/                 # AuditLog model + record()
│   ├── tasks/
│   │   ├── celery_app.py          # Celery instance, beat schedule, task routing
│   │   ├── email_tasks.py         # send_email (retry with backoff)
│   │   ├── fee_tasks.py           # generate_monthly_ledger, overdue_digest
│   │   └── maintenance_tasks.py   # purge orphan files, expired tokens, old notifications
│   ├── templates/email/           # invite, password_reset, enquiry_received (*.html + *.txt)
│   ├── db_models.py               # imports every model (Alembic autogenerate + mapper config)
│   └── cli.py                     # create-admin, seed-dev, backfill-ledger
├── alembic/
│   ├── env.py
│   └── versions/
├── tests/
│   ├── conftest.py                # app, db (transaction-per-test), client, auth helpers, factories
│   ├── unit/                      # pure functions: security, csv parsing, fee status, time windows
│   └── integration/               # HTTP-level tests per module
├── docs/                          # this folder
├── .env.example
├── .gitignore
├── alembic.ini
├── docker-compose.yml             # postgres, redis, minio, mailpit, api, worker, beat
├── Dockerfile                     # multi-stage, non-root user
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml                 # ruff, mypy, pytest config only
└── README.md
```

Inside a module:
```
students/
├── models.py     # SQLAlchemy models
├── schemas.py    # Pydantic request/response models
├── service.py    # business logic; takes AsyncSession + typed args; raises AppError subclasses
├── router.py     # thin: parse → call service → return schema
└── csv_io.py     # module-specific helper
```

**Import rule** (prevents circular imports): `router → service → models/schemas → core`. Services may import other modules' **services** (for example, `payment_submissions.service` calls `fees.service.record_payment`). Never import routers from services. Models reference each other by string name in `relationship("Student")`, and `db_models.py` imports them all once.

---

## 4. Django → FastAPI map

| Django | FastAPI here | What is different |
|--------|--------------|-------------------|
| `settings.py` | `core/config.py` `Settings(BaseSettings)` | Typed, validated at startup, read from env/`.env`. Accessed via `get_settings()` (cached), not a global module |
| `urls.py` | `APIRouter` per module, included in `api/v1.py` | Routes live next to their handlers; prefix and tags set on include |
| Apps | `modules/*` | No auto-discovery. You import routers and models explicitly |
| Models / ORM | SQLAlchemy 2.0 `Mapped[]` models | No `Model.objects`. You write `await db.scalars(select(Student).where(...))`. Nothing is lazy-loaded in async — use `selectinload()` explicitly or you get an error instead of a silent N+1 |
| Migrations | Alembic | Autogenerate is a draft; **always read the generated file** |
| DRF Serializer | Pydantic schemas | Validation only. No `.save()`. Separate `StudentIn` / `StudentOut` instead of `read_only_fields` |
| `ViewSet` / `APIView` | Plain `async def` route functions | No class hierarchy. Shared behaviour goes into dependencies |
| `permission_classes` | Dependencies: `Depends(require_admin)` | Composable and visible in the signature. Object-level checks live in services |
| `request.user` | `current_user: CurrentUser` parameter (`Annotated[User, Depends(get_current_user)]`) | Explicit injection, easy to override in tests |
| Middleware | Starlette middleware (`app.add_middleware`) | Use only for cross-cutting HTTP concerns (request ID, headers). Auth is **not** middleware here; it is a dependency per route |
| Signals | Explicit service calls (`notifications.notify_admins(...)` inside `submit_payment()`) | No hidden side effects. Grep finds every caller |
| `transaction.atomic` | One `AsyncSession` per request; commit at the end of the service; `async with db.begin_nested()` for savepoints | The session is a dependency. Commit happens explicitly in the service, not implicitly per request |
| Django forms / `clean()` | Pydantic `field_validator` / `model_validator` | Cross-field rules (UPI ⇒ screenshot) go in `model_validator` |
| `django.contrib.auth` | Own `User` model + `core/security.py` | No built-in auth. That is why §5 exists |
| Admin site | None | The SPA is the admin |
| `manage.py` commands | `python -m app.cli <cmd>` (Typer) | |
| Celery | Celery | Same. Tasks use a sync DB session, because the async session cannot be shared with Celery |
| `DEBUG=True` error pages | Exception handlers in `core/errors.py` | You define the error format yourself |

---

## 5. Authentication

### 5.1 Where tokens live: the tradeoff

| Storage | XSS impact | CSRF impact | Survives reload | Verdict |
|---------|-----------|-------------|-----------------|---------|
| `localStorage` | Any injected script reads the token and sends it anywhere. A stolen token works until it expires, from any machine | None (not sent automatically) | Yes | **Rejected for long-lived tokens.** Exfiltration from one XSS bug means persistent account takeover |
| httpOnly cookie | Scripts cannot read it. XSS can still *use* the session while the page is open, but cannot steal it | Cookie is sent automatically → needs CSRF defence | Yes | **Used for the refresh token** |
| Memory (JS variable) | Readable by injected script while the tab is open, but short-lived and gone on reload | None | No | **Used for the access token** |

**Chosen design (admin SPA):**
- **Access token**: JWT, 15 min, returned in the JSON body, kept in memory, sent as `Authorization: Bearer`. Bearer headers are not sent automatically, so regular API calls need **no CSRF protection**.
- **Refresh token**: opaque 256-bit random string, stored **hashed** in `refresh_tokens`, delivered as cookie `msrf_refresh` with `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`, lifetime 7 days (idle) and 30 days (absolute) **[Q18]**.
- On page load the SPA calls `/auth/refresh` to get an access token. A reload does not log the user out.
- **Rotation**: every refresh issues a new refresh token and marks the old one replaced. Presenting a replaced token means it was stolen and replayed, so the whole family is revoked.
- **CSRF on cookie endpoints** (`/auth/refresh`, `/auth/logout`): `SameSite=Strict` (cross-site requests carry no cookie) **plus** an `Origin` header check against `CORS_ORIGINS`. Defence in depth, no CSRF token plumbing needed.
- **Why the refresh token is not a JWT**: it must be revocable (logout, deactivation, reuse detection), which requires a DB lookup anyway. An opaque token is simpler and leaks nothing if logged.

Dev note: `localhost:5173` and `localhost:8000` are the same *site* (ports do not matter), and browsers allow `Secure` cookies on `localhost`, so the same flow works locally.

The **website** has no logged-in users. It only uses public endpoints.

### 5.2 Access token claims
`sub` (user id), `role`, `ver` (token_version), `type: "access"`, `iat`, `exp`, `iss`, `aud`, `jti`.

### 5.3 Per-request check (`get_current_user`)
1. Parse the bearer token. Verify signature, `exp`, `iss`, `aud`, `type`.
2. Load the user by `sub` (a primary-key lookup).
3. Reject if `!is_active` or `ver != user.token_version`.

Loading the user on every request costs one indexed query. In return, deactivating a coach or resetting a password takes effect **immediately**, not after up to 15 minutes. At this scale that is the right trade.

### 5.4 Passwords and tokens
- Argon2id (pwdlib defaults). Rehash on login if parameters change.
- Policy: 10–128 chars, not equal to the email. No composition rules (NIST 800-63B).
- Reset and invite tokens: 32 random bytes, URL-safe, stored as SHA-256, single use, purpose-bound.
- Login timing: when the email is unknown, still verify against a dummy hash, so response time does not reveal which emails exist.

---

## 6. Authorization (RBAC)

```python
# api/deps.py (sketch)
CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: Role):
    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise Forbidden()
        return user

    return checker


AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]
CoachUser = Annotated[User, Depends(require_role(Role.COACH))]


async def get_current_coach(user: CoachUser, db: DbSession) -> CoachProfile: ...


CurrentCoach = Annotated[CoachProfile, Depends(get_current_coach)]
```

Router-level enforcement, so a new route cannot forget it:
```python
admin_router = APIRouter(dependencies=[Depends(require_role(Role.ADMIN))])
coach_router = APIRouter(prefix="/coach", dependencies=[Depends(require_role(Role.COACH))])
```

**Two layers:**
1. **Role** (dependency on the router): is this an admin endpoint?
2. **Object scope** (service): is *this* student, session or report within the coach's scope? Coach services always take `coach: CoachProfile` as their first argument and build queries that **start from the coach's scope** (`WHERE category_id IN coach_categories`). They never fetch by ID and then check afterwards. Out-of-scope objects return `404`, not `403`, so a coach cannot probe for existence.

**Why separate `/coach/*` endpoints** instead of one `/students` that behaves differently per role: coach responses use a **reduced schema** (no fees, no addresses), and the query must be scoped. Two explicit endpoints are easier to read, test and audit for IDOR than one endpoint with `if role == ...` branches.

Fixed roles, no permission tables. The frontend's permission matrix page is dead code, and two roles do not justify a configurable RBAC engine. If a third role appears (accountant, receptionist), add it to the enum and the dependencies.

---

## 7. Realtime: no WebSockets (for now)

The analysis found **no feature that needs server push**:
- No chat, no live dashboards, no collaborative editing.
- The only "live" element is the admin notification bell. Its events (a parent submits a payment, an enquiry arrives) happen a few times a day, for a handful of admins.

**Decision:** poll `GET /notifications/unread-count` every 60 s while the tab is visible (`document.visibilityState`). That is one indexed `COUNT` per admin per minute.

What WebSockets would cost: an authenticated socket handshake (tokens cannot go in headers from browsers, so ticket exchange), a connection manager, **Redis pub/sub fan-out** because in-memory managers break with more than one worker, sticky sessions or LB config, reconnection logic in the SPA, and a separate test harness. All of that for a 60-second improvement in latency on a bell icon.

**When to revisit:** if a feature needs sub-second updates (for example, a live attendance board, or coaches messaging admins). Prefer **Server-Sent Events** first (one-way, plain HTTP, works through proxies, auto-reconnect in the browser). The design hook already exists: `notifications.service.notify_*()` is the single place that creates notifications. Adding `redis.publish(f"user:{id}", ...)` there plus an SSE endpoint subscribing to that channel scales across workers.

---

## 8. Background work: BackgroundTasks vs Celery

| | FastAPI `BackgroundTasks` | Celery |
|-|---------------------------|--------|
| Runs | In the same process, after the response is sent | Separate worker process |
| Survives a crash or deploy | No (lost) | Yes (task stays in the broker until acknowledged) |
| Retries / backoff | No | Yes |
| Scheduling | No | Yes (`beat`) |
| Visibility | None | Task state, Flower if wanted |
| Cost | Zero | Worker + beat containers, broker |

**Rule used:**
- Anything that **must eventually happen**, talks to an external system, or is scheduled → **Celery**.
- Cheap in-process follow-ups where loss is acceptable → do it **inline** in the request. `BackgroundTasks` is not used at all, because no task fits the "fire and forget, may be lost" category.

| Job | Where | Why |
|-----|-------|-----|
| Send email (invite, reset, enquiry ack) | Celery, `autoretry_for=(SMTPException,)`, exponential backoff, max 5 | SMTP is slow/flaky; a lost reset email is a support ticket |
| Monthly ledger generation | Celery beat, 1st 00:05 IST | Scheduled; idempotent |
| Overdue digest | Celery beat, 11th 09:00 IST | Scheduled |
| Purge orphan uploads / expired tokens / old notifications | Celery beat, daily 03:00 IST | Housekeeping |
| Notification rows for admins | Inline | One `INSERT … SELECT`; must be in the same transaction as the submission |
| Image resize | Inline | ~100 ms; the admin waits for the thumbnail anyway |
| CSV import (≤ 1,000 rows) | Inline | Under 1 s; the user needs the per-row errors immediately |
| PDF generation | Not needed | The frontends print client-side |

**Transactional email dispatch:** enqueue the Celery task **after commit** (`send_email.delay(...)` called after `await db.commit()`). Otherwise the worker can run before the token row exists, or send an email for a transaction that rolled back.

Worker settings: `task_acks_late=True`, `worker_prefetch_multiplier=1`, `task_time_limit=60`, timezone `Asia/Kolkata`, JSON serializer only (no pickle).

Is Celery justified at all? Narrowly. Email retries and three scheduled jobs could be handled by a cron container and a DB-backed outbox. But Celery is standard, you know it from Django, and the brief asks for it. Keep it to the jobs above.

---

## 9. Rate limiting

**Implementation:** a dependency factory backed by Redis (`limits` library, moving-window strategy).

```python
@router.post("/login", dependencies=[Depends(RateLimit("login", "5/minute", key=ip_and_email)),
                                     Depends(RateLimit("login-ip", "100/hour", key=client_ip))])
```

| Endpoint group | Limits | Key |
|----------------|--------|-----|
| `POST /auth/login` | 5/min and 20/hour per (IP, email); 100/hour per IP | IP+email, IP |
| `POST /auth/forgot-password` | 3/hour per email; 10/hour per IP | email, IP |
| `POST /auth/reset-password` | 10/hour per IP | IP |
| `POST /auth/refresh` | 30/min per IP | IP |
| `POST /public/payment-submissions` | 5/hour per IP; 20/day per phone | IP, phone |
| `POST /public/enquiries`, `POST /public/jobs/*/applications` | 5/hour per IP | IP |
| `GET /public/*` | 120/min per IP | IP (mostly absorbed by Next.js ISR) |
| Authenticated API | 300/min per user | user id |
| `POST /uploads`, document uploads | 30/min per user | user id |
| `POST /coaches/{id}/resend-invite` | 5/hour per coach | coach id |

**Brute-force protection:** per-(IP, email) limits stop online guessing against one account. Per-IP limits stop spraying many accounts. **No hard account lockout**: lockout lets anyone lock the director out by typing wrong passwords. After 10 failures/hour on one email, failures are written to `audit_logs` (and optionally an admin notification).

**Client IP behind a proxy:** run uvicorn with `--proxy-headers --forwarded-allow-ips=<LB IPs>`. Only then is `X-Forwarded-For` honoured. Never trust `X-Forwarded-For` from arbitrary clients; otherwise attackers rotate fake IPs to bypass limits. Behind Cloudflare, use `CF-Connecting-IP` with Cloudflare IP ranges allowlisted.

**Redis down:** fail **closed** for auth and public-form endpoints (return `503`), fail **open** for authenticated reads. Logged as errors.

**Bot protection:** rate limits do not stop distributed spam on public forms. Recommend Cloudflare Turnstile verification on the three public POSTs **[Q10]**.

---

## 10. Files and uploads

- Storage: S3-compatible object storage. Two prefixes or buckets: `public/` (CDN, CMS images) and `private/` (everything else).
- Validation pipeline: size check while streaming (reject above the limit without buffering the whole body) → **magic-byte detection** (`filetype`) → allowlist per purpose → images **re-encoded with Pillow** (strips EXIF/GPS, defeats polyglots, caps 2,000 px) → generated storage key (`private/<purpose>/<yyyy>/<mm>/<uuid>.<ext>`) → `stored_files` row.
- The user's filename is never part of the storage key. It is kept only for `Content-Disposition: attachment; filename*=UTF-8''…`.
- Private downloads: `GET /files/{id}` authorises by `purpose` and the owning entity, then `302`s to a presigned URL valid for 5 minutes, with `ResponseContentDisposition=attachment`. Files are never served inline from the API origin.
- Limits: images 5–10 MB, documents 10 MB, CV 5 MB, CSV 2 MB. The reverse proxy caps request bodies at 12 MB.
- No antivirus scanning in v1. Files are only downloaded by staff, documents are not rendered by the backend, and images are re-encoded. **[Q19]** Add ClamAV if CVs or documents are ever opened by systems that auto-execute macros.

---

## 11. Errors and logging

```python
class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    detail = "Bad request"


class NotFound(AppError):
    status_code = 404  # code set per use: "STUDENT_NOT_FOUND"


class Conflict(AppError):
    status_code = 409


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


class BusinessRuleViolation(AppError):
    status_code = 422
```

Handlers registered in `main.py`:
- `AppError` → `{"detail", "code"}` with its status.
- `RequestValidationError` → `422` with `errors: [{field, message}]` (field paths converted to camelCase).
- `IntegrityError` → mapped by constraint name to `409` with a friendly code (for example `uq_users_email` → `EMAIL_EXISTS`). This handles races that pre-checks miss.
- `HTTPException` → same shape.
- `Exception` → `500 {"detail":"Internal server error","code":"INTERNAL_ERROR","requestId": "..."}`. Full traceback goes to the log and Sentry only.

Logging: JSON lines to stdout (`timestamp, level, logger, message, request_id, user_id, path, status, duration_ms`). Request ID comes from `X-Request-ID` or is generated, and is echoed in the response header. **Never log**: passwords, tokens, cookies, `Authorization` headers, full request bodies of public forms, or file contents. Phone numbers are masked in logs (`98******45`).

---

## 12. Configuration

`Settings(BaseSettings)` reads `.env` in dev and real environment variables in production.

```env
APP_ENV=development            # development | test | production
DEBUG=false
API_PREFIX=/api/v1
DATABASE_URL=postgresql+asyncpg://msrf:msrf@localhost:5432/msrf
DATABASE_URL_SYNC=postgresql+psycopg://msrf:msrf@localhost:5432/msrf
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=                       # empty: results not stored
JWT_SECRET_KEY=change-me                     # ≥ 32 random bytes in production
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
JWT_ISSUER=msrf-api
JWT_AUDIENCE=msrf-admin
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
TRUSTED_HOSTS=localhost,127.0.0.1
ADMIN_APP_URL=http://localhost:5173          # used to build email links
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=false
SMTP_FROM_EMAIL=no-reply@example.com
SMTP_FROM_NAME=MSRF Academy
S3_ENDPOINT_URL=http://localhost:9000
S3_REGION=auto
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_BUCKET_PRIVATE=msrf-private
S3_BUCKET_PUBLIC=msrf-public
PUBLIC_MEDIA_BASE_URL=http://localhost:9000/msrf-public
TURNSTILE_SECRET_KEY=                        # empty disables captcha (dev/test)
SENTRY_DSN=
TIMEZONE=Asia/Kolkata
```

Production guards in `Settings` (startup fails if violated): `DEBUG=false`; `JWT_SECRET_KEY` at least 32 chars and not the placeholder; `CORS_ORIGINS` contains no `*` and no `http://` origins; `TRUSTED_HOSTS` set; SMTP and S3 credentials present; docs disabled unless `DOCS_ENABLED=true`.

---

## 13. CORS and HTTP security

- `CORSMiddleware(allow_origins=settings.CORS_ORIGINS, allow_credentials=True, allow_methods=[GET, POST, PUT, PATCH, DELETE], allow_headers=[Authorization, Content-Type, X-Request-ID], max_age=600)`. Dev: `http://localhost:5173` (admin), `http://localhost:3000` (website). Production: the exact `https://admin.<domain>` and `https://www.<domain>`.
- The wildcard `*` is impossible with credentials and is rejected at startup in production.
- `TrustedHostMiddleware` with `TRUSTED_HOSTS`.
- Security headers middleware: `Strict-Transport-Security` (production), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` (JSON API; docs routes get a relaxed CSP).
- HTTPS terminates at the proxy; HTTP → HTTPS redirect at the proxy.

---

## 14. Security review checklist

| Area | Design |
|------|--------|
| JWT | HS256 with ≥ 256-bit secret; `alg` pinned on decode; `exp`/`iss`/`aud`/`type` checked; short TTL; `token_version` revocation |
| Passwords | Argon2id; reset tokens hashed, single use, 30 min; no plaintext passwords anywhere (removes the temp-password feature) |
| CSRF | Bearer header for the API; cookie only on `/auth/*` with `SameSite=Strict` + `Origin` allowlist |
| CORS | Explicit origins; credentials only for those |
| SQL injection | ORM / bound parameters only; `sort` parameter mapped through an allowlist dict, never interpolated |
| XSS | API returns JSON only; CMS text is stored raw and **must be rendered as text** by React (no `dangerouslySetInnerHTML`); CSV export formula-neutralised |
| Mass assignment | Separate `*In` schemas with `extra="forbid"`; services assign fields explicitly; `role`, `status` on coach creation, `amount_paid`, `reviewed_by` are never client-settable |
| Authorization bypass | Router-level role dependencies; tests assert every non-public route rejects anonymous and wrong-role callers (see §16, generated from the route table) |
| IDOR | Coach queries start from the coach's scope; `404` on out-of-scope; UUIDs; `/files/{id}` checks the owning entity |
| Brute force | §9 limits; dummy-hash timing equalisation; no enumeration on login or forgot-password |
| Uploads | §10 |
| Information leakage | Uniform error shape; no stack traces; generic `500`; `IntegrityError` mapped to codes; docs off in production |
| Secrets | Env only; `.env` git-ignored; `.env.example` placeholders; secrets never logged |
| SMTP | TLS (STARTTLS or 465); credentials from env; From address on a domain with SPF/DKIM/DMARC configured **[Q20]** |
| Debug | `DEBUG` forced false in production by the settings validator |
| Financial integrity | Row locks on verify/payment; DB check constraints; payments immutable (void instead); audit log |
| PII of minors | Student photos/documents private; coach view reduced; logs mask phones; retention policy **[Q12]** |
| Dependencies | Pinned; `pip-audit` in CI |

---

## 15. Data access patterns

- **One `AsyncSession` per request** via `get_db()`. Services call `await db.commit()` themselves at the end of a write. The dependency rolls back on exception.
- **Eager loading is explicit**: `selectinload()` for collections, `joinedload()` for many-to-one. `lazy="raise"` on relationships, so an accidental lazy load fails in tests instead of silently issuing queries (or crashing under async).
- **Pagination**: `LIMIT/OFFSET` with a separate `COUNT(*)` on the same filtered query. Fine for these sizes. Keyset pagination is unnecessary.
- **Concurrency**: `SELECT … FOR UPDATE` on the ledger entry and submission rows during payment/verify; unique constraints as the final arbiter.

---

## 16. Testing strategy

- **Real PostgreSQL** (compose service or testcontainers). Each test runs inside a transaction that is rolled back (SAVEPOINT pattern), so tests are isolated and fast.
- Redis: real instance on DB 15 (flushed per test), or `fakeredis` for unit tests.
- Celery: `task_always_eager=True` in tests, plus an in-memory email backend that records messages.
- S3: `moto` server mode.
- Factories: plain functions in `tests/factories.py` (`make_student(db, **overrides)`). `factory_boy` is optional.

Minimum suites:
1. **Auth**: login success/failure, inactive user, refresh rotation, refresh reuse → family revoked, logout, forgot (no enumeration), reset (expired, reused token), change password, invite acceptance, `token_version` invalidation.
2. **Authorization matrix**: iterate `app.routes`; for every non-public route assert anonymous → `401` and wrong role → `403`. New routes are covered automatically.
3. **IDOR**: coach A cannot read/update/delete coach B's sessions or reports, cannot mark students outside their categories, cannot fetch another coach's student or file → `404`.
4. **Role escalation**: tampered JWT (`role: ADMIN`, re-signed with a wrong key) → `401`; `alg: none` → `401`; extra `role` field in bodies → `422` (extra=forbid).
5. **Tokens**: expired access token → `401 TOKEN_EXPIRED`; wrong audience/issuer → `401`.
6. **Rate limiting**: the sixth login in a minute → `429` with `Retry-After`.
7. **Business**: one session per coach per day (including the concurrency race via two sessions); 7-day lock in IST around midnight; fee allocation and overpayment; verify twice → `409`; void reverses the ledger; monthly ledger job idempotent; CSV import all-or-nothing.
8. **Uploads**: wrong magic bytes with the right extension → `415`; oversize → `413`; EXIF stripped.

---

## 17. Local development and deployment

`docker-compose.yml` services: `postgres:16`, `redis:7`, `minio` (+ bucket init), `mailpit` (SMTP sink, web UI at :8025), `api` (uvicorn `--reload`), `worker` (celery worker), `beat` (celery beat).

```bash
cp .env.example .env
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli create-admin --email you@example.com --name "Your Name"
docker compose exec api python -m app.cli seed-dev        # demo categories/students/coaches (dev only)
# API docs: http://localhost:8000/api/docs   Mail: http://localhost:8025   MinIO: http://localhost:9001
```

Production: the same image runs `api` (`uvicorn app.main:app --workers N --proxy-headers --forwarded-allow-ips=…`; or gunicorn with uvicorn workers), `worker` and `beat` (**exactly one beat instance**). Managed Postgres with daily backups and point-in-time recovery. Managed Redis. Migrations run as a release step (`alembic upgrade head`) before new API instances start.

---

## 18. Implementation order (after sign-off)

1. Skeleton: config, DB, base models, errors, logging, health, docker-compose, CI (ruff, mypy, pytest).
2. Users + auth + RBAC dependencies + rate limiting + email task + CLI `create-admin`. Tests 1–6.
3. Files/uploads + reference data.
4. Coaches (invite flow) + students (CRUD, documents, CSV).
5. Sessions/attendance + coach portal.
6. Performance reports.
7. Fees ledger + payments + beat job; payment submissions (public + admin).
8. Website CMS + public endpoints; enquiries, jobs, applications.
9. Notifications, dashboard, search.
10. Hardening pass: security headers, production settings guard, load test on list endpoints, OpenAPI review.

Each step ends with passing tests and an updated OpenAPI spec that the frontend can start wiring against.
