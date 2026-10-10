"""Late fee calculation (fixture)."""

from decimal import Decimal

DAILY_LATE_FEE = Decimal("0.25")
MAX_LATE_FEE = Decimal("10.00")


def calculate_late_fee(days_overdue: int) -> Decimal:
    """Return the late fee for the given overdue days, capped at MAX_LATE_FEE."""
    if days_overdue <= 0:
        return Decimal("0.00")
    fee = Decimal(days_overdue) * DAILY_LATE_FEE
    return min(fee, MAX_LATE_FEE)


def is_fee_waivable(days_overdue: int, is_first_offence: bool) -> bool:
    """First-offenders can waive fees under one week."""
    return is_first_offence and days_overdue <= 7


def format_receipt(borrower_name: str, book_title: str, fee: Decimal) -> str:
    """Format a receipt string for a late fee charge."""
    return f"Receipt for {borrower_name}: {book_title} — {fee}"
