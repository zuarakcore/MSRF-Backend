# Frontend Analysis

Scope: `Website/` (public site, git `thahirthm/msrf`) and `MSRF ADMIN/` (admin + coach portal).
Analysed: 2026-10-05, admin at commit `129722f`, website at `9da63a5`.

---

## 1. Headline findings

| # | Finding | Impact on backend |
|---|---------|-------------------|
| 1 | **The admin app is not Next.js.** It is a Vite + React 19 SPA with `react-router-dom` v7. No server runtime. | Auth must work for a pure browser SPA (no Next.js server to hold cookies or proxy). Static deploy. |
| 2 | **The admin app has zero backend integration.** All state is `useState(INITIAL_*)` from `src/mock-data/msrf-data.ts`. `src/api/client.ts` is a stub that returns `{}`. | Every screen needs an API. The TypeScript types in `src/types/index.ts` are the best available data contract, but several are inconsistent (see §5). |
| 3 | **The website talks to Supabase directly from the browser** (payment proof, job application) and has its own Supabase-based "staff login" and payment-verification page. | Supabase is replaced entirely. Website `/auth` and `/admin/payments` must be deleted — they duplicate the admin app. |
| 4 | **Website content is hard-coded** in `src/lib/site-data.ts`, while the admin app has CMS screens for the same content (programmes, team, gallery, careers). | Public read endpoints are needed, and the website must be rewired to them. |
| 5 | **No realtime features exist anywhere.** No WebSocket, SSE, or polling code. The only "live" UI is the admin notification bell. | WebSockets are **not** justified (see ARCHITECTURE.md §7). |
| 6 | **Business rules live in the UI**: fee status, invoice generation, 7-day edit locks, attendance %, payment-to-student matching, overdue logic. | All must move server-side; the UI versions are unreliable (some are hash-based fake data). |

---

## 2. Admin app (`MSRF ADMIN/`)

### 2.1 Stack

- Vite 8, React 19, TypeScript 6, Tailwind 4, `react-router-dom` 7, `lucide-react`.
- No data-fetching library, no form library, no validation library.
- PDF and print output are client-side (`window.print()` via `PrintPortal`, `PlayerDevelopmentReportPDF`). No server-side PDF is needed.
- CSV export is client-side (`utils/format.ts → exportToCSV`).

### 2.2 Routing and roles (`src/App.tsx`)

Two roles: `SUPER_ADMIN` and `COACH`. `RoleGuard` is a client-side redirect only.

**Admin routes (routed + in sidebar)**

| Route | Page | Purpose |
|-------|------|---------|
| `/super-admin/dashboard` | `SuperAdminDashboard` | KPIs, recent admissions, pending verifications, due invoices, CMS counts |
| `/super-admin/students` | `StudentListPage` | Roster CRUD, 6 filters, search, pagination, CSV import/export, status toggle |
| `/super-admin/students/:id` | `StudentProfilePage` | Tabs: overview, attendance, fees (12 months), payments, performance, documents |
| `/super-admin/coaches` | `CoachListPage` | Coach CRUD, contract upload, "credentials" modal |
| `/super-admin/coaches/:id` | `CoachProfilePage` | Profile, coach attendance history by month, documents, credentials |
| `/super-admin/program-types` | `ProgramTypesCMSPage` | Reference data CRUD |
| `/super-admin/training-centers` | `TrainingCentersPage` | Reference data CRUD |
| `/super-admin/attendance` | `AttendanceManagementPage` | Read-only: trainee/coach attendance stream + monthly summary, CSV/print |
| `/super-admin/fees` | `FeeManagementPage` | Month-wise fee ledger, record payment + discount, financial report |
| `/super-admin/payments` | `PaymentVerificationPage` | Verify/reject parent-submitted payment proofs |
| `/super-admin/reports` | `ReportsCenterPage` | Session reports and player development reports, filter by coach |
| `/super-admin/website/categories` | `CategoriesCMSPage` | Student categories (age groups). Labelled "website" but drives students and attendance |
| `/super-admin/website/programmes` | `ProgrammesCMSPage` | Website programmes |
| `/super-admin/website/team` | `TeamCMSPage` | Leadership/board members |
| `/super-admin/website/gallery` | `GalleryManagementPage` | Photos with free-text categories |
| `/super-admin/website/careers` | `CareersCMSPage` | Job postings |
| `/super-admin/website/job-applications` | `JobApplicationsCMSPage` | Review applications, download CV |
| `/super-admin/website/enquiries` | `ContactEnquiriesCMSPage` | Contact form submissions |
| `/super-admin/notifications` | `NotificationsPage` | Notification list |
| `/super-admin/settings` | `SettingsPage` | Change own password |

