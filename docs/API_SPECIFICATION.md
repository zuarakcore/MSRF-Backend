# API Specification — v1

Contract between the FastAPI backend and the two frontends (admin SPA, Next.js website).
The live, generated reference will be at `/api/docs` (Swagger) and `/api/redoc`. This document is the design target.

---

## 1. Conventions

### 1.1 Base URL and format
- Base: `https://api.<domain>/api/v1` (dev: `http://localhost:8000/api/v1`).
- JSON in and out, UTF-8. **Field names are camelCase on the wire** (Python uses snake_case internally; Pydantic converts). Query parameters are also camelCase.
- IDs are UUID strings. Human-readable codes (`studentCode`, `receiptNumber`, `submissionNumber`) are separate fields.
- Dates: `YYYY-MM-DD`. Timestamps: ISO 8601 with offset, in UTC (`2026-10-05T08:00:00Z`). "Today", due dates and 7-day locks are evaluated in **Asia/Kolkata**.
- Money: JSON number, rupees, at most 2 decimals (`2500` or `2500.5`).
- Enums are UPPER_SNAKE (`ACTIVE`, `PENDING`, `UNDER_REVIEW`). The frontend maps them to labels.

### 1.2 Authentication
- Send `Authorization: Bearer <accessToken>` on every authenticated request.
- The access token lives **in memory** (a React context variable, never `localStorage`). TTL 15 minutes.
- The refresh token is an **httpOnly cookie** set by the API (`msrf_refresh`, `Path=/api/v1/auth`, `Secure`, `SameSite=Strict`). JavaScript cannot read it.
- On app load and on any `401` with code `TOKEN_EXPIRED`, call `POST /auth/refresh` with `credentials: 'include'`, then retry once. Serialize refreshes (one in flight at a time).
- Cookie endpoints (`/auth/login`, `/auth/refresh`, `/auth/logout`) must be called with `credentials: 'include'`. The API checks the `Origin` header against the allowlist.

```ts
// admin: minimal fetch wrapper sketch
await fetch(`${API}/auth/refresh`, { method: 'POST', credentials: 'include' });
await fetch(`${API}/students`, { headers: { Authorization: `Bearer ${accessToken}` } });
```

### 1.3 Pagination
Query: `page` (≥1, default 1), `pageSize` (1–100, default 20).
```json
{ "items": [ ... ], "total": 134, "page": 1, "pageSize": 20, "pages": 7 }
```

### 1.4 Errors
Every error has the same shape:
```json
{ "detail": "Student not found", "code": "STUDENT_NOT_FOUND" }
```
Validation errors (`422`) add field-level detail:
```json
{
  "detail": "Validation error",
  "code": "VALIDATION_ERROR",
  "errors": [ { "field": "parentPhone", "message": "Enter a valid 10-digit Indian mobile number" } ]
}
```
CSV import errors use `"errors": [{ "row": 7, "field": "dateOfBirth", "message": "..." }]`.

Why `code`: the UI shows toasts with a title and message. A stable `code` lets the UI choose wording and behaviour without parsing English text.

Common status codes:

| Status | Code(s) | Meaning |
|--------|---------|---------|
| 400 | `INVALID_OR_EXPIRED_TOKEN`, `INVALID_CURRENT_PASSWORD` | Bad request semantics |
| 401 | `NOT_AUTHENTICATED`, `TOKEN_EXPIRED`, `TOKEN_INVALID`, `INVALID_CREDENTIALS` | Log in or refresh |
| 403 | `FORBIDDEN`, `ACCOUNT_INACTIVE`, `SESSION_LOCKED`, `REPORT_LOCKED`, `ORIGIN_NOT_ALLOWED` | Not allowed |
| 404 | `*_NOT_FOUND` | Missing, **or exists but is not yours** |
| 409 | `EMAIL_EXISTS`, `IN_USE`, `SESSION_EXISTS_FOR_DATE`, … | Conflict with current state |
| 413 | `FILE_TOO_LARGE` | Upload over the limit |
| 415 | `UNSUPPORTED_FILE_TYPE` | Bad MIME / magic bytes |
| 422 | `VALIDATION_ERROR`, business-rule codes | Invalid input |
| 429 | `RATE_LIMITED` | Has a `Retry-After` header |
| 500 | `INTERNAL_ERROR` | No internals exposed |

### 1.5 Roles
`ADMIN` (the frontend currently calls it `SUPER_ADMIN`) and `COACH`. The role is read from the database on every request. Nothing the client sends can change it.

