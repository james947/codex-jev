"""Bounded TypeSafe classification. Codex authentication stays in Codex."""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from codex_jev.policy import Decision, QUESTIONS, RoutingError, decide


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JevClient:
    def __init__(self, api_key: str, timeout: float = 8.0):
        self._api_key = api_key
        self.timeout = timeout

    def _classify(self, current: str, previous: str) -> Decision:
        if not self._api_key:
            raise RoutingError("Set TYPESAFE_API_KEY locally to enable automatic routing.")
        payload = {
            "model": "jev-latest",
            "state": json.dumps({"current_request": current[:4000], "previous_request": previous[:1000]}),
            "questions": QUESTIONS,
        }
        request = Request(
            "https://api.typesafe.ai/v1/systemone",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirects()).open(request, timeout=self.timeout) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise RoutingError("Jev response exceeded the size limit.")
            return decide(json.loads(raw))
        except HTTPError as exc:
            exc.close()
            raise RoutingError(f"Jev returned HTTP {exc.code}; no Codex turn was started.") from None
        except (URLError, TimeoutError, OSError):
            raise RoutingError("Jev is unreachable or timed out; no Codex turn was started.") from None
        except (ValueError, UnicodeError):
            raise RoutingError("Jev returned an unreadable response; no Codex turn was started.") from None

    async def classify(self, current: str, previous: str = "") -> Decision:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._classify, current, previous), timeout=self.timeout + 1
            )
        except TimeoutError:
            raise RoutingError("Jev timed out; no Codex turn was started.") from None
