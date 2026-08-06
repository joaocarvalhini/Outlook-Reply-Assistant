#!/usr/bin/env python3
"""Poll loop.

Startup order matters: configuration, knowledge base and Graph credentials are
all validated before the first poll, so a misconfigured deployment fails on the
command line rather than three hours later in a client's mailbox.

    python daemon.py --once          one pass, then exit (use for cron)
    python daemon.py                 poll forever
    python daemon.py --no-dry-run    actually write to the mailbox
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import FrameType

import anthropic

from src import __version__
from src.classifier import Classifier
from src.config import Config, ConfigError
from src.drafter import Drafter
from src.knowledge_base import KnowledgeBaseError, KnowledgeBaseLoader
from src.logger import configure_logging, get_logger
from src.models import Outcome
from src.outlook import GraphError, OutlookClient
from src.pipeline import Pipeline
from src.prompts import ClassifierPromptBuilder, ReplyPromptBuilder
from src.state import ProcessingState
from src.triage import Triage, load_blocklist

_LOG = get_logger("cli")

BATCH_SIZE = 25

_stopping = False


def _handle_signal(_signum: int, _frame: FrameType | None) -> None:
    """Finish the message in flight, then exit. Never kill mid-write."""
    global _stopping
    _stopping = True
    _LOG.info("stop requested; finishing current batch")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Outlook reply assistant")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--env-file", type=Path, default=None, help="path to a .env file")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--log-file", default=None, help="also write an INFO-level log here")
    dry = parser.add_mutually_exclusive_group()
    dry.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    dry.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    return parser.parse_args(argv)


def _build_config(args: argparse.Namespace) -> Config:
    overrides: dict[str, str | None] = {}
    if args.log_level:
        overrides["LOG_LEVEL"] = args.log_level
    if args.log_file:
        overrides["LOG_FILE"] = args.log_file
    if args.dry_run is not None:
        overrides["DRY_RUN"] = "true" if args.dry_run else "false"
    return Config.from_env(env_file=args.env_file, overrides=overrides)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_once(pipeline: Pipeline, outlook: OutlookClient, state: ProcessingState) -> None:
    """Process everything that arrived since the watermark."""
    try:
        messages = outlook.list_new_messages(state.last_received, limit=BATCH_SIZE)
    except GraphError as exc:
        _LOG.error("could not list messages", extra={"error": exc})
        return

    if not messages:
        _LOG.debug("nothing new")
        return

    counts: dict[Outcome, int] = {}
    for email in messages:
        if _stopping:
            break
        result = pipeline.process(email)
        counts[result.outcome] = counts.get(result.outcome, 0) + 1

    _LOG.info(
        "batch done",
        extra={
            "seen": len(messages),
            **{outcome.value: count for outcome, count in counts.items()},
        },
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        config = _build_config(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.log_level, config.log_file)
    _LOG.info(
        "startup",
        extra={
            "version": __version__,
            "mailbox": config.mailbox,
            "classifier": config.classifier_model,
            "reply": config.reply_model,
            "dry_run": config.dry_run,
        },
    )

    try:
        knowledge_base = KnowledgeBaseLoader(directory=config.knowledge_dir).load()
    except KnowledgeBaseError as exc:
        print(f"Knowledge base error: {exc}", file=sys.stderr)
        return 2

    messages_client = anthropic.Anthropic(api_key=config.api_key).messages

    state = ProcessingState.load(config.state_file)
    if not state.last_received:
        # First run: start from now. Drafting replies to a year of history would
        # be both expensive and wrong.
        state.last_received = _now()
        state.save()
        _LOG.info("watermark initialised", extra={"from": state.last_received})

    triage = Triage(
        mailbox=config.mailbox,
        own_domain=config.mailbox_domain,
        drafted_category=config.drafted_category,
        escalated_category=config.escalated_category,
        blocked_domains=load_blocklist(config.blocklist_file),
    )
    classifier = Classifier(
        client=messages_client,
        model=config.classifier_model,
        prompts=ClassifierPromptBuilder(company_name=config.company_name),
    )
    drafter = Drafter(
        client=messages_client,
        model=config.reply_model,
        prompts=ReplyPromptBuilder(
            company_name=config.company_name,
            assistant_name=config.assistant_name,
            signature=config.signature,
        ),
        knowledge_base=knowledge_base,
        max_tokens=config.max_tokens,
        max_body_chars=config.max_body_chars,
    )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    with OutlookClient(config) as outlook:
        pipeline = Pipeline(
            outlook=outlook,
            triage=triage,
            classifier=classifier,
            drafter=drafter,
            state=state,
            drafted_category=config.drafted_category,
            escalated_category=config.escalated_category,
            dry_run=config.dry_run,
        )

        if args.once:
            run_once(pipeline, outlook, state)
            return 0

        while not _stopping:
            run_once(pipeline, outlook, state)
            for _ in range(config.poll_seconds):
                if _stopping:
                    break
                time.sleep(1)

    _LOG.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