| Prefix | Who |
|--------|-----|
| `/auth/*` | Public or any user (per endpoint) |
| `/public/*` | Anonymous (website) |
| `/coach/*` | `COACH` only, automatically scoped to the calling coach |
| everything else | `ADMIN` unless stated |

### 1.6 Uploads
- **Admin JSON forms** (student photo, coach photo, team photo): `POST /uploads` first, then put the returned `id` in the JSON body (`photoFileId`). Unused uploads are deleted after 24 h.
- **Document and gallery uploads, public forms**: one multipart request containing fields and file.
- File responses include `url`: a CDN URL for public images, or `/api/v1/files/{id}` for private files (redirects to a 5-minute signed URL; send the bearer token).

---

## 2. Shared schemas

```ts
type UUID = string;
type Status = 'ACTIVE' | 'INACTIVE';
type Gender = 'MALE' | 'FEMALE' | 'OTHER';
type BloodGroup = 'A+'|'A-'|'B+'|'B-'|'AB+'|'AB-'|'O+'|'O-';
type Batch = 'MORNING' | 'EVENING' | 'WEEKEND';          // labels: "Morning (6:00 AM - 8:00 AM)", ...
type Relationship = 'FATHER' | 'MOTHER' | 'GUARDIAN' | 'OTHER';
type AttendanceStatus = 'PRESENT' | 'ABSENT' | 'INFORMED';
type FeeStatus = 'PAID' | 'PENDING' | 'OVERDUE';
type PaymentMode = 'CASH' | 'BANK_TRANSFER' | 'UPI' | 'CHEQUE';

interface FileRef { id: UUID; fileName: string; contentType: string; sizeBytes: number; url: string; }

interface UserOut {
  id: UUID; email: string; fullName: string; role: 'ADMIN' | 'COACH';
  isActive: boolean; coachId: UUID | null; lastLoginAt: string | null;
}

interface RefItem { id: UUID; name: string; }           // embedded category/programType/trainingCenter
```

---

## 3. Authentication — `/auth`

### POST /auth/login
Auth: none · Rate limit: 5/min per IP+email, 20/hour per email, 100/hour per IP
```json
// request
{ "email": "coach@example.com", "password": "********" }
// 200 response  (+ Set-Cookie: msrf_refresh=...; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth)
{ "accessToken": "eyJ...", "tokenType": "bearer", "expiresIn": 900, "user": UserOut }
```
Errors: `401 INVALID_CREDENTIALS`, `403 ACCOUNT_INACTIVE`, `403 ORIGIN_NOT_ALLOWED`, `429`.
**The role selector and demo credentials on the login page must be removed.** The UI routes by `user.role`.

### POST /auth/refresh
Auth: refresh cookie · Rate limit: 30/min per IP
Request: empty body, `credentials: 'include'`. Response `200`: same as login (new cookie set).
Errors: `401 INVALID_REFRESH_TOKEN`, `401 REFRESH_TOKEN_REUSED` (all sessions for the user are revoked; force re-login).

### POST /auth/logout
Auth: refresh cookie (access token optional) · Response `204`, cookie cleared.

### GET /auth/me
Auth: JWT · Response `200 UserOut`.

### POST /auth/forgot-password
Auth: none · Rate limit: 3/hour per email, 10/hour per IP
```json
{ "email": "admin@example.com" }
```
Response: **always `202`** `{ "detail": "If the account exists, an email has been sent." }`

### POST /auth/reset-password
Auth: none · Rate limit: 10/hour per IP. Used for both password reset and coach invitation links.
```json
{ "token": "<from email link>", "newPassword": "********" }
```
Response `204`. Errors: `400 INVALID_OR_EXPIRED_TOKEN`, `422` (password policy: 10–128 chars, not the email).
**New admin page needed**: `/set-password?token=...`.

### POST /auth/change-password
Auth: JWT
```json
{ "currentPassword": "********", "newPassword": "********" }
```
Response `200` (same body as login: new access token + cookie; other sessions revoked). Errors: `400 INVALID_CURRENT_PASSWORD`, `422`.

---

## 4. Students — `/students` (ADMIN)

