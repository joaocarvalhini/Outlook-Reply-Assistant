#!/usr/bin/env python3
"""Evaluation harness: measures what the pipeline decides, against fixtures.

    python eval.py                         # every stage, uses .env and eval/cases.json
    python eval.py --stage triage          # deterministic rules only: free, instant
    python eval.py --cases eval/real.json  # a curated set from the client's mailbox

Runs the real triage, the real classifier and the real drafter against fixed
emails with a known expected outcome. No mailbox is touched and no draft is
created -- Graph is not involved at all, so this is safe to run against a live
configuration.

Each case declares one terminal expectation:

    skip      the pipeline should never reply to this
    escalate  a human should look at it; the assistant must not answer
    draft     the assistant should be able to answer from the knowledge base

Three numbers come out, and they are not equally important.

  lost customers  cases that should have produced a draft or an escalation and
                  were silently dropped instead. This is the number that costs
                  money: in production a discarded customer email leaves no
                  trace anyone looks at. Target is zero.
  recall          of the cases that SHOULD escalate, how many did. Low recall
                  means the assistant answered something it did not know --
                  it invented a policy and a customer believed it.
  precision       of the cases the assistant DID escalate, how many should have.
                  Low precision means humans get work they did not need.
                  Annoying, but safe.

`--stage triage` needs neither an API key nor a knowledge base, which makes it
the loop to run while tuning blocklist.txt.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from src.classifier import Classifier, ClassifierError
from src.config import Config, ConfigError
from src.drafter import Drafter, DrafterError
from src.knowledge_base import KnowledgeBaseError, KnowledgeBaseLoader
from src.logger import configure_logging
from src.models import EmailMessage, Outcome, TokenUsage
from src.prompts import ClassifierPromptBuilder, ReplyPromptBuilder
from src.triage import Triage, load_blocklist
from src.utils import truncate

DEFAULT_CASES = Path("eval/cases.json")

# The three terminal expectations, in the order a case travels through them.
EXPECTATIONS = ("skip", "escalate", "draft")

# Anything but "skip" is mail a human would have wanted to see.
CUSTOMER_MAIL = ("escalate", "draft")

# Not an expectation: a case that never got a verdict because the API failed.
# In production such a message escalates, which is correct -- but scoring it as
# a correct escalation here would let a dead API key report a passing run.
ERROR = "error"


@dataclass(frozen=True, slots=True)
class Case:
    """One fixture: an email and what the pipeline is supposed to do with it."""

    id: str
    email: dict
    expect: str
    note: str = ""
    # Optional. When set, the classifier's category is checked too, which is how
    # a case pins down *why* a message was skipped rather than just that it was.
    expect_category: str = ""

    def to_message(self, mailbox: str) -> EmailMessage:
        """Build the message the pipeline would have received from Graph.

        Addresses may contain `{mailbox}` and `{domain}`, substituted with the
        configured values. Without that, a fixture asserting "sender is a
        colleague" would only hold for whichever shop the fixtures were written
        against, and would quietly stop testing anything for every other client.

        `body` is the cleaned plain text, i.e. what `outlook.fetch_detail` would
        already have produced -- fixtures are authored as text, not as the HTML
        soup a mail client emits.
        """
        domain = mailbox.partition("@")[2]

        def resolve(address: object) -> str:
            return str(address).format(mailbox=mailbox, domain=domain).lower()

        return EmailMessage(
            id=f"eval-{self.id}",
            conversation_id=f"conv-{self.id}",
            internet_message_id=f"<{self.id}@eval.local>",
            subject=str(self.email.get("subject", "")),
            sender=resolve(self.email.get("from", "")),
            sender_name=str(self.email.get("from_name", "")),
            to=tuple(resolve(a) for a in self.email.get("to", [])),
            cc=tuple(resolve(a) for a in self.email.get("cc", [])),
            received_at="2026-08-06T10:00:00Z",
            body=str(self.email.get("body", "")),
            headers=tuple((str(k), str(v)) for k, v in self.email.get("headers", [])),
            categories=tuple(self.email.get("categories", [])),
        )


@dataclass(frozen=True, slots=True)
class Result:
    case: Case
    actual: str
    stage: str
    detail: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def correct(self) -> bool:
        return self.actual == self.case.expect


class Harness:
    """Runs one case through as much of the pipeline as the stage allows."""

    def __init__(
        self,
        triage: Triage,
        mailbox: str,
        classifier: Classifier | None = None,
        drafter: Drafter | None = None,
    ) -> None:
        self._triage = triage
        self._mailbox = mailbox
        self._classifier = classifier
        self._drafter = drafter

    def evaluate(self, case: Case) -> Result:
        email = case.to_message(self._mailbox)

        verdict = self._triage.screen(email)
        if not verdict.accepted:
            return Result(case, "skip", "triage", verdict.rule)

        verdict = self._triage.screen_headers(email)
        if not verdict.accepted:
            return Result(case, "skip", "headers", verdict.rule)

        if self._classifier is None or self._drafter is None:
            # Triage-only run: the message survived every free rule, which is
            # all this stage can assert. Anything expecting more is untested,
            # not passing, so report the stage honestly.
            return Result(case, "pass", "triage", "reached the model stages")

        try:
            classification = self._classifier.classify(email)
        except ClassifierError as exc:
            return Result(case, ERROR, "classifier", truncate(str(exc), 110))

        usage = classification.usage
        if not classification.should_reply:
            return Result(case, "skip", "classifier", classification.category.value, usage)

        if case.expect_category and classification.category.value != case.expect_category:
            # Not a failure on its own -- the outcome may still be right -- but
            # worth surfacing, because a drifting category is an early warning.
            detail = f"{classification.category.value} (esperado {case.expect_category})"
        else:
            detail = classification.category.value

        try:
            draft = self._drafter.draft(email)
        except DrafterError as exc:
            return Result(case, ERROR, "drafter", truncate(str(exc), 110), usage)

        usage = usage + draft.usage
        if draft.outcome is Outcome.DRAFTED:
            return Result(case, "draft", "drafter", detail, usage)
        return Result(case, "escalate", "drafter", truncate(draft.reason, 90), usage)


def load_cases(path: Path) -> list[Case]:
    """Read and validate the fixture file. A bad case is a hard error."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [Case(**entry) for entry in raw]

    seen: set[str] = set()
    for case in cases:
        if case.expect not in EXPECTATIONS:
            raise ValueError(
                f"case {case.id!r} has invalid expect {case.expect!r}; "
                f"use one of {', '.join(EXPECTATIONS)}"
            )
        if case.id in seen:
            raise ValueError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
    return cases


