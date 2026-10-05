-- Large synthetic dataset for performance testing (NEVER run against a real database).
--   createdb -O msrf msrf_perf
--   DATABASE_URL=postgresql+asyncpg://msrf:msrf@localhost:5432/msrf_perf .venv/bin/alembic upgrade head
--   psql -U msrf -d msrf_perf -f scripts/seed_perf.sql
-- Volume: 5,000 students, 60 coaches, ~11k sessions, ~440k attendance rows, 60k ledger months,
-- ~50k payments, 10k performance reports (150k skill ratings), 20k notifications.
\set ON_ERROR_STOP on
select setseed(0.42);

insert into categories (id, name, sort_order) select gen_random_uuid(), 'Category ' || g, g from generate_series(1, 12) g;
insert into program_types (id, name) select gen_random_uuid(), 'Program ' || g from generate_series(1, 3) g;
insert into training_centers (id, name, location) select gen_random_uuid(), 'Center ' || g, 'Kerala' from generate_series(1, 5) g;

insert into users (id, email, full_name, role, is_active, is_verified)
values (gen_random_uuid(), 'perf-admin@example.com', 'Perf Admin', 'ADMIN', true, true);

with u as (
    insert into users (id, email, full_name, role, is_verified)
    select gen_random_uuid(), 'coach' || g || '@perf.example.com', 'Coach ' || g, 'COACH', true
    from generate_series(1, 60) g returning id
)
insert into coach_profiles (id, user_id, phone, experience_years, joined_date)
select gen_random_uuid(), id, '9847000000', 5, date '2020-01-01' from u;

-- each coach covers two neighbouring categories
with c as (select id, row_number() over (order by id) rn from coach_profiles),
     k as (select id, sort_order rn from categories)
insert into coach_categories (coach_id, category_id)
select c.id, k.id from c join k on k.rn in (((c.rn - 1) % 12) + 1, (c.rn % 12) + 1);

with cat as (select array_agg(id order by sort_order) a from categories),
     pt as (select array_agg(id) a from program_types),
     tc as (select array_agg(id) a from training_centers)
insert into students (id, student_code, admission_number, admission_date, full_name, date_of_birth, gender,
                      category_id, program_type_id, training_center_id, batch, monthly_fee, status,
                      parent_name, parent_relationship, parent_phone, email)
select gen_random_uuid(), 'PERF-' || lpad(g::text, 5, '0'), 'PADM-' || g, current_date - 400 - (g % 300),
       'Student ' || g || ' ' || (array['Nair','Menon','Kumar','Pillai','Khan','Thomas'])[1 + g % 6],
       date '2008-01-01' + (g % 3000), (array['MALE','FEMALE'])[1 + g % 2]::gender,
       cat.a[1 + g % 12], pt.a[1 + g % 3], tc.a[1 + g % 5],
       (array['MORNING','EVENING','WEEKEND'])[1 + g % 3]::batch, (array[2000,2500,3000])[1 + g % 3],
       (case when g % 20 = 0 then 'INACTIVE' else 'ACTIVE' end)::record_status,
       'Parent ' || g, 'FATHER', '9' || lpad((400000000 + g)::text, 9, '0'), 'student' || g || '@perf.example.com'
from generate_series(1, 5000) g, cat, pt, tc;

-- one session per day for 30 coaches over the last year
create temp table s as
select gen_random_uuid() id, d::date sd, c.id coach_id, c.rn
from generate_series(current_date - 365, current_date - 1, interval '1 day') d,
     (select id, row_number() over (order by id) rn from coach_profiles) c
where c.rn <= 30;
insert into training_sessions (id, session_date, created_by_coach_id, venue, daily_topic, explanation)
select id, sd, coach_id, 'Main Ground', 'Topic ' || (rn % 9), 'Session plan' from s;
insert into session_coaches (session_id, coach_id, session_date, role, status)
select id, coach_id, sd, 'CREATOR', 'PRESENT' from s;
insert into session_categories (session_id, category_id)
select s.id, cc.category_id from s join coach_categories cc on cc.coach_id = s.coach_id;

-- ~40 students per session, drawn from the session's categories
insert into student_attendance (id, session_id, student_id, session_date, status)
select gen_random_uuid(), s.id, st.id, s.sd,
       (case when abs(hashtext(st.id::text || s.sd)) % 100 < 85 then 'PRESENT'
             when abs(hashtext(st.id::text || s.sd)) % 100 < 95 then 'ABSENT' else 'INFORMED' end)::attendance_status
from s
join coach_categories cc on cc.coach_id = s.coach_id
join students st on st.category_id = cc.category_id and st.status = 'ACTIVE'
where abs(hashtext(st.id::text || s.id::text)) % 21 = 0;

-- 12 months of fees; ~85% paid in full
insert into fee_ledger_entries (id, student_id, period, amount_due, due_date)
select gen_random_uuid(), st.id, p::date, st.monthly_fee, p::date + 9
from students st,
     generate_series(date_trunc('month', current_date) - interval '11 months', date_trunc('month', current_date),
                     interval '1 month') p;
create temp table pe as
select e.id eid, e.student_id, e.amount_due, e.period, gen_random_uuid() pid, row_number() over () rn
from fee_ledger_entries e where random() < 0.85;
insert into payments (id, receipt_number, student_id, amount, mode, paid_on, source, status)
select pid, 'PERF-RCPT-' || rn, student_id, amount_due, 'UPI', period + 5, 'MANUAL', 'VALID' from pe;
insert into payment_allocations (id, payment_id, ledger_entry_id, amount)
select gen_random_uuid(), pid, eid, amount_due from pe;
update fee_ledger_entries e set amount_paid = pe.amount_due from pe where pe.eid = e.id;

-- 4 reports for 2,500 students, each with the 15 skill ratings
create temp table pr as
select gen_random_uuid() id, st.id sid,
       (select cc.coach_id from coach_categories cc where cc.category_id = st.category_id limit 1) cid, m
from (select * from students order by student_code limit 2500) st, generate_series(1, 4) m;
insert into performance_reports (id, student_id, coach_id, report_period, recorded_date, strong_foot, strengths,
                                 areas_for_improvement, coach_remarks, overall_rating)
select id, sid, cid, 'Perf period ' || m, current_date - m * 30, 'RIGHT', 'Strengths', 'Improve', 'Remarks', 4
from pr;
insert into performance_skill_ratings (report_id, skill, rating)
select pr.id, s, 3 + (abs(hashtext(pr.id::text || s::text)) % 3) from pr, unnest(enum_range(null::skill)) s;

insert into notifications (id, user_id, type, title, message, read_at, created_at)
select gen_random_uuid(), (select id from users where role = 'ADMIN' limit 1), 'SYSTEM', 'Notice ' || g, 'Message',
       case when g % 5 = 0 then null else now() end, now() - g * interval '1 minute'
from generate_series(1, 20000) g;

analyze;
select 'students' t, count(*) from students union all select 'sessions', count(*) from training_sessions
union all select 'attendance', count(*) from student_attendance union all select 'ledger', count(*) from fee_ledger_entries
union all select 'payments', count(*) from payments union all select 'skill ratings', count(*) from performance_skill_ratings;