**Routed but not in the sidebar:** `/super-admin/invoices` (`InvoiceListPage`), `/coach/students`, `/coach/students/:id`.

**Dead code (imported or present but not routed):** `StudentAssignmentPage`, `UserManagementPage`, `RolesPermissionsPage`, `WebsiteCMSPage`. Commit `d605390` says "remove assignments and invoices". Do **not** build backends for these unless confirmed.

**Coach routes**

| Route | Page | Purpose |
|-------|------|---------|
| `/coach/dashboard` | `CoachDashboard` | Own stats: students, attendance rate, reports, today's status |
| `/coach/attendance` | `CoachAttendancePage` | 3-step session wizard (setup → session plan → mark attendance); list/edit/delete within 7 days |
| `/coach/performance` | `CoachPerformancePage` | 15-skill player development reports; edit/delete within 7 days |
| `/coach/students` (+`/:id`) | `CoachStudentListPage`, `CoachStudentProfilePage` | Own students, read-only with performance history |

### 2.3 Authentication (`context/AuthContext.tsx`, `pages/auth/LoginPage.tsx`)

- Mock: `login()` ignores the password and picks a role from **a role selector on the login form** or from `email.includes('coach')`.
- The app **starts logged in as Super Admin** (`useState(INITIAL_USERS[0])`).
- `RoleSwitcher` component lets a user switch role in-app.
- Login form is pre-filled with demo credentials (`admin@msrf.org` / `msrf2026admin#`).
- "Forgot password" modal only sets a local flag.
- `api/client.ts` reads `localStorage['msrf_auth_token']`, falling back to `'demo-jwt-token-xyz'`.
- `hasPermission(module, action)` reads a static permission matrix. `RolesPermissionsPage` (dead) edits it. **The matrix is fixed per role; there is no per-user permission editing in routed UI.**

### 2.4 Entities and workflows

**Student** (`types.Student`, form in `StudentListPage`)
- Personal: full name*, gender, blood group, DOB* (not in the future), phone, email, address, photo.
- Admission: admission number (optional, auto `ADM-YYYY-NNN`), admission date, category, program type, training center, batch (`Morning 6–8`, `Evening 4–6`, `Weekend Special`), monthly fee (default ₹2000).
- Parent*: name*, relationship (Father/Mother/Guardian), phone* (10 digits), email, address.
- Emergency: name, relationship, phone.
- System: `studentId` (`MSRF-YYYY-NNN`), status Active/Inactive.
- Derived (displayed): attendance %, present/absent counts, fee status, total/paid/pending/discount.
- **No coach field on the form.** `coachId` exists on the type but nothing in the routed UI sets it.
- Filters: search (name, studentId, parent name, phone), category, program type, training center, DOB year, status (default Active), fee status. Page sizes selectable.
- CSV import with a downloadable template (14 columns); CSV export of the filtered list.
- Documents: title + file (UI fakes PDF).

**Coach** (`types.Coach`, form in `CoachListPage`)
- Full name*, gender, email (login username), phone (10 digits), blood group, experience years, joined date, address, contract file (`.pdf,.doc,.docx,.png,.jpg,.jpeg`), photo, bio.
- On create: generates `MSRF#NNNN` temp password and toasts "Credentials mailed".
- Profile shows **the temporary password in plain text** and copies `email + password` to the clipboard.
- `monthlyRating`, `attendanceAvg`, `capacity`, `assignedStudentsCount` are displayed but have no source of truth.
- Coach attendance history is mock-generated.

**Reference data**: Category, Program Type, Training Center — title/name, description or location/phone, status. All CRUD + status toggle + delete.

**Training session + attendance** (`CoachAttendancePage`, `types.DailyTrainingSessionReport`)
- Step 1: pick ≥1 category, date, co-coaches (other coaches), venue.
- Step 2: time (free text, e.g. `06:00 AM - 08:00 AM`), daily topic*, explanation*, ordered "splits" (heading, minutes, explanation; ≥1), overall session overview.
- Step 3: mark each student Present/Absent/Informed with remarks; "mark all present".
- All selected coaches are **auto-marked Present** (this is the only source of coach attendance).
- Rules: one session per coach per date (creator or co-coach); edit and delete allowed only if `0 ≤ today − date ≤ 7` days.
- Student roster for marking = students whose category matches the selected categories (fuzzy string matching).
- List filters: search (topic, venue), category, year, month, specific date.
- `weeklyTopic` exists in data, but the form always saves `''`.

