"""Authentication and service-location responses.

The console posts form fields to ``/ac``. A successful service location tells
it which host serves downloads for ``svc`` 9000.
"""

from __future__ import annotations

import logging

from server.services.account.sessions import SessionStore, form_body, now_stamp

LOGGER = logging.getLogger(__name__)

_DOWNLOAD_SERVICE = "9000"


class NasService:
    def __init__(self, sessions: SessionStore, service_host: str) -> None:
        self._sessions = sessions
        self._service_host = service_host

    def handle(self, path: str, form: dict[str, str]) -> tuple[int, bytes]:
        if path == "/pr":
            return 200, self._profanity(form)
        if path != "/ac":
            LOGGER.warning("NAS unknown path %s", path)
            return 404, b""
        action = form.get("action", "").lower()
        if action == "acctcreate":
            fields = self._create()
        elif action == "login":
            fields = self._login(form)
        elif action == "svcloc":
            fields = self._locate(form)
        else:
            LOGGER.warning("NAS unknown action %s", action or "<missing>")
            return 400, b""
        fields["datetime"] = now_stamp()
        LOGGER.info("NAS %s -> %s", action, fields.get("returncd"))
        return 200, form_body(fields)

    def _create(self) -> dict[str, str]:
        session = self._sessions.create_account()
        return {"retry": "0", "returncd": "002", "userid": session.userid}

    def _login(self, form: dict[str, str]) -> dict[str, str]:
        userid = form.get("userid", "")
        if not userid:
            LOGGER.warning("NAS login without userid")
            return {"retry": "1", "returncd": "1100", "locator": "gamespy.com"}
        gamecd = form.get("gamecd", "") or form.get("gsbrcd", "")[:4]
        session = self._sessions.login(userid, gamecd)
        return {
            "retry": "0",
            "returncd": "001",
            "locator": "gamespy.com",
            "challenge": session.challenge,
            "token": session.token,
        }

    def _locate(self, form: dict[str, str]) -> dict[str, str]:
        userid = form.get("userid", "")
        session = self._sessions.login(userid or "0")
        fields = {"retry": "0", "returncd": "007", "statusdata": "Y"}
        service = form.get("svc", "")
        if service == _DOWNLOAD_SERVICE:
            fields["svchost"] = self._service_host
            fields["token"] = session.token
        elif service == "9001":
            fields["svchost"] = self._service_host
            fields["servicetoken"] = session.token
        else:
            fields["svchost"] = "n/a"
            fields["servicetoken"] = session.token
        return fields

    def _profanity(self, form: dict[str, str]) -> bytes:
        words = form.get("words", "")
        blanks = "0" * (len(words.split("\t")) if words else 0)
        fields = {"prwords": blanks, "returncd": "000", "datetime": now_stamp()}
        for letter in "ACEJKP":
            fields[f"prwords{letter}"] = blanks
        LOGGER.debug("NAS profanity check, %d words", blanks.count("0") if blanks else 0)
        return form_body(fields)
