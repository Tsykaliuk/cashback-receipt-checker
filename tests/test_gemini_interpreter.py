import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import httpx
import pytest
from google.genai import errors, types

from cashback.gemini_interpreter import GeminiInterpreter, GeminiReceipt, InterpretationError
from cashback.models import Promo
from tests.conftest import USABLE_RAW

ANSWER: dict[str, object] = {
    "is_receipt": True,
    "store": "Lidl",
    "purchase_date": "2026-11-03",
    "total": 23.4,
    "currency": "EUR",
    "receipt_number": "4711-0815",
    "items": [{"name": "BANANEN", "price": 1.29}, {"name": "MILKA ALPMI 100G", "price": 2.49}],
    "promo_item_index": 1,
    "match_reason": "Це Milka Alpenmilch.",
}


@dataclass
class StubResponse:
    text: str | None


Outcome = str | None | errors.APIError | httpx.TransportError


@dataclass
class StubModels:
    outcomes: dict[str, Outcome]
    calls: list[dict[str, object]] = field(default_factory=list)

    def generate_content(
        self, *, model: str, contents: list[types.Part | str], config: types.GenerateContentConfig
    ) -> StubResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes[model]
        if isinstance(outcome, (errors.APIError, httpx.TransportError)):
            raise outcome
        return StubResponse(outcome)


@dataclass
class StubClient:
    models: StubModels


def interpreter(reply: str | None = None, error: errors.APIError | None = None) -> tuple[GeminiInterpreter, StubModels]:
    models = StubModels(outcomes={"gemini-test": error or reply})
    return GeminiInterpreter(StubClient(models), ["gemini-test"]), models


def api_error(code: int, status: str) -> errors.APIError:
    error_type = errors.ClientError if code < 500 else errors.ServerError
    return error_type(code, {"error": {"code": code, "message": status, "status": status}})


def answer(**overrides: object) -> str:
    return json.dumps({**ANSWER, **overrides})


def test_converts_answer_to_receipt(promo: Promo) -> None:
    receipt = interpreter(answer())[0].from_text(USABLE_RAW, promo)
    assert receipt.purchase_date == date(2026, 11, 3)
    assert receipt.total == Decimal("23.40")
    assert receipt.promo_item is not None
    assert receipt.promo_item.price == Decimal("2.49")


def test_text_request_contains_textract_output_and_promo(promo: Promo) -> None:
    gemini, models = interpreter(answer())
    gemini.from_text(USABLE_RAW, promo)
    call = models.calls[0]
    config = call["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert promo.product in str(config.system_instruction)
    assert config.response_schema is GeminiReceipt
    assert "MILKA ALPMI 100G" in str(call["contents"])
    assert call["model"] == "gemini-test"


def test_image_request_sends_image_part(promo: Promo) -> None:
    gemini, models = interpreter(answer())
    gemini.from_image(b"jpeg", "image/jpeg", promo)
    contents = models.calls[0]["contents"]
    assert isinstance(contents, list)
    part = contents[0]
    assert isinstance(part, types.Part)
    assert part.inline_data is not None
    assert part.inline_data.data == b"jpeg"


def test_non_receipt_drops_other_fields(promo: Promo) -> None:
    receipt = interpreter(answer(is_receipt=False))[0].from_text(USABLE_RAW, promo)
    assert not receipt.is_receipt
    assert receipt.items == []


def test_unparseable_date_becomes_none(promo: Promo) -> None:
    receipt = interpreter(answer(purchase_date="03.11.2026"))[0].from_text(USABLE_RAW, promo)
    assert receipt.purchase_date is None
    assert not receipt.is_complete()


def test_out_of_range_index_is_an_error(promo: Promo) -> None:
    with pytest.raises(InterpretationError):
        interpreter(answer(promo_item_index=5))[0].from_text(USABLE_RAW, promo)


def test_malformed_json_is_an_error(promo: Promo) -> None:
    with pytest.raises(InterpretationError):
        interpreter('{"is_receipt": true')[0].from_text(USABLE_RAW, promo)


def test_empty_reply_is_an_error(promo: Promo) -> None:
    with pytest.raises(InterpretationError):
        interpreter(None)[0].from_text(USABLE_RAW, promo)


def test_api_error_is_wrapped(promo: Promo) -> None:
    with pytest.raises(InterpretationError, match="Gemini"):
        interpreter(error=api_error(403, "PERMISSION_DENIED"))[0].from_text(USABLE_RAW, promo)


def test_receipt_remembers_which_model_answered(promo: Promo) -> None:
    assert interpreter(answer())[0].from_text(USABLE_RAW, promo).model == "gemini-test"


@pytest.mark.parametrize(
    "failure",
    [api_error(503, "UNAVAILABLE"), api_error(504, "DEADLINE_EXCEEDED"), api_error(429, "RESOURCE_EXHAUSTED"), httpx.ReadTimeout("slow")],
)
def test_overloaded_model_falls_back_to_next(promo: Promo, failure: Outcome) -> None:
    models = StubModels(outcomes={"main": failure, "backup": answer()})
    receipt = GeminiInterpreter(StubClient(models), ["main", "backup"]).from_text(USABLE_RAW, promo)

    assert receipt.model == "backup"
    assert [call["model"] for call in models.calls] == ["main", "backup"]


def test_permanent_error_does_not_fall_back(promo: Promo) -> None:
    models = StubModels(outcomes={"main": api_error(400, "INVALID_ARGUMENT"), "backup": answer()})
    with pytest.raises(InterpretationError):
        GeminiInterpreter(StubClient(models), ["main", "backup"]).from_text(USABLE_RAW, promo)
    assert len(models.calls) == 1


def test_all_models_overloaded_gives_friendly_error(promo: Promo) -> None:
    models = StubModels(outcomes={"main": api_error(503, "UNAVAILABLE"), "backup": httpx.ConnectError("offline")})
    with pytest.raises(InterpretationError, match="перевантажений"):
        GeminiInterpreter(StubClient(models), ["main", "backup"]).from_text(USABLE_RAW, promo)


def test_at_least_one_model_is_required() -> None:
    with pytest.raises(ValueError):
        GeminiInterpreter(StubClient(StubModels(outcomes={})), [])