```ts
interface StudentIn {                       // POST body; PATCH = all fields optional
  fullName: string;                         // 2–120
  gender: Gender;
  bloodGroup?: BloodGroup | null;
  dateOfBirth: string;                      // not in the future; age 3–25
  phone?: string | null;                    // normalised to 10 digits
  email?: string | null;
  address?: string | null;                  // ≤ 300
  photoFileId?: UUID | null;
  admissionNumber?: string | null;          // auto ADM-YYYY-NNN if empty; unique
  admissionDate: string;
  categoryId: UUID; programTypeId: UUID; trainingCenterId: UUID;   // must be ACTIVE
  batch: Batch;
  monthlyFee: number;                       // 0–1,000,000
  status?: Status;                          // PATCH only (status toggle)
  remarks?: string | null;
  parentName: string; parentRelationship: Relationship;
  parentPhone: string; parentEmail?: string | null; parentAddress?: string | null;
  emergencyName?: string | null; emergencyRelationship?: string | null; emergencyPhone?: string | null;
}

interface StudentListItem {
  id: UUID; studentCode: string; fullName: string; photo: FileRef | null;
  dateOfBirth: string; gender: Gender; phone: string | null;
  category: RefItem; programType: RefItem; trainingCenter: RefItem; batch: Batch;
  parentName: string; parentPhone: string; status: Status;
  monthlyFee: number;
  attendancePercentage: number | null;      // last 90 days; null if no sessions
  currentMonthFeeStatus: FeeStatus | null;
}

interface StudentDetail extends StudentListItem, Omit<StudentIn, 'photoFileId'|'categoryId'|'programTypeId'|'trainingCenterId'> {
  admissionNumber: string;
  totals: { present: number; absent: number; informed: number;
            feeDueToDate: number; paidToDate: number; discountToDate: number; outstanding: number; };
  createdAt: string; updatedAt: string;
}
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/students` | `search, categoryId, programTypeId, trainingCenterId, birthYear, status, feeStatus, sort (fullName\|-admissionDate\|studentCode), page, pageSize` | `200 Page<StudentListItem>` | |
| GET | `/students/filter-options` | birth years + active reference lists | `200 {birthYears:number[], categories:RefItem[], programTypes:RefItem[], trainingCenters:RefItem[]}` | |
| POST | `/students` | `StudentIn` | `201 StudentDetail` | `409 ADMISSION_NUMBER_EXISTS`, `422 REFERENCE_INACTIVE` |
| GET | `/students/{id}` | | `200 StudentDetail` | `404 STUDENT_NOT_FOUND` |
| PATCH | `/students/{id}` | partial `StudentIn` (incl. `status`) | `200 StudentDetail` | `404`, `409`, `422` |
| DELETE | `/students/{id}` | only if no history | `204` | `409 STUDENT_HAS_HISTORY` |
| GET | `/students/{id}/attendance` | `year, month` | `200 {items:[{sessionId,date,status,remarks,categories:string[],coachName}], summary:{present,absent,informed,rate}}` | |
| GET | `/students/{id}/fees` | `year` (default current) | `200 {year, months:[LedgerMonth]}` | |
| GET | `/students/{id}/payments` | | `200 Payment[]` | |
| GET | `/students/{id}/performance-reports` | | `200 PerformanceReportSummary[]` | |
| GET | `/students/{id}/documents` | | `200 Document[]` | |
| POST | `/students/{id}/documents` | multipart `title` (1–120), `file` (PDF/JPEG/PNG/DOC/DOCX ≤ 10 MB) | `201 Document` | `413`, `415` |
| DELETE | `/students/{id}/documents/{documentId}` | | `204` | `404` |
| GET | `/students/import-template` | | `200 text/csv` | |
| POST | `/students/import` | multipart `file` (CSV ≤ 2 MB, ≤ 1,000 rows), `?dryRun=true` | `200 {created:number, dryRun:boolean}` | `422 CSV_INVALID` with row errors |
| GET | `/students/export` | same filters as list | `200 text/csv` (streamed) | |

```ts
interface Document { id: UUID; title: string; kind: 'GENERAL'|'CONTRACT'; file: FileRef; uploadedAt: string; uploadedBy: string; }
```

CSV columns (template): `Full Name, Gender, Blood Group, Date of Birth, Phone, Email, Address, Admission Date, Category, Program Type, Training Center, Batch, Monthly Fee, Parent Name, Parent Relationship, Parent Phone, Parent Email, Emergency Name, Emergency Phone`. Reference columns match **by name**, case-insensitive.

---

## 5. Coaches — `/coaches` (ADMIN)

