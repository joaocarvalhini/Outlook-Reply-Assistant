"""Loading, cleaning and combining local knowledge-base documents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger
from .models import Document, KnowledgeBase
from .utils import clean_document_text

__all__ = ["KnowledgeBaseError", "KnowledgeBaseLoader"]

_LOG = get_logger("knowledge_base")

DEFAULT_EXTENSIONS: tuple[str, ...] = (".md", ".txt")


class KnowledgeBaseError(RuntimeError):
    """Raised when no usable knowledge base can be produced."""


@dataclass(frozen=True, slots=True)
class KnowledgeBaseLoader:
    """Reads every supported document under `directory` into a `KnowledgeBase`.

    Unreadable or empty files are skipped with a warning rather than aborting
    the load; a directory with no usable content at all is a hard error, because
    an assistant without a knowledge base escalates every single email.
    """

    directory: Path
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    recursive: bool = True

    def load(self) -> KnowledgeBase:
        """Discover, read and clean every document. Raises `KnowledgeBaseError`."""
        directory = self.directory
        if not directory.exists():
            raise KnowledgeBaseError(f"Knowledge base directory not found: {directory}")
        if not directory.is_dir():
            raise KnowledgeBaseError(f"Knowledge base path is not a directory: {directory}")

        paths = self._discover(directory)
        if not paths:
            raise KnowledgeBaseError(
                f"No {' or '.join(self.extensions)} files found in {directory}"
            )

        documents: list[Document] = []
        for path in paths:
            content = self._read(path)
            if content is None:
                continue
            documents.append(
                Document(name=self._relative_name(path, directory), path=path, content=content)
            )

        if not documents:
            raise KnowledgeBaseError(f"Every document in {directory} was empty or unreadable")

        knowledge_base = KnowledgeBase(documents=tuple(documents))
        _LOG.info(
            "knowledge base loaded",
            extra={
                "directory": str(directory),
                "documents": len(documents),
                "chars": knowledge_base.total_chars,
            },
        )
        return knowledge_base

    def _discover(self, directory: Path) -> list[Path]:
        """Return matching files in a stable, reproducible order."""
        pattern = "**/*" if self.recursive else "*"
        allowed = {extension.lower() for extension in self.extensions}
        paths = [
            path
            for path in directory.glob(pattern)
            if path.is_file() and path.suffix.lower() in allowed
        ]
        # Deterministic ordering keeps the cached prompt prefix byte-stable.
        return sorted(paths, key=lambda path: path.as_posix().lower())

    def _read(self, path: Path) -> str | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            _LOG.warning("skipping non-utf8 document", extra={"path": str(path)})
            return None
        except OSError as exc:
            _LOG.warning("skipping unreadable document", extra={"path": str(path), "error": exc})
            return None

        content = clean_document_text(raw)
        if not content:
            _LOG.warning("skipping empty document", extra={"path": str(path)})
            return None
        return content

    @staticmethod
    def _relative_name(path: Path, directory: Path) -> str:
        try:
            return path.relative_to(directory).as_posix()
        except ValueError:  # pragma: no cover - defensive
            return path.name
