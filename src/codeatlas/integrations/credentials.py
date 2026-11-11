"""Authenticated encryption for provider credentials stored in the application database."""

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise ValueError("credential encryption key must be a Fernet URL-safe key") from error

    def encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        try:
            return self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as error:
            raise ValueError(
                "stored credential cannot be decrypted; check credential key"
            ) from error
