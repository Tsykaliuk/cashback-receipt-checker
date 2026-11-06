from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

CENT = Decimal("0.01")


def to_money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


class Promo(BaseModel):
    title: str
    product: str
    start: date
    end: date
    max_refund: Decimal = Field(gt=0)
    currency: str = "EUR"
    stores: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_period(self) -> "Promo":
        if self.end < self.start:
            raise ValueError("Дата завершення акції раніша за дату початку")
        return self


class RawField(BaseModel):
    kind: str
    label: str | None = None
    value: str
    confidence: float


class RawLineItem(BaseModel):
    fields: dict[str, str]


class RawReceipt(BaseModel):
    summary: list[RawField] = Field(default_factory=list)
    line_items: list[RawLineItem] = Field(default_factory=list)

    def is_usable(self) -> bool:
        return any(item.fields.get("ITEM") for item in self.line_items)

    def to_text(self) -> str:
        lines = ["Summary fields:"]
        for field in self.summary:
            name = field.label if field.kind == "OTHER" and field.label else field.kind
            lines.append(f"- {name}: {field.value} (confidence {field.confidence:.0f}%)")
        lines.append("Line items:")
        for number, item in enumerate(self.line_items):
            parts = "; ".join(f"{key}={value}" for key, value in item.fields.items())
            lines.append(f"{number}. {parts}")
        return "\n".join(lines)


class LineItem(BaseModel):
    name: str
    price: Decimal


class Receipt(BaseModel):
    is_receipt: bool
    store: str | None = None
    purchase_date: date | None = None
    total: Decimal | None = None
    currency: str | None = None
    receipt_number: str | None = None
    items: list[LineItem] = Field(default_factory=list)
    promo_item_index: int | None = None
    match_reason: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def _check_index(self) -> "Receipt":
        index = self.promo_item_index
        if index is not None and not 0 <= index < len(self.items):
            raise ValueError(f"promo_item_index {index} поза межами списку товарів")
        return self

    def is_complete(self) -> bool:
        return self.is_receipt and self.purchase_date is not None and bool(self.items)

    @property
    def promo_item(self) -> LineItem | None:
        return None if self.promo_item_index is None else self.items[self.promo_item_index]


class ReaderKind(StrEnum):
    TEXTRACT = "textract"
    GEMINI_VISION = "gemini-vision"
    SKIPPED = "skipped"


class RejectCode(StrEnum):
    NOT_RECEIPT = "not_receipt"
    UNREADABLE_DATE = "unreadable_date"
    OUTSIDE_PERIOD = "outside_period"
    STORE_NOT_ALLOWED = "store_not_allowed"
    NO_PROMO_ITEM = "no_promo_item"
    RECEIPT_USED = "receipt_used"
    PARTICIPANT_USED = "participant_used"


REJECT_LABELS: dict[RejectCode, str] = {
    RejectCode.NOT_RECEIPT: "Не чек",
    RejectCode.UNREADABLE_DATE: "Не прочитано дату",
    RejectCode.OUTSIDE_PERIOD: "Поза періодом акції",
    RejectCode.STORE_NOT_ALLOWED: "Магазин не бере участі",
    RejectCode.NO_PROMO_ITEM: "Немає акційного товару",
    RejectCode.RECEIPT_USED: "Чек уже використано",
    RejectCode.PARTICIPANT_USED: "Учасник уже отримав кешбек",
}


class Rejection(BaseModel):
    code: RejectCode
    message: str


class Decision(BaseModel):
    rejections: list[Rejection] = Field(default_factory=list)
    refund: Decimal = Decimal("0.00")

    @property
    def approved(self) -> bool:
        return not self.rejections


class Submission(BaseModel):
    id: str
    created_at: datetime
    email: str
    approved: bool
    refund: Decimal
    currency: str
    rejections: list[Rejection] = Field(default_factory=list)
    reader: ReaderKind
    store: str | None = None
    purchase_date: date | None = None
    total: Decimal | None = None
    matched_item: str | None = None
    match_reason: str | None = None
    gemini_model: str | None = None
    image_key: str
    image_hash: str
    fingerprint: str | None = None
    timings_ms: dict[str, int] = Field(default_factory=dict)
