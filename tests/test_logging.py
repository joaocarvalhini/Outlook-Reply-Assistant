"""Structured logging.

One of these tests exists because of a real bug: `extra={"message": ...}`
collides with a reserved `LogRecord` attribute and raises `KeyError` inside the
logging module. Nothing catches it, and it only fires the first time that exact
line runs -- which for the classifier meant the first successfully classified
email, in production, after every unit test had passed.

So rather than pinning the one field name that broke, the scan below walks the
source for every `extra=` key and rejects any that would collide.
"""

from __future__ import annotations

import ast
import logging
import unittest
from pathlib import Path

from src.logger import LOGGER_ROOT, StructuredFormatter, get_logger
from src.logger import _RESERVED_FIELDS as RESERVED

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCANNED = sorted(PROJECT_ROOT.glob("src/*.py")) + [
    PROJECT_ROOT / "daemon.py",
    PROJECT_ROOT / "eval.py",
]


def _extra_keys(path: Path) -> list[tuple[str, int]]:
    """Return every literal key passed as `extra={...}`, with its line number."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((key.value, key.lineno))
    return found


class ExtraFieldTests(unittest.TestCase):
    def test_no_extra_key_collides_with_a_reserved_attribute(self) -> None:
        offenders: list[str] = []
        for path in SCANNED:
            if not path.exists():
                continue
            for key, line in _extra_keys(path):
                if key in RESERVED:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line} -> {key!r}")

        self.assertEqual(
            offenders,
            [],
            "reserved LogRecord attributes used as extra= keys; logging raises "
            "KeyError at runtime:\n  " + "\n  ".join(offenders),
        )

    def test_the_scan_would_actually_catch_one(self) -> None:
        """A guard that cannot fail guards nothing."""
        self.assertIn("message", RESERVED)
        self.assertIn("args", RESERVED)
        self.assertNotIn("email_id", RESERVED)

    def test_every_scanned_file_was_parsed(self) -> None:
        for path in SCANNED:
            with self.subTest(path=path.name):
                self.assertTrue(path.exists(), f"{path} disappeared from the scan list")


class FormatterTests(unittest.TestCase):
    def test_renders_extras_as_key_value(self) -> None:
        record = logging.LogRecord(
            name=f"{LOGGER_ROOT}.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="classified",
            args=(),
            exc_info=None,
        )
        record.email_id = "AAMkAGI2"
        record.category = "pedido_cliente"

        line = StructuredFormatter().format(record)

        self.assertIn("classified", line)
        self.assertIn("email_id=AAMkAGI2", line)
        self.assertIn("category=pedido_cliente", line)

    def test_quotes_values_containing_spaces(self) -> None:
        record = logging.LogRecord(
            name=f"{LOGGER_ROOT}.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="escalated",
            args=(),
            exc_info=None,
        )
        record.reason = "Base de conhecimento nao refere pagamentos"

        self.assertIn('reason="Base de conhecimento', StructuredFormatter().format(record))

    def test_logging_with_a_reserved_key_raises(self) -> None:
        """Documents the failure mode the scan above prevents.

        The level has to be raised explicitly, and that is the second half of
        why the original bug reached production: with no `configure_logging`
        call the package logger sits at the root's WARNING, so `.info()` returns
        before a LogRecord is ever built and the collision never fires. The
        daemon does configure logging, so there it fires on the first call.
        """
        logger = get_logger("test")
        previous = logger.level
        handler = logging.NullHandler()
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.setLevel, previous)
        self.addCleanup(logger.removeHandler, handler)

        with self.assertRaises(KeyError):
            logger.info("boom", extra={"message": "collides"})


if __name__ == "__main__":
    unittest.main()