```ts
interface CoachIn {
  fullName: string; email: string;          // email = login; unique
  phone: string; gender?: Gender | null; bloodGroup?: BloodGroup | null;
  experienceYears: number;                  // 0–60
  joinedDate: string; address?: string | null; bio?: string | null;  // bio ≤ 1,000
  photoFileId?: UUID | null;
  categoryIds?: UUID[];                     // students this coach can see/mark (see OPEN_QUESTIONS Q1)
  status?: Status;                          // PATCH only
}
interface CoachListItem {
  id: UUID; userId: UUID; fullName: string; email: string; phone: string; photo: FileRef | null;
  experienceYears: number; joinedDate: string; status: Status;
  inviteStatus: 'PENDING' | 'ACCEPTED' | 'EXPIRED';
  categories: RefItem[]; studentCount: number; attendanceRate: number | null;   // last 90 days
}
interface CoachDetail extends CoachListItem { gender; bloodGroup; address; bio; lastLoginAt: string | null; createdAt: string; }
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/coaches` | `search, status, categoryId, page, pageSize` | `200 Page<CoachListItem>` | |
| POST | `/coaches` | `CoachIn`; creates the account and emails the invite | `201 CoachDetail` | `409 EMAIL_EXISTS` |
| GET | `/coaches/{id}` | | `200 CoachDetail` | `404 COACH_NOT_FOUND` |
| PATCH | `/coaches/{id}` | partial; `status: INACTIVE` revokes all sessions immediately | `200 CoachDetail` | `404`, `409` |
| DELETE | `/coaches/{id}` | only if no sessions or reports | `204` | `409 COACH_HAS_HISTORY` |
| POST | `/coaches/{id}/resend-invite` | rate limit 5/hour per coach | `202` | `409 INVITE_ALREADY_ACCEPTED` |
| GET | `/coaches/{id}/attendance` | `year, month` | `200 {items:[{sessionId,date,time,role:'CREATOR'\|'CO_COACH',status,remarks}], summary}` | |
| GET/POST/DELETE | `/coaches/{id}/documents[/{documentId}]` | as students; `kind` field (`CONTRACT`\|`GENERAL`) on POST | | |

**UI change:** remove the temporary-password display/copy. Show `inviteStatus` and a "Resend invite" button instead.

---

## 6. Reference data (write: ADMIN, read: any JWT)

Same contract for `/categories`, `/program-types`, `/training-centers`.

```ts
interface CategoryIn    { name: string; description?: string | null; status?: Status; }   // name 2–100, unique
interface TrainingCenterIn { name: string; location: string; phone?: string | null; status?: Status; }
interface RefOut { id; name; description?; location?; phone?; status: Status; studentCount: number; createdAt: string; }
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/{resource}` | `search, status` (unpaginated, ≤ 500) | `200 RefOut[]` | |
| POST | `/{resource}` | | `201 RefOut` | `409 NAME_EXISTS` |
| PATCH | `/{resource}/{id}` | | `200 RefOut` | `404`, `409` |
| DELETE | `/{resource}/{id}` | | `204` | `409 IN_USE` |

> The frontend uses `title` for categories and program types and `name` for centers. The API uses `name` for all three.

---

## 7. Coach portal — `/coach` (COACH, scoped to self)

### 7.1 Dashboard and students
| Method | URL | Response |
|--------|-----|----------|
| GET | `/coach/dashboard` | `{studentCount, attendanceRateThisMonth, reportsCount, today:{hasSession:boolean, sessionId:UUID\|null}, recentSessions: SessionListItem[]}` |
| GET | `/coach/students` | `search, categoryId, page` → `Page<CoachStudentItem>` |
| GET | `/coach/students/{id}` | `CoachStudentDetail` · `404` if outside scope |
| GET | `/coach/students/{id}/performance-reports` | `PerformanceReportSummary[]` |

```ts
interface CoachStudentItem { id; studentCode; fullName; photo; dateOfBirth; gender; category: RefItem; batch; status; attendancePercentage; }
interface CoachStudentDetail extends CoachStudentItem { bloodGroup; emergencyName; emergencyPhone; parentName; parentPhone; }
// No fees, no addresses, no documents. (OPEN_QUESTIONS Q9)
```

