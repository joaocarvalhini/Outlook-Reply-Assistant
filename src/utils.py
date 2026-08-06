"""Small, dependency-free helpers shared across the package.

Two jobs live here that are easy to get wrong and expensive to get wrong twice:
turning an Outlook HTML body into something worth sending to a model, and
turning a model's HTML back into something safe to store in a draft.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from io import StringIO

__all__ = [
    "clean_document_text",
    "html_to_text",
    "normalize_whitespace",
    "sanitize_html",
    "strip_quoted_reply",
    "truncate",
]

_WHITESPACE_RE = re.compile(r"\s+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)

_BOM = chr(0xFEFF)


def normalize_whitespace(text: str) -> str:
    """Collapse every run of whitespace into a single space and trim."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_document_text(text: str) -> str:
    """Normalise a knowledge-base document before it enters the prompt.

    Deterministic on purpose: the system prompt is the cached prefix of every
    request, so byte-stable output is what makes cache hits possible.
    """
    text = text.lstrip(_BOM)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE_RE.sub("", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    """Shorten `text` to at most `limit` characters, appending `suffix`."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return text
    if limit <= len(suffix):
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


# --------------------------------------------------------------------------- #
# Inbound: Outlook HTML -> plain text
# --------------------------------------------------------------------------- #

_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
)
_DROP_TAGS = frozenset({"script", "style", "head", "title"})


class _TextExtractor(HTMLParser):
    """Flattens HTML into text, inserting newlines at block boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out = StringIO()
        self._suppress = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROP_TAGS:
            self._suppress += 1
        elif tag in _BLOCK_TAGS:
            self._out.write("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS and self._suppress:
            self._suppress -= 1
        elif tag in _BLOCK_TAGS:
            self._out.write("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._out.write(data)

    @property
    def text(self) -> str:
        return self._out.getvalue()


def html_to_text(content: str) -> str:
    """Flatten an email body to plain text.

    Accepts HTML or plain text — Outlook returns either depending on how the
    sender's client composed the message.
    """
    if not content:
        return ""

    if "<" in content and ">" in content:
        parser = _TextExtractor()
        try:
            parser.feed(content)
            parser.close()
            content = parser.text
        except Exception:  # malformed markup: fall back to the raw string
            content = re.sub(r"<[^>]+>", " ", content)
            content = html.unescape(content)

    content = content.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    content = _TRAILING_SPACE_RE.sub("", content)
    content = _BLANK_LINES_RE.sub("\n\n", content)
    return content.strip()


# Markers that begin a quoted thread. Portuguese, English and the Outlook
# separator, which is what most of this mailbox's traffic will carry.
_QUOTE_MARKERS = (
    re.compile(r"^-{2,}\s*(mensagem original|original message)\s*-{2,}", re.I | re.M),
    re.compile(r"^_{5,}\s*$", re.M),
    re.compile(r"^\s*(de|from)\s*:.*\n\s*(enviad[ao]|sent)\s*:", re.I | re.M),
    re.compile(r"^\s*(em|on)\b.{0,120}\b(escreveu|wrote)\s*:\s*$", re.I | re.M),
)


def strip_quoted_reply(text: str) -> str:
    """Drop the quoted thread below a reply.

    The quoted history is usually longer than the new message and adds nothing
    the model needs — the customer's actual question is at the top. Cutting it
    is the single largest input-token saving in the pipeline.
    """
    if not text:
        return ""

    cut = len(text)
    for marker in _QUOTE_MARKERS:
        match = marker.search(text)
        if match and match.start() < cut:
            cut = match.start()

    head = text[:cut]

    # Also drop a trailing run of ">" quoted lines, which some clients emit
    # without any separator at all.
    lines = head.split("\n")
    while lines and lines[-1].lstrip().startswith(">"):
        lines.pop()

    return "\n".join(lines).strip() or text.strip()


# --------------------------------------------------------------------------- #
# Outbound: model HTML -> draft-safe HTML
# --------------------------------------------------------------------------- #

# Deliberately narrow. A draft body only needs paragraphs, emphasis and lists;
# anything else is either the model improvising or an injection attempt riding
# in from the customer's email.
_ALLOWED_TAGS = frozenset({"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li"})
_VOID_TAGS = frozenset({"br"})


class _Sanitizer(HTMLParser):
    """Rebuilds HTML from an allowlist, escaping everything else."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out = StringIO()
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_TAGS:
            return
        # Attributes are dropped wholesale: no href, no style, no event handlers,
        # so there is nothing left for `javascript:` or `onerror=` to attach to.
        if tag in _VOID_TAGS:
            self._out.write("<br>")
            return
        self._out.write(f"<{tag}>")
        self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            self._out.write("<br>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag in self._open:
            while self._open:
                current = self._open.pop()
                self._out.write(f"</{current}>")
                if current == tag:
                    break

    def handle_data(self, data: str) -> None:
        self._out.write(html.escape(data, quote=False))

    @property
    def html(self) -> str:
        closing = "".join(f"</{tag}>" for tag in reversed(self._open))
        return self._out.getvalue() + closing


def sanitize_html(content: str) -> str:
    """Reduce model output to a small, safe subset of HTML.

    The draft lands in the team's own mailbox and is read before it is sent, but
    "a human reviews it" is not a security control — the body is derived from an
    untrusted email, so it is rebuilt from an allowlist rather than filtered.
    """
    if not content:
        return ""

    parser = _Sanitizer()
    try:
        parser.feed(content)
        parser.close()
    except Exception:
        return f"<p>{html.escape(html_to_text(content), quote=False)}</p>"

    cleaned = parser.html.strip()
    if not cleaned:
        return ""
    if "<" not in cleaned:
        return f"<p>{cleaned}</p>"
    return cleaned
