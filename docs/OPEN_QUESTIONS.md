# Open Questions

Requirements the frontend code does not settle. The design documents already use the **recommended** answer for each one, so if you accept a recommendation nothing changes.

**Blocking** = changes the schema or core rules, so it must be answered before implementing that module.

---

## Blocking

### Q1. How is a coach linked to students?
The frontend uses three conflicting rules (FRONTEND_ANALYSIS §5.1), and nothing in the current UI sets a student's coach.
- **A. Category-based (recommended).** Admin assigns categories to each coach (`coach_categories`). A coach sees and marks students in those categories. This matches how the attendance wizard already works (pick categories → roster), and the coach assignment page was deliberately removed.
- B. Direct assignment: `students.coach_id`. Brings back the removed assignment UI. Does not fit sessions that span categories and several coaches.
- C. Both: category for attendance, direct coach for performance reports.

Needs: a category multi-select on the coach form (small UI change).

### Q2. Invoices: receipts only, or real invoices?
Today the "invoices" are generated after payment, and GST is always 0.
- **Recommended:** receipts only (one per payment, sequential number) plus **void** for corrections.
- Alternative: issue invoices before payment (monthly or annual), with GST. Needs an invoice table, numbering rules, and probably an accountant's input.

### Q3. Fee schedule rules
- Fees run **per calendar month, January–December**, due on the 10th? (The UI hard-codes 2026 Jan–Dec and the 10th.) Or is there an academic year (for example June–May)?
- A student admitted mid-month: is the full monthly fee due for that month? **Recommended:** yes, first ledger month = admission month.
- Inactive students: stop generating months while inactive? **Recommended:** yes.
- Residential/weekend programmes with different billing (term or annual)? Assumed no.
- Is there historical fee data to import, or do ledgers start at go-live?

### Q4. Session edit rights
- Can **co-coaches** edit or delete a session, or only the creator? **Recommended:** creator only.
- Should **admins** be able to correct attendance after the 7-day lock? **Recommended:** yes, via a later admin endpoint with an audit log. Not in v1 because the admin UI is read-only.

### Q5. Deleting students and coaches
The UI has delete buttons. Deleting someone with attendance or payment history would destroy financial records.
- **Recommended:** delete only when there is no history (`409` otherwise); deactivate instead.

### Q6. Performance reports per period
Can a coach file two reports for the same student and period? **Recommended:** no, unique `(student, report_period)`. Also: should the period be a structured month (`2026-09`) instead of free text? The current design keeps the UI's free text (`report_period varchar(60)`). **Recommended:** switch to a structured month (`period date`, first of month) with the label derived from it, which makes "one per month" enforceable and sortable.

---

## Non-blocking (defaults chosen)

| # | Question | Default used |
|---|----------|--------------|
| Q7 | Password minimum length (UI says 6) | 10 characters |
| Q8 | Coach `monthlyRating` has no data source | Drop it. Show attendance rate and student count instead |
| Q9 | Which student fields may a coach see? | Name, code, photo, DOB/age, gender, category, batch, blood group, parent name and phone, emergency contact. No fees, addresses or documents |
| Q10 | Bot protection on public forms | Cloudflare Turnstile (free). Disabled when the key is empty |
| Q11 | How is a parent told their payment proof was rejected? | No channel in v1 (no parent email on the form). The admin calls them. Optional later: SMS/WhatsApp provider |
| Q12 | Retention for CVs, rejected applications, payment screenshots | CVs 12 months after the decision; screenshots kept with the payment record. Needs your policy (DPDP Act 2023 applies) |
| Q13 | Auto-reply email to enquiries? | Yes, if the parent gave an email |
| Q14 | Events (trials, clinics, tournaments) appear on the website but have no admin CMS | Not built. Say if you want an Events CMS (one table, ~1 day) |
| Q15 | API docs in production | Disabled (`DOCS_ENABLED=false`) |
| Q16 | Is there production data in Supabase (`payment_submissions`, uploaded screenshots)? | Assumed none. If there is, add a one-off import script |
| Q17 | Production domains | `www.<domain>`, `admin.<domain>`, `api.<domain>` on one registrable domain (required for the cookie design) |
| Q18 | Session lifetime | Access 15 min; refresh 7 days idle / 30 days absolute |
| Q19 | Antivirus scanning of uploads | Not in v1 |
| Q20 | Email provider / sending domain | Any SMTP (Amazon SES, Brevo, Zoho, Google Workspace SMTP relay). The sending domain needs SPF/DKIM/DMARC |
| Q21 | Can admins create other admins from the UI? | No. CLI only (the user-management page is dead code) |
| Q22 | Hosting target (VPS + Docker, Render/Railway/Fly, AWS) | Docker images that run anywhere; compose for dev |

---

## Frontend changes the backend depends on

These are not questions, but the backend design assumes them (details in API_SPECIFICATION §14):

1. Remove the login role selector, `RoleSwitcher`, the default-logged-in state and the pre-filled credentials.
2. Replace temporary-password display with invite status and a resend button; add a `/set-password` page.
3. Payment verification picks student + month explicitly.
4. Uploads use multipart, not base64.
5. Website: delete `/auth`, `/admin/payments` and Supabase; wire contact, career and payment forms to the API.
