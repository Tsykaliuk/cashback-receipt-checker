import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from .fingerprint import image_hash, normalize_email, receipt_fingerprint
from .images import JPEG_MIME, prepare_image
from .models import Decision, Promo, RawReceipt, ReaderKind, Receipt, Rejection, Submission
from .rules import PARTICIPANT_USED, RECEIPT_USED, History, evaluate
from .storage import AlreadyClaimedError, ClaimKind, Storage


class ReceiptReader(Protocol):
    def read(self, image: bytes) -> RawReceipt: ...


class ReceiptInterpreter(Protocol):
    def from_text(self, raw: RawReceipt, promo: Promo) -> Receipt: ...

    def from_image(self, image: bytes, mime_type: str, promo: Promo) -> Receipt: ...


class Step(StrEnum):
    PRECHECK = "Перевіряємо, чи не подавали цей чек або email раніше"
    TEXTRACT = "AWS Textract читає чек"
    GEMINI_TEXT = "Gemini розбирає текст від Textract і шукає акційний товар"
    GEMINI_VISION = "Textract не впорався, Gemini читає фото напряму"
    GEMINI_PHOTO = "Gemini читає фото чека і шукає акційний товар"
    RULES = "Перевіряємо умови акції"
    SAVE = "Зберігаємо фото в S3 і заявку в DynamoDB"


@dataclass(frozen=True)
class CheckResult:
    submission: Submission
    receipt: Receipt | None
    raw: RawReceipt | None


CLAIM_REJECTIONS: dict[ClaimKind, Rejection] = {
    ClaimKind.PARTICIPANT: PARTICIPANT_USED,
    ClaimKind.RECEIPT: RECEIPT_USED,
    ClaimKind.IMAGE: RECEIPT_USED,
}


@contextmanager
def _timed(timings: dict[str, int], key: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[key] = round((time.perf_counter() - started) * 1000)


def _unique(rejections: list[Rejection]) -> list[Rejection]:
    seen: dict[str, Rejection] = {}
    for rejection in rejections:
        seen.setdefault(rejection.code, rejection)
    return list(seen.values())


class CashbackPipeline:
    def __init__(
        self,
        reader: ReceiptReader | None,
        interpreter: ReceiptInterpreter,
        storage: Storage,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._reader = reader
        self._interpreter = interpreter
        self._storage = storage
        self._now = now

    def process(
        self,
        email: str,
        upload: bytes,
        promo: Promo,
        on_step: Callable[[Step], None] = lambda step: None,
    ) -> CheckResult:
        email = normalize_email(email)
        upload_hash = image_hash(upload)
        image = prepare_image(upload)
        submission_id = uuid4().hex
        timings: dict[str, int] = {}

        on_step(Step.PRECHECK)
        early = self._early_rejections(email, upload_hash)
        if early:
            submission = self._submission(
                submission_id, email, upload_hash, promo, Decision(rejections=early), None, ReaderKind.SKIPPED, timings
            )
            self._save(submission, image, {}, timings, on_step)
            return CheckResult(submission=self._with_timings(submission, timings), receipt=None, raw=None)

        raw, receipt, reader = self._recognize(image, promo, timings, on_step)

        on_step(Step.RULES)
        fingerprint = receipt_fingerprint(receipt)
        history = History(
            receipt_used=fingerprint is not None and self._storage.is_claimed(ClaimKind.RECEIPT, fingerprint)
        )
        decision = evaluate(receipt, promo, history)

        claims: dict[ClaimKind, str] = {}
        if decision.approved and fingerprint is not None:
            claims = {ClaimKind.PARTICIPANT: email, ClaimKind.RECEIPT: fingerprint, ClaimKind.IMAGE: upload_hash}

        submission = self._submission(submission_id, email, upload_hash, promo, decision, receipt, reader, timings)
        try:
            self._save(submission, image, claims, timings, on_step)
        except AlreadyClaimedError as error:
            decision = Decision(rejections=_unique([CLAIM_REJECTIONS[kind] for kind in error.kinds]))
            submission = self._submission(submission_id, email, upload_hash, promo, decision, receipt, reader, timings)
            self._save(submission, image, {}, timings, on_step)
        return CheckResult(submission=self._with_timings(submission, timings), receipt=receipt, raw=raw)

    def _early_rejections(self, email: str, upload_hash: str) -> list[Rejection]:
        rejections: list[Rejection] = []
        if self._storage.is_claimed(ClaimKind.IMAGE, upload_hash):
            rejections.append(RECEIPT_USED)
        if self._storage.is_claimed(ClaimKind.PARTICIPANT, email):
            rejections.append(PARTICIPANT_USED)
        return rejections

    def _recognize(
        self, image: bytes, promo: Promo, timings: dict[str, int], on_step: Callable[[Step], None]
    ) -> tuple[RawReceipt | None, Receipt, ReaderKind]:
        if self._reader is None:
            on_step(Step.GEMINI_PHOTO)
            with _timed(timings, "gemini_vision"):
                receipt = self._interpreter.from_image(image, JPEG_MIME, promo)
            return None, receipt, ReaderKind.GEMINI_VISION

        on_step(Step.TEXTRACT)
        with _timed(timings, "textract"):
            raw = self._reader.read(image)

        if raw.is_usable():
            on_step(Step.GEMINI_TEXT)
            with _timed(timings, "gemini_text"):
                receipt = self._interpreter.from_text(raw, promo)
            if receipt.is_complete():
                return raw, receipt, ReaderKind.TEXTRACT

        on_step(Step.GEMINI_VISION)
        with _timed(timings, "gemini_vision"):
            receipt = self._interpreter.from_image(image, JPEG_MIME, promo)
        return raw, receipt, ReaderKind.GEMINI_VISION

    def _save(
        self,
        submission: Submission,
        image: bytes,
        claims: dict[ClaimKind, str],
        timings: dict[str, int],
        on_step: Callable[[Step], None],
    ) -> None:
        on_step(Step.SAVE)
        with _timed(timings, "storage"):
            self._storage.save(submission, image, claims)

    @staticmethod
    def _with_timings(submission: Submission, timings: dict[str, int]) -> Submission:
        return submission.model_copy(update={"timings_ms": dict(timings)})

    def _submission(
        self,
        submission_id: str,
        email: str,
        upload_hash: str,
        promo: Promo,
        decision: Decision,
        receipt: Receipt | None,
        reader: ReaderKind,
        timings: dict[str, int],
    ) -> Submission:
        item = receipt.promo_item if receipt else None
        return Submission(
            id=submission_id,
            created_at=self._now(),
            email=email,
            approved=decision.approved,
            refund=decision.refund,
            currency=promo.currency,
            rejections=decision.rejections,
            reader=reader,
            store=receipt.store if receipt else None,
            purchase_date=receipt.purchase_date if receipt else None,
            total=receipt.total if receipt else None,
            matched_item=item.name if item else None,
            match_reason=receipt.match_reason if receipt else None,
            gemini_model=receipt.model if receipt else None,
            image_key=f"receipts/{submission_id}.jpg",
            image_hash=upload_hash,
            fingerprint=receipt_fingerprint(receipt) if receipt else None,
            timings_ms=timings,
        )