### 7.2 Training sessions and attendance
```ts
interface SessionIn {
  sessionDate: string;                      // today − 7 ≤ date ≤ today (IST)
  categoryIds: UUID[];                      // ≥ 1, each within the coach's categories
  coCoachIds: UUID[];                       // active coaches, not self
  venue: string;                            // 1–200
  startTime?: string | null; endTime?: string | null;   // "06:00" (HH:MM, 24h); end > start
  weeklyTopic?: string | null; dailyTopic: string; explanation: string; overview?: string | null;
  splits: { heading: string; durationMinutes: number; explanation?: string }[];   // 1–20, ordered
  attendance: { studentId: UUID; status: AttendanceStatus; remarks?: string }[];  // students in the categories
}
interface SessionListItem {
  id; sessionDate; venue; startTime; endTime; dailyTopic;
  categories: RefItem[]; createdBy: RefItem; coaches: { coach: RefItem; status: AttendanceStatus }[];
  counts: { present: number; absent: number; informed: number; total: number };
  canEdit: boolean;                         // server-computed (creator + 7-day window)
}
interface SessionDetail extends SessionListItem, Omit<SessionIn,'categoryIds'|'coCoachIds'> {
  attendance: { student: { id; studentCode; fullName; photo }; status; remarks }[];
  createdAt; updatedAt;
}
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/coach/session-roster` | `categoryIds` (repeatable) | `200 {students: CoachStudentItem[]}` | `422 CATEGORY_NOT_ASSIGNED` |
| GET | `/coach/sessions` | `search, categoryId, year, month, date, page` | `200 Page<SessionListItem>` | |
| POST | `/coach/sessions` | `SessionIn` | `201 SessionDetail` | `409 SESSION_EXISTS_FOR_DATE`, `422 STUDENT_NOT_IN_SESSION_CATEGORIES`, `422 DATE_OUT_OF_RANGE` |
| GET | `/coach/sessions/{id}` | creator or co-coach | `200 SessionDetail` | `404 SESSION_NOT_FOUND` |
| PUT | `/coach/sessions/{id}` | full replace; creator only, ≤ 7 days | `200 SessionDetail` | `403 SESSION_LOCKED`, `404`, `409`, `422` |
| DELETE | `/coach/sessions/{id}` | creator only, ≤ 7 days | `204` | `403 SESSION_LOCKED`, `404` |

The time is currently a free-text field in the UI (`"06:00 AM - 08:00 AM"`). The API takes `startTime`/`endTime`. The UI should use two time inputs (or a preset select).

### 7.3 Performance reports
```ts
type Skill = 'TECHNICAL_ABILITY'|'TACTICAL_UNDERSTANDING'|'BALL_CONTROL_FIRST_TOUCH'|'PASSING'|'DRIBBLING'
  |'SHOOTING_FINISHING'|'DEFENDING'|'DECISION_MAKING'|'INDIVIDUAL_SKILLS'|'TEAMWORK'|'COMMUNICATION'
  |'HARD_WORK'|'DISCIPLINE'|'CHARACTER_ATTITUDE'|'FITNESS';
interface PerformanceReportIn {
  studentId: UUID; reportPeriod: string;    // e.g. "Monthly Evaluation - Sep 2026", ≤ 60; unique per student
  recordedDate: string;                     // today − 7 ≤ date ≤ today
  position?: string | null; strongFoot: 'RIGHT'|'LEFT'|'BOTH';
  skills: { skill: Skill; rating: 1|2|3|4|5; comment?: string }[];   // exactly the 15 skills
  strengths: string; areasForImprovement: string;                    // ≤ 2,000 each
  developmentGoals: string[];               // subset of config.standardGoals
  customGoal?: string | null; coachRemarks: string; overallRating: 1|2|3|4|5;
}
interface PerformanceReportSummary { id; student: RefItem; coach: RefItem; reportPeriod; recordedDate; position; overallRating; canEdit: boolean; }
interface PerformanceReportDetail extends PerformanceReportSummary, PerformanceReportIn {
  student: { id; fullName; studentCode; dateOfBirth; age: number }; createdAt; updatedAt;
}
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/performance-reports/config` | any JWT | `200 {skills:{key,label}[], standardGoals:string[], positions:string[]}` | |
| GET | `/coach/performance-reports` | `search, studentId, page`; own reports | `200 Page<Summary>` | |
| POST | `/coach/performance-reports` | | `201 Detail` | `404 STUDENT_NOT_FOUND` (out of scope), `409 REPORT_EXISTS_FOR_PERIOD`, `422` |
| GET | `/coach/performance-reports/{id}` | author only | `200 Detail` | `404` |
| PUT | `/coach/performance-reports/{id}` | author, ≤ 7 days after `recordedDate` | `200 Detail` | `403 REPORT_LOCKED` |
| DELETE | `/coach/performance-reports/{id}` | same rule | `204` | `403 REPORT_LOCKED` |

---

## 8. Admin: sessions, attendance, reports (ADMIN, read-only)