**Admin attendance** (`AttendanceManagementPage`): read-only. Tabs trainees/coaches. Stream view (per record) and monthly summary per student (rate ≥85 Good, ≥70 Needs Attention, else Critical). Filters: year (current year and 5 back), month (no future months), date, category, status, search. CSV export and print. All current data is hash-generated.

**Performance report** (`CoachPerformancePage`, `types.PerformanceRecord`)
- Student, report period (text), recorded date, position, DOB/age (copied from student), strong foot (Right/Left/Both).
- 15 fixed skills (`DEFAULT_15_CATEGORIES`), each rated 1–5 with a comment.
- Strengths, areas for improvement, development goals (multi-select from 7 standard goals), custom goal, coach remarks, overall rating 1–5.
- Edit/delete within 7 days. Coach sees only own reports.
- Legacy fields (`technicalSkills`, `staminaDiscipline`, `teamwork`, `rating`) are unused.

**Fees** (`FeeManagementPage`, `StudentProfilePage` fees tab)
- Model implied: **monthly fee per student, 12 months per calendar year, due on the 10th**.
- Ledger view per (year, month): fee, paid, discount, pending, status (Paid/Pending; Overdue appears elsewhere).
- Admin records payment: amount, discount + discount reason, mode (Cash, Bank Transfer, UPI, Cheque), remarks.
- Only students admitted on or before the selected period are listed.
- Summary: expected, collected, outstanding, pending count. PDF report via print.
- **Invoices are fabricated client-side** from payments and months. `InvoiceListPage` reads static mock invoices with GST fields that are always 0.

**Payment verification** (`PaymentVerificationPage`, fed by website form)
- Submission: number, student name, parent name/phone, amount, transaction ID, payment date, screenshot, status (Pending Verification/Verified/Rejected), rejection reason, verified by/at.
- Verify: **auto-matches a student by phone substring or exact name** and adds the amount to their paid total.
- Reject: reason required, toast says "notice issued to parent" (no channel exists).
- Admin can edit the student name on a submission.

**CMS**
- Programme: title, age group, description, status. `enquiriesCount` displayed. (The website shows more fields — see §3.3.)
- Team member: name, designation (from a list or custom, upper-cased), biography, photo, status.
- Gallery item: title/caption, category (fixed list or custom free text), image, status.
- Career (job): position, location, experience required, closing date, description, status Open/Closed, applications count.
- Job application: name, email, phone, position, CV, applied date, status (Under Review/Shortlisted/Rejected); admin can edit fields and delete.
- Contact enquiry: name, email, phone, programme/subject, message, status (New/Contacted/Resolved); edit and delete.

**Notifications**: types `PAYMENT, ADMISSION, ATTENDANCE, FEE_DUE, SYSTEM, ENQUIRY, APPLICATION`; title, message, timestamp, read flag, link. Header bell with unread count, mark one / mark all read.

**Global search** (`GlobalSearchModal`, ⌘K): students, coaches, payments, invoices.

**Settings**: change own password (current, new, confirm; min 6 chars).

### 2.5 File uploads

- `ImageUpload` and `FileUpload` read files with `FileReader.readAsDataURL` and store **base64 data URLs in form state**. No size or type checks beyond the `accept` attribute.
- Upload points: student photo, student documents, coach photo, coach contract, coach documents, team photo, gallery image, CSV import.

---

## 3. Website (`Website/`)

### 3.1 Stack and history

- Next.js 16 App Router, React 19, Tailwind 4, shadcn/ui, TanStack Query (provider only, unused), zod, sonner.
- Built in Lovable as TanStack Start, then converted to Next.js by regex scripts (`migrate.mjs`, `migrate2.mjs`) that add `"use client"` to **every** page and component. The site renders client-side and loses most SSR/SEO benefits.
- `package.json` name is still `tanstack_start_ts`; it carries unused deps (`@tailwindcss/vite`, `nitro`, `@lovable.dev/cloud-auth-js`).
- `next.config.mjs` rewrites `/__l5e/*` to a Lovable preview domain (a third-party proxy left in production config).

