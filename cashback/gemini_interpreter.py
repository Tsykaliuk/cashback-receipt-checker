from datetime import date
from typing import Protocol

import httpx
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from .models import LineItem, Promo, RawReceipt, Receipt, to_money


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class InterpretationError(RuntimeError):
    pass


class GenerateResponse(Protocol):
    @property
    def text(self) -> str | None: ...


class GenerateModels(Protocol):
    def generate_content(
        self, *, model: str, contents: list[types.Part | str], config: types.GenerateContentConfig
    ) -> GenerateResponse: ...


class GenaiClient(Protocol):
    @property
    def models(self) -> GenerateModels: ...


class GeminiLineItem(BaseModel):
    name: str
    price: float


class GeminiReceipt(BaseModel):
    is_receipt: bool
    store: str | None
    purchase_date: str | None
    total: float | None
    currency: str | None
    receipt_number: str | None
    items: list[GeminiLineItem]
    promo_item_index: int | None
    match_reason: str | None


INSTRUCTIONS = """You process shop receipts for a cashback promotion.

Promotion product: {product}

1. Decide whether the input is a shop/cash-register receipt. If not, set is_receipt=false and leave the other fields empty.
2. Extract the store name, purchase date as YYYY-MM-DD, grand total, ISO 4217 currency code and the receipt/fiscal number.
   European receipts usually write dates as DD.MM.YYYY. Use null for anything you cannot read.
3. List every purchased product line: the name exactly as printed and the final line price after discounts.
   Skip subtotal, tax, payment, change and discount-only lines.
4. Receipt item names are abbreviated (e.g. "MILKA ALPMI 100G" = Milka Alpenmilch chocolate 100 g).
   Set promo_item_index to the 0-based index in your items list of the line that is the promotion product.
   Match only when brand and product type agree; a different brand or product type is not a match. Otherwise null.
5. match_reason: one short sentence in Ukrainian explaining why that line matches, or why nothing matched.
"""

TEXT_PREFIX = "Below is the output of an OCR service (AWS Textract AnalyzeExpense) for the receipt:\n\n"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def to_receipt(answer: GeminiReceipt, model: str) -> Receipt:
    if not answer.is_receipt:
        return Receipt(is_receipt=False, model=model)
    return Receipt(
        is_receipt=True,
        model=model,
        store=answer.store,
        purchase_date=_parse_date(answer.purchase_date),
        total=None if answer.total is None else to_money(answer.total),
        currency=answer.currency,
        receipt_number=answer.receipt_number,
        items=[LineItem(name=item.name, price=to_money(item.price)) for item in answer.items],
        promo_item_index=answer.promo_item_index,
        match_reason=answer.match_reason,
    )


class GeminiInterpreter:
    """Tries models in order; an overloaded or slow model hands the request to the next one."""

    def __init__(self, client: GenaiClient, models: list[str]) -> None:
        if not models:
            raise ValueError("Потрібна хоча б одна модель Gemini")
        self._client = client
        self._models = models

    def from_text(self, raw: RawReceipt, promo: Promo) -> Receipt:
        return self._ask([TEXT_PREFIX + raw.to_text()], promo)

    def from_image(self, image: bytes, mime_type: str, promo: Promo) -> Receipt:
        return self._ask([types.Part.from_bytes(data=image, mime_type=mime_type)], promo)

    def _ask(self, contents: list[types.Part | str], promo: Promo) -> Receipt:
        config = types.GenerateContentConfig(
            system_instruction=INSTRUCTIONS.format(product=promo.product),
            response_mime_type="application/json",
            response_schema=GeminiReceipt,
        )
        failures: list[str] = []
        for model in self._models:
            try:
                response = self._client.models.generate_content(model=model, contents=contents, config=config)
            except errors.APIError as error:
                if error.code not in TRANSIENT_STATUS_CODES:
                    raise InterpretationError(f"Gemini API повернув помилку: {error}") from error
                failures.append(f"{model}: {error.code} {error.status}")
                continue
            except httpx.TransportError as error:
                failures.append(f"{model}: {type(error).__name__}")
                continue
            return self._parse(response, model)

        raise InterpretationError(
            "Gemini зараз перевантажений або недоступний, спробуй ще раз за хвилину. (" + "; ".join(failures) + ")"
        )

    @staticmethod
    def _parse(response: GenerateResponse, model: str) -> Receipt:
        if not response.text:
            raise InterpretationError("Gemini повернув порожню відповідь.")
        try:
            return to_receipt(GeminiReceipt.model_validate_json(response.text), model)
        except ValidationError as error:
            raise InterpretationError(f"Gemini повернув дані в неочікуваному форматі: {error}") from error
