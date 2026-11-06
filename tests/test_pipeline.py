from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cashback.images import InvalidImageError
from cashback.models import Promo, RawReceipt, ReaderKind, Receipt, RejectCode, Submission
from cashback.pipeline import CashbackPipeline, Step
from cashback.storage import AlreadyClaimedError, ClaimKind
from tests.conftest import USABLE_RAW, FakeInterpreter, FakeReader, MemoryStorage, make_image, make_receipt

EMAIL = "Ivan@Example.com"


def build(
    raw: RawReceipt = USABLE_RAW,
    text_result: Receipt | None = None,
    image_result: Receipt | None = None,
    storage: MemoryStorage | None = None,
) -> tuple[CashbackPipeline, FakeReader, FakeInterpreter, MemoryStorage]:
    reader = FakeReader(raw)
    interpreter = FakeInterpreter(text_result, image_result)
    store = storage or MemoryStorage()
    pipeline = CashbackPipeline(reader, interpreter, store, now=lambda: datetime(2026, 11, 6, tzinfo=UTC))
    return pipeline, reader, interpreter, store


def codes(submission: Submission) -> list[RejectCode]:
    return [rejection.code for rejection in submission.rejections]


def test_textract_path_approves_and_claims(promo: Promo) -> None:
    pipeline, _, interpreter, storage = build(text_result=make_receipt())
    result = pipeline.process(EMAIL, make_image(), promo)

    assert result.submission.approved
    assert result.submission.refund == Decimal("2.49")
    assert result.submission.reader == ReaderKind.TEXTRACT
    assert result.submission.email == "ivan@example.com"
    assert result.submission.matched_item == "MILKA ALPMI 100G"
    assert interpreter.image_calls == 0
    assert storage.is_claimed(ClaimKind.PARTICIPANT, "ivan@example.com")
    assert result.submission.image_key in storage.images


def test_unusable_textract_result_falls_back_to_gemini_vision(promo: Promo) -> None:
    pipeline, _, interpreter, _ = build(raw=RawReceipt(), image_result=make_receipt())
    result = pipeline.process(EMAIL, make_image(), promo)

    assert result.submission.reader == ReaderKind.GEMINI_VISION
    assert result.submission.approved
    assert interpreter.text_calls == 0


def test_incomplete_text_interpretation_falls_back_to_vision(promo: Promo) -> None:
    pipeline, _, interpreter, _ = build(
        text_result=make_receipt(purchase_date=None), image_result=make_receipt()
    )
    result = pipeline.process(EMAIL, make_image(), promo)

    assert result.submission.reader == ReaderKind.GEMINI_VISION
    assert interpreter.text_calls == 1
    assert interpreter.image_calls == 1


def test_same_file_twice_is_rejected_without_calling_apis(promo: Promo) -> None:
    pipeline, reader, _, storage = build(text_result=make_receipt())
    image = make_image()
    pipeline.process(EMAIL, image, promo)
    second = pipeline.process("other@example.com", image, promo)

    assert codes(second.submission) == [RejectCode.RECEIPT_USED]
    assert second.submission.reader == ReaderKind.SKIPPED
    assert reader.calls == 1
    assert len(storage.submissions) == 2


def test_same_participant_with_new_receipt_is_rejected(promo: Promo) -> None:
    pipeline, *_ = build(text_result=make_receipt())
    pipeline.process(EMAIL, make_image("white"), promo)
    second = pipeline.process(" ivan@example.COM", make_image("gray"), promo)

    assert codes(second.submission) == [RejectCode.PARTICIPANT_USED]


def test_new_photo_of_used_receipt_is_caught_by_fingerprint(promo: Promo) -> None:
    pipeline, reader, *_ = build(text_result=make_receipt())
    pipeline.process(EMAIL, make_image("white"), promo)
    second = pipeline.process("other@example.com", make_image("gray"), promo)

    assert codes(second.submission) == [RejectCode.RECEIPT_USED]
    assert reader.calls == 2


def test_lost_race_is_saved_as_rejection(promo: Promo) -> None:
    class RacingStorage(MemoryStorage):
        def save(self, submission: Submission, image: bytes, claims: dict[ClaimKind, str]) -> None:
            if claims:
                raise AlreadyClaimedError([ClaimKind.RECEIPT, ClaimKind.IMAGE])
            super().save(submission, image, claims)

    pipeline, *_, storage = build(text_result=make_receipt(), storage=RacingStorage())
    result = pipeline.process(EMAIL, make_image(), promo)

    stored = storage.submissions[result.submission.id]
    assert codes(result.submission) == [RejectCode.RECEIPT_USED]
    assert result.submission.refund == Decimal("0.00")
    assert not stored.approved
    assert codes(stored) == [RejectCode.RECEIPT_USED]


def test_rejected_submission_claims_nothing(promo: Promo) -> None:
    pipeline, *_, storage = build(text_result=make_receipt(promo_item_index=None))
    result = pipeline.process(EMAIL, make_image(), promo)

    assert codes(result.submission) == [RejectCode.NO_PROMO_ITEM]
    assert storage.claims == {}


def test_steps_are_reported_in_order(promo: Promo) -> None:
    pipeline, *_ = build(text_result=make_receipt())
    steps: list[Step] = []
    result = pipeline.process(EMAIL, make_image(), promo, on_step=steps.append)

    assert steps == [Step.PRECHECK, Step.TEXTRACT, Step.GEMINI_TEXT, Step.RULES, Step.SAVE]
    assert set(result.submission.timings_ms) == {"textract", "gemini_text", "storage"}


def test_invalid_upload_stops_before_anything_is_saved(promo: Promo) -> None:
    pipeline, reader, _, storage = build(text_result=make_receipt())
    with pytest.raises(InvalidImageError):
        pipeline.process(EMAIL, b"not an image", promo)
    assert reader.calls == 0
    assert storage.submissions == {}


def test_without_textract_gemini_reads_photo_directly(promo: Promo) -> None:
    interpreter = FakeInterpreter(image_result=make_receipt())
    pipeline = CashbackPipeline(None, interpreter, MemoryStorage())
    steps: list[Step] = []
    result = pipeline.process(EMAIL, make_image(), promo, on_step=steps.append)

    assert result.submission.approved
    assert result.submission.reader == ReaderKind.GEMINI_VISION
    assert result.raw is None
    assert interpreter.text_calls == 0
    assert steps == [Step.PRECHECK, Step.GEMINI_PHOTO, Step.RULES, Step.SAVE]