### 3.2 Pages

| Route | Data | Backend need |
|-------|------|--------------|
| `/` | `site-data.ts`: programs, team, events, partnership, stats | Public programmes, team (+ events?) |
| `/about` | programs, team, events, roadmap, trust points | Same |
| `/programs` | programs | Public programmes |
| `/partner` | partnership, programs | Static |
| `/gallery` | galleryItems (filter by category), events | Public gallery |
| `/career` | hard-coded `jobs` array + `JobApplicationDialog` | Public jobs + application submit |
| `/contact` | form: parent name, player name, phone, email, programme of interest, message. **`onSubmit` only sets local state; nothing is sent.** | Public enquiry submit |
| `/submit-payment-proof` | Supabase upload + insert | Public payment submission |
| `/auth` | Supabase email/password sign-up + sign-in + Google OAuth | **Delete** |
| `/admin/payments` | Supabase list/verify/reject | **Delete** (duplicate of admin app) |

### 3.3 Website data shapes that the admin CMS does not cover

- **Programme**: `slug, name, age, description, duration, days, coach, benefits[]`. The admin `ProgrammeCMS` has only `title, ageGroup, description, status`.
- **Job**: `title, location, description, status, postedOn`. Close to admin `CareerCMS`.
- **Gallery**: `src, category, caption, wide`. Categories `All, Training, Matches, Events, Argentina`.
- **Events** (`title, kind, date, place`): shown on home/about/gallery, **no admin CMS**.
- **Team**: `name, role, image`.

### 3.4 Forms and validation (zod, client-side)

**Payment proof (`/submit-payment-proof`, the live flow)**
- `studentName` 2–120, `parentMobile` normalised (`+91`/`91`/`0` stripped) and must match `^[6-9]\d{9}$`, `paymentMethod` `UPI|Cash`, `handedOverTo` required for Cash, `paymentDate` required, `amount` > 0 and ≤ 1,000,000.
- Screenshot required for UPI: JPEG/PNG, ≤ 5 MB.
- **Inserts columns `payment_method` and `handed_over_to`, which do not exist** in the Supabase migration. The code itself comments that it "might fail".

**`PaymentProofDialog`**: an older variant with `transactionId` instead of method. Not used by any page (dead).

**Job application (`JobApplicationDialog`)**
- `fullName` 2–120, `mobileNumber` (same rule), `emailAddress` email, `location` 2–100, CV PDF/DOC/DOCX ≤ 5 MB.
- Sends `job_title` (string), not a job ID. **Inserts into `job_applications`, which does not exist.**
- `location` is collected here but not shown in the admin type.

### 3.5 Supabase setup

- Tables: `user_roles`, `payment_submissions`. Storage buckets: `payment-proofs` (anonymous insert, admin read).
- Trigger `grant_first_user_admin`: **the first account to sign up becomes admin.** Combined with the open sign-up form at `/auth`, whoever signs up first on a fresh project owns it.
- The website admin page builds screenshot URLs with `getPublicUrl` on bucket **`payments`**, but uploads go to **`payment-proofs`**, a private bucket. Screenshots cannot load.
- The browser client reads `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`. `.env` defines only `SUPABASE_*` and `VITE_*`. Next.js exposes only `NEXT_PUBLIC_*` to the browser, so the client most likely throws at runtime. **The website forms are probably not working today.**
- `.env` is committed to git (publishable keys only).

### 3.6 Other

- Theme stored in `localStorage['mcfc-theme']` (harmless).
- No tokens, cookies, or sessions anywhere else on the website.
- Branding inconsistency: the website is "Malabar Challengers FC (MCFC)" under MSRF; the admin says "MSRF Coaching Management System".

---

## 4. Search-term sweep (summary)

| Term | Hits |
|------|------|
| `fetch(` / `axios` | None. Admin `client.ts` has a commented-out `fetch`. |
| `/api/` | Only `api/client.ts` base `'/api/v1'` (comment mentions "Django REST Framework"). |
| `localhost` | Coach credentials text: `http://localhost:5173/login`. |
| `NEXT_PUBLIC_` / `process.env` | Supabase client only. Admin uses no `import.meta.env`. |
| `token` / `Bearer` / `Authorization` | Admin `client.ts` stub only. |
| `cookie` / `session` | Supabase session in `localStorage` on the website. "Session" in the admin means *training session*. |
| `register` / `password` / `reset` / `forgot` | Admin login, forgot-password modal (no-op), settings change-password, coach temp passwords. Website `/auth` sign-up. |
| `email` | Field values only. No email sending anywhere. Toasts *claim* emails are sent (coach credentials, password reset). |
| `websocket` / `socket` | None. |
| `upload` | See §2.5 and §3.4. |
| `notification` | Admin `NotificationContext` (mock list + toasts). |
| `chat` | None. |

