# MSRF Backend

FastAPI backend for the MSRF admin/coach portal (`../MSRF ADMIN`, Vite SPA) and the public website (`../Website`, Next.js).

- Design documents: [`docs/`](docs/). Start with [`ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- Live API reference: http://localhost:8000/api/docs (Swagger) and http://localhost:8000/api/redoc

## What is implemented

All modules from the design are implemented: 129 operations under `/api/v1`.

| Area | Endpoints (prefix `/api/v1`) |
|------|------------------------------|
| Auth | `/auth/login, refresh, logout, me, forgot-password, reset-password, change-password` |
| Students | `/students` (CRUD, filters, documents, CSV import/export/template, filter options), `/students/{id}/attendance, fees, payments, performance-reports` |
| Coaches | `/coaches` (invite-based accounts, categories, documents, attendance, resend invite) |
| Reference data | `/categories`, `/program-types`, `/training-centers` |
| Coach portal | `/coach/dashboard, students, session-roster, sessions, performance-reports` |
| Attendance & reports (admin) | `/sessions`, `/attendance/students[/summary,/export]`, `/attendance/coaches[/export]`, `/performance-reports[/config]` |
| Fees | `/fees/ledger`, `/fees/summary`, `/fees/payments`, `/payments[/{id}/void]` |
| Payment verification | `/payment-submissions` (verify / reject) |
| Website CMS | `/programmes`, `/team-members`, `/gallery-items`, `/jobs`, `/job-applications`, `/enquiries` |
| Public website | `/public/programmes, team-members, gallery-items, gallery-categories, jobs, jobs/{id}/applications, enquiries, payment-submissions` |
| Other | `/notifications`, `/dashboard/summary`, `/search`, `/uploads`, `/files/{id}/content`, `/health/live, ready` |

Scheduled jobs (Celery beat, Asia/Kolkata): fee ledger generation daily at 00:05 (idempotent), overdue digest on the 11th, and daily maintenance at 03:00 (orphan uploads, expired tokens, old notifications, ledger consistency check).

## Run locally (Homebrew PostgreSQL + Redis, no Docker)

Requires PostgreSQL 16+, Redis 7+, and [uv](https://docs.astral.sh/uv/).

```bash
# once: database role + dev and test databases
psql -d postgres -c "CREATE ROLE msrf LOGIN PASSWORD 'msrf'"
createdb -O msrf msrf && createdb -O msrf msrf_test
for db in msrf msrf_test; do psql -d $db -c "CREATE EXTENSION IF NOT EXISTS citext; CREATE EXTENSION IF NOT EXISTS pg_trgm;"; done

# once: Python environment and config
uv venv --python 3.12 .venv
uv pip install -r requirements-dev.txt
cp .env.example .env          # then set JWT_SECRET_KEY and SMTP_* values

# schema, demo data, first admin
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed-dev        # optional demo data; prints demo passwords once
.venv/bin/python -m app.cli create-admin --email you@example.com --name "Your Name"

# run (three terminals, or see "Background" below)
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/celery -A app.tasks.celery_app worker -l info
.venv/bin/celery -A app.tasks.celery_app beat -l info -s logs/celerybeat-schedule
```

With `CELERY_TASK_ALWAYS_EAGER=true` you can skip the worker: tasks such as email run inside the API process.

**Background** (what is running now):
```bash
mkdir -p logs
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app > logs/api.log 2>&1 &
nohup .venv/bin/celery -A app.tasks.celery_app worker -l info > logs/worker.log 2>&1 &
nohup .venv/bin/celery -A app.tasks.celery_app beat -l info -s logs/celerybeat-schedule > logs/beat.log 2>&1 &
# stop:
pkill -f "uvicorn app.main:app"; pkill -f "celery -A app.tasks.celery_app"
```

### Email

`EMAIL_BACKEND=smtp` sends real email (invites, password resets, enquiry auto-replies) through the `SMTP_*` settings. `console` prints emails to the terminal; `memory` is for tests. With Gmail, use an App Password, and set `SMTP_FROM_EMAIL` to the Gmail address (Gmail rewrites other From addresses).

### Docker

`docker compose up -d` starts PostgreSQL, Redis, Mailpit (SMTP sink, UI at http://localhost:8025), the API, a Celery worker and beat. Then:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli create-admin --email you@example.com --name "Your Name"
```

## CLI

```bash
python -m app.cli create-admin --email … --name …     # prompts for the password
echo "$PW" | python -m app.cli create-admin --email … --name … --password-stdin
python -m app.cli seed-dev                            # demo data (refuses in production)
python -m app.cli seed-demo-activity                 # + 6 months of fees, payments, sessions, reports, gallery…
python -m app.cli generate-ledger                     # create this month's fee entries now
```

## Tests and checks

```bash
.venv/bin/pytest            # real PostgreSQL (msrf_test) + Redis DB 15; each test rolls back
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app          # strict
.venv/bin/alembic check     # models and migrations agree
```

`tests/integration/test_authorization.py` reads the OpenAPI schema and asserts that every non-public route rejects anonymous requests, so new routes are covered automatically.

## Migrations

```bash
.venv/bin/alembic revision --autogenerate -m "describe the change"   # then READ the generated file
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade -1
```

Autogenerate does not drop PostgreSQL enum types on downgrade (add them by hand, as in the existing migrations), and it can mis-render functional indexes. Review every generated migration.

## Differences from the design documents

These were decided during implementation; the design docs describe the original plan.

| Topic | Implemented | Why |
|-------|-------------|-----|
| Private file access | File responses carry a short-lived **signed URL** (`/files/{id}/content?expires&signature` locally, an S3 presigned URL in production) instead of `GET /files/{id}` with a bearer token | `<img src>` tags cannot send an `Authorization` header; the API only hands out links in responses the caller may see |
| Reference/CMS active flag | `status: ACTIVE/INACTIVE` column instead of `is_active` | Same value the API exposes; one enum across modules |
| Public forms | File parts are fields of the same multipart form (`screenshot`, `cv`) | FastAPI form models validate them together |
| Programme delete | Allowed even with enquiries (enquiries keep a `subject` snapshot) | Enquiries are history, not dependants |
| `refresh_tokens` | `replaced_at` + `absolute_expires_at` instead of `replaced_by_id` | Enables the 30-second multi-tab grace window |
| Fee ledger job | Runs daily, not only on the 1st | Idempotent; recovers automatically if beat was down on the 1st |
| Performance `reportPeriod` | Free text (unique per student) | Matches the current UI; see OPEN_QUESTIONS Q6 |
