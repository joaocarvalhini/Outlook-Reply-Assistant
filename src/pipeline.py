"""Per-message orchestration.

One message in, one outcome out. This is the only module that knows the order of
the stages and the only place where a failure becomes a decision: anything that
goes wrong -- Graph, the classifier, the drafter, a malformed response -- ends up
as the "needs a human" category on the original email. Nothing is ever silently
dropped, and nothing half-finished is ever written as a draft.

Every write to the mailbox goes through `_dry_run` first. Running with
DRY_RUN=true exercises the whole path, costs the same tokens, and touches
nothing -- which is how the first week in a client's mailbox should look.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classifier import Classifier, ClassifierError
from .drafter import Drafter, DrafterError
from .logger import get_logger
from .models import EmailMessage, Outcome, TokenUsage
from .outlook import GraphError, OutlookClient
from .state import ProcessingState
from .triage import Triage
from .utils import truncate

__all__ = ["Pipeline", "PipelineResult"]

_LOG = get_logger("pipeline")


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """What happened to one message, for the poll loop's summary line."""

    outcome: Outcome
    rule: str = ""
    reason: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(slots=True)
class Pipeline:
    """Runs triage -> classify -> draft -> write for a single message."""

    outlook: OutlookClient
    triage: Triage
    classifier: Classifier
    drafter: Drafter
    state: ProcessingState
    drafted_category: str
    escalated_category: str
    dry_run: bool = True

    def process(self, email: EmailMessage) -> PipelineResult:
        """Handle one message. Never raises."""
        if self.state.was_processed(email.ledger_key):
            return self._done(email, PipelineResult(Outcome.SKIPPED, rule="ledger"))

        verdict = self.triage.screen(email)
        if not verdict.accepted:
            return self._done(
                email, PipelineResult(Outcome.SKIPPED, rule=verdict.rule, reason=verdict.reason)
            )

        try:
            email = self.outlook.fetch_detail(email)
        except GraphError as exc:
            # No body means no decision to make. Leave it unmarked so the next
            # poll retries it rather than burying a real customer email.
            _LOG.error("detail fetch failed", extra={"email_id": email.id[:16], "error": exc})
            return PipelineResult(Outcome.FAILED, rule="fetch", reason=str(exc))

        verdict = self.triage.screen_headers(email)
        if not verdict.accepted:
            return self._done(
                email, PipelineResult(Outcome.SKIPPED, rule=verdict.rule, reason=verdict.reason)
            )

        try:
            classification = self.classifier.classify(email)
        except ClassifierError as exc:
            return self._escalate(email, f"Classificador falhou: {exc}", TokenUsage())

        usage = classification.usage
        if not classification.should_reply:
            return self._done(
                email,
                PipelineResult(
                    Outcome.SKIPPED,
                    rule=classification.category.value,
                    reason=classification.reason,
                    usage=usage,
                ),
            )

        try:
            draft = self.drafter.draft(email)
        except DrafterError as exc:
            return self._escalate(email, f"Gerador falhou: {exc}", usage)

        usage = usage + draft.usage
        if not draft.has_draft:
            return self._escalate(email, draft.reason, usage)

        try:
            if not self._dry_run("createReply", email):
                draft_id = self.outlook.create_reply_draft(email.id, draft.html)
                _LOG.info(
                    "draft created",
                    extra={"email_id": email.id[:16], "draft": draft_id[:16]},
                )
        except GraphError as exc:
            return self._escalate(email, f"Nao foi possivel criar o rascunho: {exc}", usage)

        return self._done(
            email, PipelineResult(Outcome.DRAFTED, rule=classification.category.value, usage=usage)
        )

    # -- internals ----------------------------------------------------------- #

    def _escalate(self, email: EmailMessage, reason: str, usage: TokenUsage) -> PipelineResult:
        """Flag the original for a human. The reason lives in the log, not the mailbox."""
        _LOG.warning(
            "escalated",
            extra={"email_id": email.id[:16], "reason": truncate(reason, 160)},
        )
        return self._done(
            email,
            PipelineResult(Outcome.ESCALATED, rule="escalation", reason=reason, usage=usage),
        )

    def _done(self, email: EmailMessage, result: PipelineResult) -> PipelineResult:
        """Mark the message, advance the watermark, persist.

        Marking happens last and on every terminal outcome, including skips:
        without it a restart re-classifies the same newsletter forever.
        """
        category = self._category_for(result.outcome)
        if category and not self._dry_run("addCategory", email):
            try:
                self.outlook.add_category(email, category)
            except GraphError as exc:
                # The ledger below still prevents a duplicate draft in this
                # process; only a restart would re-examine this message.
                _LOG.error(
                    "could not mark message", extra={"email_id": email.id[:16], "error": exc}
                )

        self.state.mark(email.ledger_key, email.received_at)
        self.state.save()
        return result

    def _category_for(self, outcome: Outcome) -> str:
        if outcome is Outcome.DRAFTED:
            return self.drafted_category
        if outcome is Outcome.ESCALATED:
            return self.escalated_category
        # Skipped mail is left visually untouched: the team's inbox should not
        # fill up with categories on newsletters they never asked us to label.
        return ""

    def _dry_run(self, operation: str, email: EmailMessage) -> bool:
        if not self.dry_run:
            return False
        _LOG.info("dry run", extra={"operation": operation, "email_id": email.id[:16]})
        return True