---

## 5. Inconsistencies the backend must resolve

1. **How a coach is linked to students** is defined three different ways:
   - `CoachStudentListPage`, `CoachDashboard`, `CoachStudentProfilePage`: `student.coachId === coach.id`.
   - `CoachAttendancePage`: students whose **category** matches the categories selected for the session.
   - `CoachPerformancePage`: dropdown lists **all students**.
   - No routed UI sets `coachId` (the assignment page was removed).
   → Needs a decision. See OPEN_QUESTIONS.md Q1.
2. **Course vs category.** `Student.course` and the `SportsCourse` type (Swimming, Badminton, …) are leftovers. Categories are the real grouping. Drop `course`.
3. **Fees.** The monthly fee is the only stored input. The annual total, the paid months, invoices and the overdue state are all derived in different ways on different pages. Months are hard-coded to 2026.
4. **Invoice vs receipt.** "Invoices" are generated per payment and per month on the fly. Nothing issues an invoice before payment, so these are really **receipts**.
5. **Payment proof shape** differs between the website (method, handed-over-to) and the admin type (transaction ID, parent name).
6. **Role naming**: frontend `SUPER_ADMIN`, brief says `ADMIN`.
7. **Names**: the frontend uses a single `fullName` everywhere. The brief suggests `first_name`/`last_name`.
8. **Dates**: the frontend computes "today" with `new Date().toISOString().slice(0,10)`, which is the **UTC** date. In India (UTC+5:30) this is yesterday's date until 05:30 AM, which affects early morning sessions and the 7-day lock.
9. **Attendance status** `Excused` exists in a type but not in any UI.

---

## 6. Security problems found in the frontends

| Severity | Issue | Where |
|----------|-------|-------|
| Critical | User chooses their own role at login; in-app role switcher; app boots as Super Admin | Admin `LoginPage`, `RoleSwitcher`, `AuthContext` |
| Critical | First sign-up becomes admin + public sign-up form | Website Supabase trigger + `/auth` |
| High | Temporary coach passwords shown and copied in plain text, with a hard-coded fallback password `Coach#2026!` | `CoachProfilePage`, `CoachCredentialsModal` |
| High | Payment credited to a student by fuzzy phone-substring match | `PaymentVerificationPage.handleVerify` |
| High | Coach "security guard" on student profile is client-side only; the performance form lets a coach pick any student | `CoachStudentProfilePage`, `CoachPerformancePage` |
| Medium | Token intended for `localStorage` with a hard-coded fallback token | Admin `api/client.ts` |
| Medium | Upload type/size validation is client-side only; Supabase bucket accepts any anonymous upload | Website forms, Supabase policy |
| Medium | Base64 data URLs for files (large JSON bodies, no server validation) | Admin `ImageUpload`/`FileUpload` |
| Medium | Third-party rewrite proxy in production Next.js config | `next.config.mjs` |
| Low | Demo credentials pre-filled on the login form | Admin `LoginPage` |
| Low | `.env` committed | Website |
| Note | Large amounts of minors' PII (DOB, blood group, address, parent phones, photos) | Must be access-controlled and never served from public URLs |

---

## 7. Architecture critique (frontends)

- **Pages are 500–1,600-line components** that mix mock data, derivation logic, forms and print layouts. Wiring them to an API will require a data layer. Recommend TanStack Query in the admin app (the website already has it installed) and moving derivations to the backend.
- **The website is "use client" everywhere** because of the regex migration. Public content pages should be Server Components that fetch from the API with ISR (`revalidate`). This gives SEO and removes per-visitor API load.
- **Two copies of admin functionality** (website `/admin/payments` vs the admin app). Delete the website one.
- **Unused types** (`HomeBannerCMS`, `PartnerCMS`, `TestimonialCMS`, `BlogCMS`) and unused pages. Do not build backends for them.
- **The permission matrix UI** suggests fine-grained RBAC, but the routed app uses two fixed roles. Fixed roles are the right call at this size; per-module permission tables would be premature.
