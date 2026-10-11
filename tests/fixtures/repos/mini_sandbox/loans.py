"""Loan tracking (fixture copy)."""

from books import Book


class Loan:
    """A single borrowing event."""

    def __init__(self, book: Book, borrower_id: str) -> None:
        self.book = book
        self.borrower_id = borrower_id
        self.returned = False

    def mark_returned(self) -> None:
        """Flag the loan as returned."""
        self.returned = True
