from datetime import UTC, date, datetime
from decimal import Decimal

from app.admin_site import _badge, _date, _datetime, _rupees
from app.modules.payment_submissions.models import SubmissionStatus


def test_rupees_use_indian_grouping() -> None:
    assert _rupees(Decimal("2500.00")) == "₹2,500"
    assert _rupees(Decimal("1234567.5")) == "₹12,34,567.50"
    assert _rupees(Decimal("999")) == "₹999"
    assert _rupees(Decimal("-100000")) == "-₹1,00,000"


def test_dates_are_readable_and_in_ist() -> None:
    assert _date(date(2026, 10, 5)) == "05 Oct 2026"
    assert _datetime(datetime(2026, 10, 5, 9, 2, tzinfo=UTC)) == "05 Oct 2026, 02:32 PM"


def test_status_badges() -> None:
    assert str(_badge(SubmissionStatus.PENDING)) == '<span class="badge bg-yellow-lt">Pending</span>'
    assert "bg-red-lt" in str(_badge(SubmissionStatus.REJECTED))
