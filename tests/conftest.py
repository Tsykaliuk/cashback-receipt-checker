import io
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from cashback.models import LineItem, Promo, RawLineItem, RawReceipt, Receipt, Submission
from cashback.storage import AlreadyClaimedError, ClaimKind, claim_key


@pytest.fixture
def promo() -> Promo:
    return Promo(
        title="Milka повертає гроші",
        product="Milka Alpenmilch молочний шоколад 100 г",
        start=date(2026, 11, 1),
        end=date(2026, 11, 30),
        max_refund=Decimal("3.00"),
        currency="EUR",
        stores=["Lidl", "Aldi"],
    )


def make_receipt(**overrides: object) -> Receipt:
    data: dict[str, object] = {
        "is_receipt": True,
        "store": "Lidl Dienstleistung GmbH",
        "purchase_date": date(2026, 11, 3),
        "total": Decimal("23.40"),
        "currency": "EUR",
        "receipt_number": "4711-0815",
        "items": [
            LineItem(name="BANANEN", price=Decimal("1.29")),
            LineItem(name="MILKA ALPMI 100G", price=Decimal("2.49")),
        ],
        "promo_item_index": 1,
        "match_reason": "MILKA ALPMI 100G це Milka Alpenmilch 100 г.",
    }
    data.update(overrides)
    return Receipt.model_validate(data)


def make_image(color: str = "white", size: tuple[int, int] = (400, 800), fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


USABLE_RAW = RawReceipt(line_items=[RawLineItem(fields={"ITEM": "MILKA ALPMI 100G", "PRICE": "2,49"})])


class MemoryStorage:
    def __init__(self) -> None:
        self.claims: dict[str, str] = {}
        self.submissions: dict[str, Submission] = {}
        self.images: dict[str, bytes] = {}

    def is_claimed(self, kind: ClaimKind, value: str) -> bool:
        return claim_key(kind, value) in self.claims

    def save(self, submission: Submission, image: bytes, claims: dict[ClaimKind, str]) -> None:
        taken = [kind for kind, value in claims.items() if claim_key(kind, value) in self.claims]
        if taken:
            raise AlreadyClaimedError(taken)
        self.submissions[submission.id] = submission
        self.images[submission.image_key] = image
        for kind, value in claims.items():
            self.claims[claim_key(kind, value)] = submission.id

    def list_submissions(self) -> list[Submission]:
        return list(self.submissions.values())

    def image_url(self, key: str) -> str:
        return f"memory://{key}"


class FakeReader:
    def __init__(self, raw: RawReceipt) -> None:
        self.raw = raw
        self.calls = 0

    def read(self, image: bytes) -> RawReceipt:
        self.calls += 1
        return self.raw


class FakeInterpreter:
    def __init__(self, text_result: Receipt | None = None, image_result: Receipt | None = None) -> None:
        self.text_result = text_result
        self.image_result = image_result
        self.text_calls = 0
        self.image_calls = 0

    def from_text(self, raw: RawReceipt, promo: Promo) -> Receipt:
        self.text_calls += 1
        if self.text_result is None:
            raise AssertionError("from_text was not expected")
        return self.text_result

    def from_image(self, image: bytes, mime_type: str, promo: Promo) -> Receipt:
        self.image_calls += 1
        if self.image_result is None:
            raise AssertionError("from_image was not expected")
        return self.image_result
