"""Book catalog (fixture copy for code-analysis tests)."""


class Book:
    """One book in the catalog."""

    def __init__(self, isbn: str, title: str, author: str) -> None:
        self.isbn = isbn
        self.title = title
        self.author = author


class Catalog:
    """In-memory catalog."""

    def __init__(self) -> None:
        self.books: dict[str, Book] = {}

    def add(self, book: Book) -> None:
        """Add a book to the catalog."""
        self.books[book.isbn] = book

    def find(self, isbn: str) -> Book | None:
        """Look up a book by ISBN, return None if absent."""
        return self.books.get(isbn)
