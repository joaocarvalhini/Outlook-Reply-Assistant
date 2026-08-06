"""Microsoft Graph client.

The only component that talks to the mailbox. It exposes exactly four
operations, which is the whole surface this daemon needs:

    list_new_messages   what arrived since the watermark (cheap fields only)
    fetch_detail        headers and body, for messages that survived triage
    create_reply_draft  POST /createReply -- a threaded draft in Drafts
    add_category        the durable "we handled this" marker

`createReply` is load-bearing. It is what makes the draft a real reply: the
recipient, the RE: subject, the In-Reply-To / References headers and the
conversation grouping are all set by Graph. A draft composed by hand would look
correct in the Drafts list and break the thread the moment it is sent.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import httpx
import msal

from .config import Config
from .logger import get_logger
from .models import EmailMessage
from .utils import html_to_text, strip_quoted_reply

__all__ = ["GraphError", "OutlookClient"]

_LOG = get_logger("outlook")

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

_LIST_FIELDS = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,"
    "ccRecipients,receivedDateTime,categories"
)
_DETAIL_FIELDS = "internetMessageHeaders,body,categories"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3


class GraphError(RuntimeError):
    """Raised when Graph cannot serve a request the daemon depends on."""


def _addresses(entries: Any) -> tuple[str, ...]:
    """Pull the address out of Graph's nested recipient shape, defensively."""
    if not isinstance(entries, list):
        return ()
    found: list[str] = []
    for entry in entries:
        address = (entry or {}).get("emailAddress", {}).get("address")
        if address:
            found.append(str(address).lower())
    return tuple(found)


class OutlookClient:
    """Application-permission Graph client scoped to a single mailbox."""

    def __init__(self, config: Config, *, http: httpx.Client | None = None) -> None:
        self._mailbox = config.mailbox
        self._base = f"{GRAPH_ROOT}/users/{config.mailbox}"
        self._http = http or httpx.Client(timeout=30.0)
        self._app = msal.ConfidentialClientApplication(
            client_id=config.client_id,
            authority=f"https://login.microsoftonline.com/{config.tenant_id}",
            client_credential=config.client_secret,
        )

    # -- public API ---------------------------------------------------------- #

    def list_new_messages(self, since: str, *, limit: int = 25) -> list[EmailMessage]:
        """Return inbox messages received after `since`, oldest first.

        `since` is an empty string on the very first run, which is deliberately
        treated as "nothing yet" rather than "everything ever" -- a first run
        must not draft replies to a year of history.
        """
        if not since:
            _LOG.info("no watermark yet; starting from now")
            return []

        params = {
            "$filter": f"receivedDateTime gt {since}",
            "$select": _LIST_FIELDS,
            "$orderby": "receivedDateTime asc",
            "$top": str(limit),
        }
        payload = self._request("GET", f"{self._base}/mailFolders/inbox/messages", params=params)
        return [self._to_email(item) for item in payload.get("value", [])]

    def fetch_detail(self, email: EmailMessage) -> EmailMessage:
        """Return a copy of `email` with headers and a cleaned body attached."""
        payload = self._request(
            "GET", f"{self._base}/messages/{email.id}", params={"$select": _DETAIL_FIELDS}
        )

        headers = tuple(
            (str(item.get("name", "")), str(item.get("value", "")))
            for item in payload.get("internetMessageHeaders") or []
            if item.get("name")
        )
        body = strip_quoted_reply(html_to_text((payload.get("body") or {}).get("content", "")))

        return replace(
            email,
            headers=headers,
            body=body,
            categories=tuple(payload.get("categories") or email.categories),
        )

    def create_reply_draft(self, message_id: str, comment_html: str) -> str:
        """Create a threaded draft reply in Drafts. Returns the draft's id.

        Graph inserts `comment` above the quoted original, so the team reviews a
        complete reply rather than a bare paragraph.
        """
        payload = self._request(
            "POST",
            f"{self._base}/messages/{message_id}/createReply",
            json={"comment": comment_html},
        )
        draft_id = str(payload.get("id", ""))
        if not draft_id:
            raise GraphError("createReply returned no draft id")
        return draft_id

    def add_category(self, email: EmailMessage, category: str) -> None:
        """Append a category to the original message, preserving existing ones."""
        if category in email.categories:
            return
        self._request(
            "PATCH",
            f"{self._base}/messages/{email.id}",
            json={"categories": [*email.categories, category]},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OutlookClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------- #

    def _token(self) -> str:
        """Acquire a token. MSAL caches and refreshes it in-process."""
        result = self._app.acquire_token_for_client(scopes=[GRAPH_SCOPE])
        if not isinstance(result, dict) or "access_token" not in result:
            description = (result or {}).get("error_description", "unknown error")
            raise GraphError(f"Could not acquire a Graph token: {description}")
        return str(result["access_token"])

    def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        """Issue one Graph call, retrying the failures that are worth retrying.

        Throttling (429) and transient 5xx are retried with the server's own
        Retry-After; everything else fails immediately, because a 403 will not
        get better by being asked again.
        """
        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            headers = {
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
            }
            try:
                response = self._http.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                _LOG.warning("graph transport failure", extra={"attempt": attempt, "error": exc})
                time.sleep(min(2**attempt, 30))
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                delay = self._retry_after(response, attempt)
                _LOG.warning(
                    "graph throttled",
                    extra={"status": response.status_code, "attempt": attempt, "delay": delay},
                )
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                raise GraphError(
                    f"{method} {url.rsplit('/', 1)[-1]} failed "
                    f"({response.status_code}): {response.text[:300]}"
                )

            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise GraphError(f"Graph returned a non-JSON body: {exc}") from exc

        raise GraphError(f"Graph unreachable after {_MAX_ATTEMPTS} attempts. {last_error}")

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), 120.0)
            except ValueError:
                pass
        return float(min(2**attempt, 30))

    @staticmethod
    def _to_email(item: dict) -> EmailMessage:
        sender = (item.get("from") or {}).get("emailAddress", {})
        return EmailMessage(
            id=str(item.get("id", "")),
            conversation_id=str(item.get("conversationId", "")),
            internet_message_id=str(item.get("internetMessageId", "")),
            subject=str(item.get("subject") or ""),
            sender=str(sender.get("address") or "").lower(),
            sender_name=str(sender.get("name") or ""),
            to=_addresses(item.get("toRecipients")),
            cc=_addresses(item.get("ccRecipients")),
            received_at=str(item.get("receivedDateTime", "")),
            categories=tuple(item.get("categories") or ()),
        )