| Method | URL | Query | Response |
|--------|-----|-------|----------|
| GET | `/sessions` | `coachId, categoryId, dateFrom, dateTo, page` | `Page<SessionListItem>` |
| GET | `/sessions/{id}` | | `SessionDetail` |
| GET | `/attendance/students` | `year, month, date, categoryId, status, search, page` | `Page<{id, date, student:{id,studentCode,fullName}, category:RefItem, status, remarks, markedBy:RefItem, markedAt}>` |
| GET | `/attendance/students/summary` | `year, month, categoryId, search, page` | `Page<{student, category, totalSessions, present, absent, informed, rate, band:'GOOD'\|'NEEDS_ATTENTION'\|'CRITICAL'}>` |
| GET | `/attendance/coaches` | `year, month, date, search, page` | `Page<{date, coach:{id,fullName,phone}, sessionId, role, status, remarks}>` |
| GET | `/attendance/students/export`, `/attendance/coaches/export` | same | `text/csv` |
| GET | `/performance-reports` | `coachId, studentId, dateFrom, dateTo, page` | `Page<PerformanceReportSummary>` |
| GET | `/performance-reports/{id}` | | `PerformanceReportDetail` |

`year` may not be in the future, and `month` may not be later than the current month for the current year → `422 PERIOD_IN_FUTURE`.

---

## 9. Fees and payments (ADMIN)

```ts
interface LedgerMonth {
  ledgerEntryId: UUID; year: number; month: number;  // 1–12
  amountDue: number; discount: number; discountReason: string | null;
  amountPaid: number; outstanding: number; dueDate: string; status: FeeStatus;
  payments: { paymentId: UUID; receiptNumber: string; amount: number; paidOn: string }[];
}
interface LedgerRow {                        // one per student for the selected period
  student: { id; studentCode; fullName; parentName; parentPhone; category: RefItem };
  amountDue; discount; amountPaid; outstanding; status: FeeStatus; lastPaymentOn: string | null;
}
interface PaymentIn {
  studentId: UUID; paidOn: string; mode: PaymentMode;
  reference?: string | null; remarks?: string | null;            // ≤ 120 / ≤ 500
  allocations: { year: number; month: number; amount: number }[]; // ≥ 1; sum = payment amount
  discount?: { year: number; month: number; amount: number; reason: string } | null;
}
interface Payment {
  id; receiptNumber: string; student: {...}; amount; mode; paidOn; reference; remarks;
  source: 'MANUAL' | 'SUBMISSION'; submissionId: UUID | null;
  allocations: { year; month; amount; label: string }[];          // label "September 2026"
  status: 'VALID' | 'VOIDED'; voidReason: string | null;
  recordedBy: RefItem; createdAt: string;
}
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/fees/ledger` | `year` (req), `month` (optional; omitted = annual aggregate), `categoryId, status, search, page` | `200 Page<LedgerRow>` | `422 PERIOD_IN_FUTURE` |
| GET | `/fees/summary` | same filters | `200 {expected, collected, discount, outstanding, pendingCount, overdueCount}` | |
| POST | `/fees/payments` | `PaymentIn` | `201 Payment` | `404 LEDGER_MONTH_NOT_FOUND`, `422 OVERPAYMENT`, `422 ALLOCATION_MISMATCH` |
| GET | `/payments` | `studentId, mode, dateFrom, dateTo, search (receipt/student), page` | `200 Page<Payment>` | |
| GET | `/payments/{id}` | receipt data for printing | `200 Payment` | `404` |
| POST | `/payments/{id}/void` | `{reason}` (5–300) | `200 Payment` | `409 ALREADY_VOIDED` |

`InvoiceListPage` and the profile "Invoice" buttons should render from `Payment` (it is a receipt).

---

## 10. Payment submissions

### POST /public/payment-submissions
Auth: none · Rate limit: 5/hour/IP, 20/day/phone · `multipart/form-data`

| Field | Rule |
|-------|------|
| `studentName` | 2–120 |
| `parentMobile` | `+91`/`91`/`0` prefix and spaces stripped → `^[6-9]\d{9}$` |
| `paymentMethod` | `UPI` \| `CASH` |
| `amount` | > 0, ≤ 1,000,000 |
| `paymentDate` | not in the future, ≤ 180 days ago |
| `transactionReference` | optional, ≤ 80 |
| `handedOverTo` | required when `CASH`, ≤ 120 |
| `screenshot` | required when `UPI`; JPEG/PNG ≤ 5 MB |
| `captchaToken` | Turnstile token (if enabled) |

Response `201`:
```json
{ "submissionNumber": "SUB-2026-00042", "status": "PENDING" }
```
Errors: `413`, `415`, `422`, `429`, `400 CAPTCHA_FAILED`.

### Admin endpoints
```ts
interface PaymentSubmission {
  id; submissionNumber; studentName; parentMobile; amount; paymentDate;
  paymentMethod: 'UPI'|'CASH'; transactionReference; handedOverTo;
  screenshot: FileRef | null; status: 'PENDING'|'VERIFIED'|'REJECTED';
  rejectionReason; remarks; reviewedBy: RefItem | null; reviewedAt; paymentId: UUID | null; createdAt;
  candidateStudents?: { id; studentCode; fullName; parentName; parentPhone; category: RefItem }[]; // detail only
}
```

