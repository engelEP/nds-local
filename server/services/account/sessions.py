"""Account numbers for the authentication step.

The console creates an account, logs in, then asks where its download service
lives. Each reply carries a fresh token. The presence server looks that token
up again when the console opens the GameSpy connection.
"""

from __future__ import annotations

import base64
import binascii
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import unquote

from server.services.store import PlayerStore


@dataclass(frozen=True)
class Session:
    userid: str
    token: str
    challenge: str


@dataclass(frozen=True)
class LoginRecord:
    """The NAS login the console will present to the presence server."""

    userid: str
    challenge: str
    gamecd: str


class SessionStore:
    def __init__(self, players: PlayerStore | None = None) -> None:
        self._players = players
        self._lock = threading.Lock()
        self._next_id = 1
        self._logins: dict[str, LoginRecord] = {}

    def create_account(self) -> Session:
        if self._players is not None:
            userid = self._players.create_account()
        else:
            with self._lock:
                userid = str(self._next_id)
                self._next_id += 1
        return Session(userid=userid, token=_token(), challenge=_challenge())

    def login(self, userid: str, gamecd: str = "") -> Session:
        session = Session(userid=userid, token=_token(), challenge=_challenge())
        record = LoginRecord(userid=userid, challenge=session.challenge, gamecd=gamecd)
        with self._lock:
            self._logins[session.token] = record
        return session

    def recall(self, token: str) -> LoginRecord | None:
        with self._lock:
            return self._logins.get(token)


def parse_form(body: bytes) -> dict[str, str]:
    """Split a form body without treating '+' as a space.

    Nintendo's base64 uses '+', and the console does not percent-encode it.
    """
    text = body.decode("utf-8", errors="replace")
    fields: dict[str, str] = {}
    for part in text.split("&"):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if sep and key not in fields:
            fields[key] = value
    return fields


def decode_form(form: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Decode Nintendo base64 values. The flag is true when any value changed.

    The download request writes the padding star as ``%2A``. Account requests
    send the star itself. Percent-decoding happens before the base64 step.
    """
    decoded = {key: decode_nintendo(unquote(value)) for key, value in form.items()}
    return decoded, decoded != form


def encode_form_body(body: bytes) -> bytes:
    """Encode each value the way the console decodes a NAS reply."""
    fields = {key: encode_nintendo(value) for key, value in parse_form(body).items()}
    return form_body(fields)


def decode_nintendo(token: str) -> str:
    """Return the text behind a Nintendo base64 token, or the token itself.

    Padding is '*' instead of '='. A value is left unchanged unless decoding
    it and encoding it again produces the same token, and the text is printable.
    That keeps a plain word such as 'acctcreate' or a code such as 'YDQE'.
    """
    if not token:
        return token
    padded = token.replace("*", "=")
    padded += "=" * ((-len(padded)) % 4)
    try:
        text = base64.b64decode(padded, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return token
    if any(ord(char) < 32 or ord(char) > 126 for char in text):
        return token
    if encode_nintendo(text) != token.replace("=", "*"):
        return token
    return text


def encode_nintendo(text: str) -> str:
    return base64.b64encode(text.encode("ascii")).decode("ascii").replace("=", "*")


def form_body(fields: dict[str, str]) -> bytes:
    parts = [f"{_form_token(key)}={_form_token(value)}" for key, value in fields.items()]
    return "&".join(parts).encode("ascii")


def _form_token(value: str) -> str:
    """Keep the body on one line. A value with '&' or a newline would split fields."""
    return "".join(char for char in value if 32 <= ord(char) < 127 and char != "&")


def now_stamp() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime())


def _token() -> str:
    return secrets.token_hex(16)


def _challenge() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))
