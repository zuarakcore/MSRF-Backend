# Backend Requirements

Every feature the two frontends need, mapped to backend work. Exact request and response bodies are in `API_SPECIFICATION.md`. Tables are in `DATABASE_DESIGN.md`.

Conventions used below:
- **Auth**: `None` (public), `JWT` (any logged-in user), `JWT·ADMIN`, `JWT·COACH`.
- **RT**: whether realtime delivery is required. **BG**: background job (Celery) needed.
- Common errors are not repeated per feature: `401` (missing/expired token), `403` (wrong role or inactive account), `422` (validation), `429` (rate limit).
- All paths are under `/api/v1`.
- Items marked **[Q#]** depend on an open question in `OPEN_QUESTIONS.md`. The design uses the recommended answer.

---

## A. Authentication and accounts

### A1. Login
- **Frontend**: admin `pages/auth/LoginPage.tsx`
- **Backend**: verify email + password, check `is_active`, issue access token (JSON) and refresh token (httpOnly cookie). The role comes from the database. **The role selector on the login form is removed.**
- **Endpoint**: `POST /auth/login`
- **Entities**: User, RefreshToken
- **Validation**: email format; password 1–128 chars (no strength check on login).
- **Errors**: `401 INVALID_CREDENTIALS` (same message for unknown email and wrong password), `403 ACCOUNT_INACTIVE`, `429`.
- **RT** no · **BG** no.

### A2. Refresh / logout / me
- **Frontend**: admin `AuthContext` (to be rewritten)
- **Endpoints**: `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
- **Backend**: rotate refresh tokens; detect reuse and revoke the whole token family; logout revokes the family and clears the cookie. `me` returns the user and, for coaches, the coach profile ID.
- **Errors**: `401 INVALID_REFRESH_TOKEN`, `401 REFRESH_TOKEN_REUSED`, `403 ORIGIN_NOT_ALLOWED` (CSRF check on cookie endpoints).

### A3. Forgot / reset password
- **Frontend**: admin `LoginPage` forgot-password modal. **A new "Set password" page is needed** for the link in the email.
- **Endpoints**: `POST /auth/forgot-password`, `POST /auth/reset-password`
- **Backend**: create a single-use token (stored hashed, 30 min TTL) and email the link. Always return `202` (no account enumeration). Reset sets the password, bumps `token_version` (kills existing access tokens) and revokes all refresh tokens.
- **Validation**: new password 10–128 chars, not equal to email. **[Q7]** The frontend currently allows 6.
- **Errors**: `400 INVALID_OR_EXPIRED_TOKEN`.
- **BG**: yes (email).

### A4. Change own password
- **Frontend**: admin `SettingsPage` (admin). Coaches have no settings page today; the endpoint serves both roles.
- **Endpoint**: `POST /auth/change-password`
- **Errors**: `400 INVALID_CURRENT_PASSWORD`.
- Revokes other sessions; the current session gets a fresh refresh token.

### A5. Coach invitation (replaces "temporary password")
- **Frontend**: `CoachListPage` (create), `CoachProfilePage` / `CoachCredentialsModal` ("Login credentials" button)
- **Backend**: creating a coach creates a User **without a password** and emails an invite link (72 h, single use). The admin can resend. **The backend never generates, stores, or returns plaintext passwords.** The credentials modal becomes "Resend invite" plus invite status.
- **Endpoints**: `POST /coaches` (sends the invite), `POST /coaches/{id}/resend-invite`, `POST /auth/reset-password` (same endpoint, token purpose `INVITE`)
- **BG**: yes (email).

### A6. Registration
- **Not provided.** No frontend flow needs public sign-up. The only existing one (website `/auth`) is a security hole. Admins are created with a CLI command (`python -m app.cli create-admin`). Coaches are invited by admins.

---

## B. Students (admin)

### B1. Student roster list
- **Frontend**: `StudentListPage`
- **Endpoint**: `GET /students`
- **Query**: `page`, `page_size` (≤100), `search` (name, student code, parent name, phone), `category_id`, `program_type_id`, `training_center_id`, `birth_year`, `status`, `fee_status` (current month), `sort` (`full_name`, `-admission_date`, `student_code`)
- **Response**: paginated rows including attendance % and current-month fee status (computed in SQL, not N+1).
- **Auth**: JWT·ADMIN.

### B2. Create / update / status / delete student
- **Endpoints**: `POST /students`, `PATCH /students/{id}`, `DELETE /students/{id}`
- **Entities**: Student, Category, ProgramType, TrainingCenter, StoredFile (photo), FeeLedgerEntry
- **Backend**:
  - Generate `student_code` `MSRF-{admission year}-{NNN}` (concurrency-safe counter).
  - Generate `admission_number` if blank; it must be unique.
  - On create, open a fee ledger entry for the admission month.
  - A change to `monthly_fee` applies to **future** ledger months only (open months are untouched unless the admin edits them).
  - Status toggle is `PATCH {status}`.
  - Delete: `409 STUDENT_HAS_HISTORY` if attendance, payments or reports exist. The admin should deactivate instead. Financial history must not be cascade-deleted. **[Q5]**
- **Validation**: full name 2–120; DOB required, not in the future, age 3–25; phones normalised to 10-digit Indian mobile (`^[6-9]\d{9}$`); parent name and phone required; emails valid if given; FK IDs must exist **and be active**; monthly fee 0–1,000,000; batch is an enum; gender is an enum.
- **Errors**: `404 STUDENT_NOT_FOUND`, `409 ADMISSION_NUMBER_EXISTS`, `422 REFERENCE_INACTIVE`.

### B3. Student profile
- **Frontend**: `StudentProfilePage` tabs
- **Endpoints**: `GET /students/{id}`, `GET /students/{id}/attendance?year&month`, `GET /students/{id}/fees?year`, `GET /students/{id}/payments`, `GET /students/{id}/performance-reports`
- **Response**: profile, attendance records, 12-month ledger with status per month, payments with receipt numbers, report summaries.

### B4. Student documents
- **Endpoints**: `GET /students/{id}/documents`, `POST /students/{id}/documents` (multipart: title + file), `DELETE /students/{id}/documents/{doc_id}`, file download via `GET /files/{file_id}`
- **Validation**: PDF/JPEG/PNG/DOC/DOCX, ≤ 10 MB, checked by magic bytes as well as the extension.
- **Storage**: private bucket; downloads go through short-lived presigned URLs after an authorisation check.

### B5. CSV import / export
- **Frontend**: `StudentListPage` import modal and export button
- **Endpoints**: `GET /students/import-template` (CSV), `POST /students/import?dry_run=true|false` (multipart CSV), `GET /students/export` (same filters as the list)
- **Backend**: parse with `csv.DictReader`; resolve category/program/center **by name** (case-insensitive); validate every row; **all-or-nothing** insert in one transaction; return per-row errors. Limit 1,000 rows and 2 MB. Runs synchronously — a 1,000-row insert takes well under a second, so Celery is not justified here.
- **Export**: streamed CSV of the *full filtered set*. The client can no longer export only what it holds, because lists are now server-paginated.
- **Security**: neutralise CSV formula injection on export (prefix cells that start with `= + - @` with `'`).
- **Errors**: `422 CSV_INVALID` with `errors: [{row, field, message}]`.

### B6. Filter options
- **Endpoint**: `GET /students/filter-options` → distinct birth years plus active categories/programs/centers. This saves the UI three calls.

---

## C. Coaches (admin)

### C1. Coach list / create / update / status / delete
- **Frontend**: `CoachListPage`, `CoachProfilePage`
- **Endpoints**: `GET /coaches` (`search`, `status`, `page`), `POST /coaches`, `GET /coaches/{id}`, `PATCH /coaches/{id}`, `DELETE /coaches/{id}`
- **Entities**: User (role COACH) + CoachProfile, StoredFile (photo), CoachDocument (contract)
- **Backend**: create User + CoachProfile in one transaction, then send the invite (A5). Deactivating a coach sets `User.is_active=false`, bumps `token_version`, and revokes refresh tokens, so access stops immediately. Delete returns `409 COACH_HAS_HISTORY` if sessions or reports exist.
- **Validation**: email unique (case-insensitive); phone rule as for students; experience 0–60; joined date not in the future.
- **Errors**: `409 EMAIL_EXISTS`, `404 COACH_NOT_FOUND`.
- **Computed fields**: assigned student count, attendance average (from session coach records). `monthlyRating` has no data source and is **dropped** **[Q8]**.

### C2. Coach attendance history
- **Endpoint**: `GET /coaches/{id}/attendance?year&month` → sessions where the coach was creator or co-coach, with status.

### C3. Coach documents
- Same as B4 under `/coaches/{id}/documents`. The "contract" is a document with `kind=CONTRACT`.

### C4. Coach ↔ category assignment **[Q1]**
- **Endpoint**: `PUT /coaches/{id}/categories` `{category_ids: []}`, included in the coach detail.
- Defines which students a coach may see and mark. **Requires a small admin UI addition** (a multi-select on the coach form).

---

## D. Reference data (admin)

### D1. Categories, program types, training centers
- **Frontend**: `CategoriesCMSPage`, `ProgramTypesCMSPage`, `TrainingCentersPage`
- **Endpoints** (each): `GET /categories`, `POST`, `PATCH /{id}`, `DELETE /{id}`. Same for `/program-types` and `/training-centers`.
- **Auth**: list = JWT (coaches need categories); write = JWT·ADMIN.
- **Validation**: title/name 2–100, unique case-insensitive; description ≤ 500; phone rule.
- **Delete**: `409 IN_USE` if referenced by students or sessions. Deactivate instead.

---

## E. Training sessions and attendance

### E1. Coach: session roster
- **Frontend**: `CoachAttendancePage` step 3
- **Endpoint**: `GET /coach/session-roster?category_ids=…`
- **Backend**: returns active students in those categories. Categories must be within the coach's assigned categories **[Q1]**. Uses exact FK matching, replacing the fuzzy string matching.

### E2. Coach: create / update / delete session
- **Endpoints**: `POST /coach/sessions`, `GET /coach/sessions/{id}`, `PUT /coach/sessions/{id}`, `DELETE /coach/sessions/{id}`
- **Entities**: TrainingSession, SessionCategory, SessionCoach, SessionSplit, StudentAttendance
- **Backend rules** (authoritative):
  - Creator is the current coach. Co-coaches must be active coaches. All coaches on the session get `PRESENT` (matches current UI behaviour).
  - **One session per coach per date**, counting creator and co-coach roles → `409 SESSION_EXISTS_FOR_DATE`.
  - Date not in the future; ≤ 7 days in the past.
  - Edit/delete only by the **creator**, and only while `0 ≤ today_IST − session_date ≤ 7` → `403 SESSION_LOCKED`. **[Q4]** Should co-coaches be able to edit?
  - Every student in `attendance` must belong to the session's categories and be active → `422 STUDENT_NOT_IN_SESSION_CATEGORIES`.
  - Splits: 1–20; heading 1–120; minutes 1–300; explanation ≤ 2,000.
  - Daily topic and explanation required. Venue required (≤ 200).
  - The whole session is written in one transaction (PUT replaces splits and attendance).
- **Errors**: `404 SESSION_NOT_FOUND` (also returned for other coaches' sessions — never confirm existence across owners).

### E3. Coach: session list
- **Endpoint**: `GET /coach/sessions` with `search` (topic, venue), `category_id`, `year`, `month`, `date`, `page`
- Returns sessions where the coach is creator **or** co-coach, plus `can_edit` computed by the server.

### E4. Admin: reports center and attendance views
- **Frontend**: `ReportsCenterPage`, `AttendanceManagementPage`, `CoachProfilePage`
- **Endpoints**:
  - `GET /sessions` (`coach_id`, `date_from`, `date_to`, `category_id`, `page`), `GET /sessions/{id}`
  - `GET /attendance/students` — record stream (`year`, `month`, `date`, `category_id`, `status`, `search`, `page`)
  - `GET /attendance/students/summary` — per-student totals and rate band (`Good` ≥ 85, `Needs Attention` ≥ 70, `Critical`)
  - `GET /attendance/coaches` — coach attendance stream
  - `GET /attendance/students/export`, `GET /attendance/coaches/export` (CSV)
- **Auth**: JWT·ADMIN. Admin attendance is read-only, as in the UI. **[Q4]** Admin override of locked sessions?
- **Validation**: no future year or month (matches UI); `year` between 2020 and the current year.

---

## F. Performance reports

### F1. Coach: CRUD
- **Frontend**: `CoachPerformancePage`
- **Endpoints**: `GET /coach/performance-reports` (`search`, `page`), `POST`, `GET /{id}`, `PUT /{id}`, `DELETE /{id}`
- **Backend rules**:
  - Student must be in the coach's scope **[Q1]** → `404 STUDENT_NOT_FOUND` if not (no existence leak).
  - Exactly the 15 configured skills, each rated 1–5 with an optional comment (≤ 500).
  - Development goals: subset of the 7 standard goals; custom goal ≤ 300.
  - Overall rating 1–5; strong foot is an enum; position ≤ 60; report period ≤ 60.
  - Edit/delete only by the author, within 7 days (IST) of `recorded_date` → `403 REPORT_LOCKED`.
  - DOB and age are **read from the student**, not stored (the UI currently copies and lets the coach type them).
  - **[Q6]** One report per student per period? Recommended: unique `(student_id, report_period)` → `409`.

### F2. Admin: read
- **Endpoints**: `GET /performance-reports` (`coach_id`, `student_id`, `date_from`, `date_to`, `page`), `GET /performance-reports/{id}`

### F3. Configuration
- **Endpoint**: `GET /performance-reports/config` → the 15 skills, standard goals, positions. Removes the hard-coded lists from the UI.

---

## G. Coach portal (read)

- `GET /coach/dashboard` → my student count, attendance rate this month, reports count, today's session status, recent sessions.
- `GET /coach/students` (`search`, `category_id`, `page`) and `GET /coach/students/{id}` → **reduced schema**: no fees, no parent address, no documents. **[Q9]** Confirm which fields a coach may see; at minimum the emergency contact.
- `GET /coach/students/{id}/performance-reports`.
- Other coaches' students → `404`.

---

## H. Fees and payments

### H1. Monthly fee ledger
- **Frontend**: `FeeManagementPage`, `StudentProfilePage` fees tab, dashboard
- **Model**: one `FeeLedgerEntry` per (student, month): `amount_due` (snapshot of monthly fee), `discount`, `amount_paid`, `due_date` (10th). Status is computed: `PAID` if paid + discount ≥ due; `OVERDUE` if unpaid and today > due date; otherwise `PENDING`.
- **Endpoints**: `GET /fees/ledger` (`year`, `month` (optional → annual aggregate), `category_id`, `status`, `search`, `page`), `GET /fees/summary` (same filters → expected, collected, outstanding, pending count)
- **BG**: **Celery beat** on the 1st of each month (00:05 IST) creates entries for all active students. It is idempotent (unique constraint), so re-runs are safe. A CLI backfill command covers historic months. **[Q3]**

### H2. Record payment (cash desk / bank / UPI / cheque)
- **Endpoint**: `POST /fees/payments`
- **Body**: `student_id`, `paid_on`, `mode`, `reference`, `remarks`, and `allocations: [{year, month, amount}]` plus optional `discount: {year, month, amount, reason}`.
- **Backend**: in one transaction, create a Payment with a receipt number `MSRF-RCPT-{YYYY}-{NNNNN}`, create allocations, update ledger `amount_paid`/`discount`, and write an audit log. Allocation to a month cannot exceed its outstanding amount → `422 OVERPAYMENT`. The sum of allocations must equal the payment amount.
- **Auth**: JWT·ADMIN.
- **Errors**: `404 LEDGER_MONTH_NOT_FOUND` (student not enrolled that month), `422 OVERPAYMENT`, `422 DISCOUNT_REASON_REQUIRED`.

### H3. Receipts ("invoices")
- **Frontend**: `InvoiceListPage`, invoice modals in the profile
- **Endpoints**: `GET /payments` (`student_id`, `date_from`, `date_to`, `mode`, `search`, `page`), `GET /payments/{id}` (receipt data: student, parent, items = allocations with month labels)
- PDFs stay client-side (print).
- **[Q2]** Real pre-payment invoices with GST? Not designed. GST fields are always 0 in the UI.

### H4. Void payment
- Not in the UI, but financial records need a correction path that is not "delete". `POST /payments/{id}/void` `{reason}` reverses the allocations and keeps the record. Recommended; small. **[Q2]**

---

## I. Parent payment submissions

### I1. Public submit
- **Frontend**: website `/submit-payment-proof`
- **Endpoint**: `POST /public/payment-submissions` (multipart)
- **Fields**: `student_name` 2–120, `parent_mobile` (normalised Indian mobile), `payment_method` `UPI|CASH`, `amount` 1–1,000,000, `payment_date` (not in the future, ≤ 180 days ago), `transaction_reference` ≤ 80 (optional), `handed_over_to` (required for CASH), `screenshot` (required for UPI; JPEG/PNG ≤ 5 MB, magic-byte checked, re-encoded with Pillow to strip EXIF/GPS and any payload).
- **Backend**: create submission `SUB-{YYYY}-{NNNNN}`; precompute candidate students by exact normalised parent phone; notify all admins.
- **Auth**: None. **Rate limit**: 5/hour/IP and 20/day/phone. **Bot check**: Cloudflare Turnstile token **[Q10]**.
- **Response**: `201 {submission_number}`. Show it to the parent as a reference.
- **BG**: notification fan-out is a cheap DB insert done inline. Admin email digest optional.

### I2. Admin review
- **Frontend**: `PaymentVerificationPage`
- **Endpoints**: `GET /payment-submissions` (`status` default `PENDING`, `search`, `page`), `GET /payment-submissions/{id}` (includes `candidate_students`), `PATCH /payment-submissions/{id}` (`student_name`, `remarks`), `POST /payment-submissions/{id}/verify`, `POST /payment-submissions/{id}/reject` `{reason}`, screenshot via `GET /files/{file_id}`
- **Verify body**: `student_id` (**admin explicitly chooses**; auto-match only suggests), `allocations: [{year, month, amount}]`, `mode` defaults to the submission method. Creates a Payment linked to the submission (H2 logic), sets `VERIFIED`, `reviewed_by`, `reviewed_at`.
- **Rules**: only `PENDING` submissions can be verified or rejected → `409 SUBMISSION_ALREADY_REVIEWED`. Uses `SELECT … FOR UPDATE` to stop double-verification by two admins.
- **Rejection notice to parent**: no channel exists (no parent email on the form). The UI toast "notice issued to parent" is false. **[Q11]** SMS/WhatsApp is out of scope unless requested.

---

## J. Website CMS (admin) and public content

### J1. Programmes
- **Admin**: `GET/POST /programmes`, `PATCH/DELETE /programmes/{id}`, `PUT /programmes/order`
- **Public**: `GET /public/programmes`, `GET /public/programmes/{slug}` (active only)
- **Fields**: union of the admin and website shapes: `slug` (auto from title, unique), `title`, `age_group`, `description`, `duration`, `training_days`, `coach_label`, `benefits[]` (≤ 10 × 120 chars), `is_active`, `sort_order`. `enquiries_count` computed. **The admin form needs the extra fields.**

### J2. Team members
- **Admin**: CRUD at `/team-members` (+ photo upload) and `PUT /team-members/order`. **Public**: `GET /public/team-members`.
- **Fields**: name, designation (free text, stored upper-cased to match UI), biography ≤ 500, photo, active, sort order.

### J3. Gallery
- **Admin**: CRUD at `/gallery-items` (multipart for the image), `GET /gallery-items/categories`
- **Public**: `GET /public/gallery-items?category&page` (paginated; the brief mentions infinite scroll), `GET /public/gallery-categories`
- **Fields**: title, caption, category (free text, trimmed, ≤ 40), image, active, `is_wide` (website layout hint), sort order.
- **Images**: JPEG/PNG/WebP ≤ 10 MB, re-encoded and resized (max 2,000 px) plus a thumbnail (600 px). Stored in the **public** bucket (served via CDN).
- **BG**: thumbnail generation for one image takes about 100 ms. Do it inline. Move it to Celery only if bulk upload is added.

### J4. Jobs (careers)
- **Admin**: CRUD at `/jobs`. **Public**: `GET /public/jobs` (status OPEN and closing date not passed).
- **Fields**: title, location, description ≤ 5,000, experience required, posted on, closing date (≥ posted on), status OPEN/CLOSED. `applications_count` computed.

### J5. Job applications
- **Public**: `POST /public/jobs/{job_id}/applications` (multipart). Fields as in the website form + CV (PDF/DOC/DOCX ≤ 5 MB, magic-byte checked). `409 JOB_CLOSED` if the job is closed. Rate limit 5/hour/IP. Bot check **[Q10]**.
- **Admin**: `GET /job-applications` (`job_id`, `status`, `search`, `page`), `GET /{id}`, `PATCH /{id}` (status; contact fields), `DELETE /{id}`, CV via `GET /files/{file_id}`.
- **BG**: notify admins (inline DB insert); optional email to HR.
- **Retention**: CVs are personal data. **[Q12]** Purge after N months?

### J6. Contact enquiries
- **Public**: `POST /public/enquiries` — `parent_name`, `player_name`, `phone`, `email`, `programme_id` (optional; must be active), `message` ≤ 2,000. Rate limit 5/hour/IP. Bot check. **The website form must get `name` attributes and an API call — it currently sends nothing.**
- **Admin**: `GET /enquiries` (`status`, `programme_id`, `search`, `page`), `PATCH /{id}` (status, contact fields), `DELETE /{id}`.
- **BG**: notify admins; optional auto-reply email to the parent **[Q13]**.

### J7. Events
- The website shows events (trials, clinics, tournaments) with **no admin CMS**. **[Q14]** Not designed until confirmed.

---

## K. Notifications

- **Frontend**: admin `Header` bell, `NotificationsPage`, `NotificationContext`
- **Endpoints**: `GET /notifications` (`unread_only`, `page`), `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`
- **Producers**: payment submission (PAYMENT), enquiry (ENQUIRY), job application (APPLICATION), student created (ADMISSION), fee job (FEE_DUE summary on the 11th: "N students overdue").
- **Recipients**: one row per active admin (fan-out on insert; there are only a handful of admins).
- **RT**: **not required.** The bell polls `unread-count` every 60 s while the tab is visible. See ARCHITECTURE.md §7 for why WebSockets are not used and when to add SSE.
- Users can only read and mark their own notifications (`404` otherwise).

---

## L. Dashboard and search (admin)

- `GET /dashboard/summary` → total students, active coaches, outstanding fees, this month's collection, this year's collection, pending verifications, CMS counts, recent admissions (5), recent submissions (4), top overdue (5). One endpoint with aggregate SQL. Cache in Redis for 60 s if needed later.
- `GET /search?q=` (≥ 2 chars) → up to 5 each of students, coaches, payment submissions, payments (receipt number). Uses `pg_trgm` indexes.

---

## M. Files

- `GET /files/{file_id}` → `302` to a presigned URL (5 min TTL) after checking that the caller may see the file's owning entity. Public-bucket files are served directly by CDN URL and never pass through this endpoint.
- Admin uploads in JSON-based forms use **two steps**: `POST /uploads` (multipart, `purpose`) → `{file_id}`, then send `photo_file_id` in the JSON body. Public forms use **single-step multipart**, so anonymous users cannot create orphan files.
- **BG**: Celery beat deletes unreferenced uploads older than 24 h.

---

## N. Operational

- `GET /health/live` (process up), `GET /health/ready` (DB + Redis reachable). No auth; excluded from rate limits.
- OpenAPI at `/api/docs` and `/api/redoc`. **Disabled in production** or protected **[Q15]**.

---

## Summary: realtime and background needs

| Need | Mechanism | Why |
|------|-----------|-----|
| Password reset / invite / notification emails | Celery task | SMTP is slow and flaky; needs retries; must not block the request |
| Monthly fee ledger generation | Celery beat (1st, 00:05 IST) | Scheduled, idempotent |
| Overdue digest notification | Celery beat (11th) | Scheduled |
| Orphan upload cleanup | Celery beat (daily) | Scheduled |
| Expired token cleanup | Celery beat (daily) | Housekeeping |
| CSV import | Synchronous | ≤ 1,000 rows; the user waits for the per-row result anyway |
| Image resize | Synchronous | ~100 ms per image |
| Admin notifications | DB insert + 60 s polling | Few admins, low event rate |
| WebSockets | **None** | No feature needs push |