def summarize(results: list[Result], *, triage_only: bool) -> int:
    """Print the report and return the exit code."""
    print()
    for result in results:
        if triage_only and result.actual == "pass":
            marker, actual = "----", "reached the model"
        elif result.actual == ERROR:
            marker, actual = "ERRO", "sem veredito"
        else:
            marker = "PASS" if result.correct else "FAIL"
            actual = result.actual
        print(
            f"{marker}  {result.case.id:<32} "
            f"expect={result.case.expect:<9} actual={actual:<18} "
            f"[{result.stage}] {result.detail}"
        )
        if marker == "FAIL" and result.case.note:
            print(f"        nota: {result.case.note}")

    errors = [r for r in results if r.actual == ERROR]
    judged = [
        r
        for r in results
        if r.actual != ERROR and not (triage_only and r.actual == "pass")
    ]
    failures = [r for r in judged if not r.correct]

    should_escalate = [r for r in judged if r.case.expect == "escalate"]
    did_escalate = [r for r in judged if r.actual == "escalate"]
    caught = [r for r in should_escalate if r.actual == "escalate"]

    # The expensive failure: mail a human wanted to see, dropped without trace.
    lost = [r for r in judged if r.case.expect in CUSTOMER_MAIL and r.actual == "skip"]

    usage = TokenUsage()
    for result in results:
        usage = usage + result.usage

    print()
    print(f"{len(judged) - len(failures)}/{len(judged)} casos corretos")
    deferred = len(results) - len(judged) - len(errors)
    if triage_only and deferred:
        print(f"{deferred} casos passaram a triagem (nao avaliados nesta etapa)")
    if errors:
        # Loud and separate. Without this a dead API key reads as a passing run,
        # because every unanswered case escalates and escalation looks correct.
        print(f"ERROS TECNICOS:         {len(errors)}  ->  resultados nao sao de confianca")

    label = 24
    if lost:
        names = ", ".join(r.case.id for r in lost)
        print(f"{'CLIENTES PERDIDOS:':<{label}}{len(lost)}  ->  {names}")
    else:
        print(f"{'clientes perdidos:':<{label}}0")

    recall = f"{len(caught) / len(should_escalate):.0%}" if should_escalate else "n/a"
    precision = f"{len(caught) / len(did_escalate):.0%}" if did_escalate else "n/a"
    print(f"{'recall de escalacao:':<{label}}{recall}")
    print(f"{'precisao de escalacao:':<{label}}{precision}")

    if usage.input_tokens or usage.output_tokens:
        print(
            f"{'tokens:':<{label}}entrada={usage.input_tokens} "
            f"saida={usage.output_tokens} cache={usage.cache_read_tokens}"
        )
    print()

    # A lost customer fails the run even if the arithmetic elsewhere looks fine,
    # and so does a technical error: a partial run proves nothing.
    return 1 if failures or lost or errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--stage",
        choices=("triage", "all"),
        default="all",
        help="triage runs the deterministic rules only: no API key, no cost",
    )
    parser.add_argument("--knowledge-dir", help="override KNOWLEDGE_DIR for this run")
    parser.add_argument("--reply-model", help="override REPLY_MODEL for this run")
    parser.add_argument("--mailbox", help="override MAILBOX; fixtures interpolate it")
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)

    configure_logging("WARNING")

    overrides: dict[str, str | None] = {}
    if args.knowledge_dir:
        overrides["KNOWLEDGE_DIR"] = args.knowledge_dir
    if args.reply_model:
        overrides["REPLY_MODEL"] = args.reply_model
    if args.mailbox:
        overrides["MAILBOX"] = args.mailbox

    # No stage of the evaluation touches Graph, so demanding a tenant, a client
    # id and a secret would block a run that has everything it actually needs.
    for name in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        overrides.setdefault(name, "unused-by-eval")

    # The mailbox is real input: triage rules compare against it, and fixtures
    # interpolate it. It is defaulted rather than required so the free stage
    # runs on a fresh checkout, and echoed below so nobody has to guess.
    overrides.setdefault("MAILBOX", "apoio@loja.pt")

    if args.stage == "triage":
        overrides.setdefault("ANTHROPIC_API_KEY", "unused-by-triage-stage")

    try:
        config = Config.from_env(env_file=args.env_file, overrides=overrides)
        cases = load_cases(args.cases)
    except (ConfigError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Arranque falhou: {exc}", file=sys.stderr)
        return 2

    triage = Triage(
        mailbox=config.mailbox,
        own_domain=config.mailbox_domain,
        drafted_category=config.drafted_category,
        escalated_category=config.escalated_category,
        blocked_domains=load_blocklist(config.blocklist_file),
    )

    if args.stage == "triage":
        harness = Harness(triage, config.mailbox)
        print(
            f"A correr {len(cases)} caso(s), apenas triagem, "
            f"com {args.cases} e caixa {config.mailbox}"
        )
    else:
        import anthropic

        try:
            knowledge_base = KnowledgeBaseLoader(directory=config.knowledge_dir).load()
        except KnowledgeBaseError as exc:
            print(f"Base de conhecimento: {exc}", file=sys.stderr)
            return 2

        messages_client = anthropic.Anthropic(api_key=config.api_key).messages
        harness = Harness(
            triage,
            config.mailbox,
            Classifier(
                client=messages_client,
                model=config.classifier_model,
                prompts=ClassifierPromptBuilder(company_name=config.company_name),
            ),
            Drafter(
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
            ),
        )
        print(
            f"A correr {len(cases)} caso(s) com {config.classifier_model} "
            f"e {config.reply_model}, a partir de {args.cases} e caixa {config.mailbox}"
        )

    results = [harness.evaluate(case) for case in cases]
    return summarize(results, triage_only=args.stage == "triage")


if __name__ == "__main__":
    raise SystemExit(main())