| Method | URL | Notes | Success | Errors |
|--------|-----|-------|---------|--------|
| GET | `/payment-submissions` | `status` (default `PENDING`), `search, page` | `200 Page<PaymentSubmission>` | |
| GET | `/payment-submissions/{id}` | includes `candidateStudents` (exact parent-phone match) | `200` | `404` |
| PATCH | `/payment-submissions/{id}` | `{studentName?, remarks?}`; PENDING only | `200` | `409 SUBMISSION_ALREADY_REVIEWED` |
| POST | `/payment-submissions/{id}/verify` | `{studentId, allocations:[{year,month,amount}], discount?}` → creates a Payment | `200` | `409 SUBMISSION_ALREADY_REVIEWED`, `422 OVERPAYMENT`, `422 ALLOCATION_MISMATCH` |
| POST | `/payment-submissions/{id}/reject` | `{reason}` 5–300 | `200` | `409` |

**UI change:** replace one-click "Verify" with a dialog: pick the student (suggestions pre-filled) and the month(s), then confirm.

---

## 11. Website CMS (ADMIN) and public content

### 11.1 Programmes
```ts
interface ProgrammeIn {
  title: string;               // 2–120; slug auto-generated, unique
  ageGroup: string;            // "6 – 10 years", ≤ 40
  description: string;         // ≤ 1,000
  duration?: string | null; trainingDays?: string | null; coachLabel?: string | null;   // ≤ 80 each
  benefits?: string[];         // ≤ 10 × 120 chars
  status?: Status; sortOrder?: number;
}
interface ProgrammeOut extends ProgrammeIn { id; slug; enquiriesCount: number; createdAt; updatedAt; }
interface PublicProgramme { slug; title; ageGroup; description; duration; trainingDays; coachLabel; benefits: string[]; }
```
| Method | URL | Auth | Success |
|--------|-----|------|---------|
| GET | `/programmes?status&search` | ADMIN | `ProgrammeOut[]` |
| POST / PATCH / DELETE | `/programmes[/{id}]` | ADMIN | `201` / `200` / `204` (`409 IN_USE` if enquiries reference it → deactivate) |
| PUT | `/programmes/order` `{ids: UUID[]}` | ADMIN | `204` |
| GET | `/public/programmes` | none | `PublicProgramme[]` (active, sorted) |
| GET | `/public/programmes/{slug}` | none | `PublicProgramme` / `404` |

### 11.2 Team members
`TeamMemberIn { name (2–120), designation (≤ 60, stored upper-case), biography (≤ 500), photoFileId?, status?, sortOrder? }`
Admin: `GET/POST /team-members`, `PATCH/DELETE /team-members/{id}`, `PUT /team-members/order`, `GET /team-members/designations` (distinct values).
Public: `GET /public/team-members` → `{name, designation, biography, photoUrl}[]`.

### 11.3 Gallery
Admin:
- `GET /gallery-items?category&status&search&page`
- `POST /gallery-items` multipart: `title` (≤ 120), `caption` (≤ 300), `category` (≤ 40), `isWide` (bool), `image` (JPEG/PNG/WebP ≤ 10 MB)
- `PATCH /gallery-items/{id}` (JSON, metadata only), `PATCH /gallery-items/{id}/image` (multipart), `DELETE /gallery-items/{id}`
- `GET /gallery-items/categories`

Public: `GET /public/gallery-items?category&page&pageSize` → `Page<{id, title, caption, category, isWide, imageUrl, thumbnailUrl, width, height}>`; `GET /public/gallery-categories` → `string[]`.

### 11.4 Jobs and applications
```ts
interface JobIn { title; location; description; experienceRequired?; postedOn: string; closingDate?: string | null; status: 'OPEN'|'CLOSED'; }
```
Admin: `GET/POST /jobs`, `PATCH/DELETE /jobs/{id}` (`409 IN_USE` if it has applications → close it instead). Responses add `applicationsCount`.
Public: `GET /public/jobs` → open and not past the closing date.

**POST /public/jobs/{jobId}/applications** — none · 5/hour/IP · multipart

| Field | Rule |
|-------|------|
| `fullName` | 2–120 |
| `mobileNumber` | Indian mobile rule |
| `emailAddress` | email |
| `location` | 2–100 |
| `cv` | PDF/DOC/DOCX ≤ 5 MB |
| `captchaToken` | if enabled |

`201 {"detail":"Application received"}` · `404 JOB_NOT_FOUND`, `409 JOB_CLOSED`.
**UI change:** the dialog must send `jobId` (from `/public/jobs`) instead of a title string.

Admin applications: `GET /job-applications?jobId&status&search&page`, `GET /job-applications/{id}`, `PATCH /job-applications/{id}` `{status?, fullName?, email?, phone?}`, `DELETE /job-applications/{id}`.
`status`: `UNDER_REVIEW | SHORTLISTED | REJECTED`.

### 11.5 Enquiries
**POST /public/enquiries** — none · 5/hour/IP · JSON
```json
{ "parentName": "…", "playerName": "…", "phone": "9847012345", "email": "a@b.com",
  "programmeId": "uuid-or-null", "message": "…", "captchaToken": "…" }
```
`201 {"detail":"Thanks — we'll be in touch"}`.
Admin: `GET /enquiries?status&programmeId&search&page`, `PATCH /enquiries/{id}` `{status?, parentName?, email?, phone?}`, `DELETE /enquiries/{id}`. `status`: `NEW | CONTACTED | RESOLVED`.

---

## 12. Notifications (any JWT; own rows only)

```ts
interface Notification { id; type: 'PAYMENT'|'ADMISSION'|'FEE_DUE'|'ENQUIRY'|'APPLICATION'|'SYSTEM'; title; message; link: string | null; readAt: string | null; createdAt; }
```
| Method | URL | Response |
|--------|-----|----------|
| GET | `/notifications?unreadOnly&page` | `Page<Notification>` |
| GET | `/notifications/unread-count` | `{count: number}` — poll every 60 s while the tab is visible |
| POST | `/notifications/{id}/read` | `204` / `404` |
| POST | `/notifications/read-all` | `204` |

`link` is an admin-app route, e.g. `/super-admin/payments`.

---

## 13. Dashboard, search, uploads, files, health

| Method | URL | Auth | Response |
|--------|-----|------|----------|
| GET | `/dashboard/summary` | ADMIN | `{students:{total,active}, coaches:{active}, fees:{outstanding, collectedThisMonth, collectedThisYear}, pendingVerifications, cms:{categories, programmes, team, gallery, openJobs, applications, newEnquiries}, recentAdmissions: StudentListItem[], recentSubmissions: PaymentSubmission[], topOverdue: LedgerRow[]}` |
| GET | `/search?q=` | ADMIN | `{students[], coaches[], paymentSubmissions[], payments[]}` (≤ 5 each; `q` ≥ 2 chars) |
| POST | `/uploads` | ADMIN | multipart `file`, `purpose` (`STUDENT_PHOTO`\|`COACH_PHOTO`\|`TEAM_PHOTO`) → `201 FileRef` |
| GET | `/files/{id}` | JWT | `302` → signed URL (5 min). `404` if not visible to the caller |
| GET | `/health/live` | none | `{status:"ok"}` |
| GET | `/health/ready` | none | `{status:"ok", db:"ok", redis:"ok"}` / `503` |

---

## 14. Frontend migration checklist

**Admin app**
1. Add `VITE_API_URL`. Replace `api/client.ts` with a real client (bearer header, refresh-on-401, camelCase types).
2. Rewrite `AuthContext`: memory token, `/auth/refresh` on boot, `/auth/me`. Delete `RoleSwitcher`, the login role selector, and the pre-filled credentials.
3. Add a `/set-password?token=` page (invite and reset).
4. Replace mock imports page by page. Adopt TanStack Query for caching and invalidation.
5. Coach form: add category multi-select; replace the credentials modal with invite status and resend.
6. Payment verification: student + month picker dialog.
7. Fee page: allocations per month; display server-computed status.
8. Session form: `startTime`/`endTime` instead of free text; send IDs, not names.
9. Uploads: multipart instead of base64 data URLs.
10. Map enum values (`SUPER_ADMIN` → `ADMIN`, `Active` → `ACTIVE`, …).
11. Programme form: add duration, training days, coach label, benefits.

**Website**
1. Delete `/auth`, `/admin/payments`, `integrations/supabase`, `integrations/lovable`, `PaymentProofDialog`, the Lovable rewrite in `next.config.mjs`, and the Supabase deps.
2. Add `NEXT_PUBLIC_API_URL`. Make content pages Server Components that fetch `/public/*` with `next: { revalidate: 300 }`.
3. Wire the contact form (`name` attributes + `POST /public/enquiries`).
4. Career page: load `/public/jobs`, send `jobId`.
5. Payment page: `POST /public/payment-submissions` (multipart); show the returned submission number.
6. Add Turnstile to the three public forms (if Q10 = yes).
