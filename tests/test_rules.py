from datetime import date
from decimal import Decimal

import pytest

from cashback.models import LineItem, Promo, RejectCode
from cashback.rules import History, evaluate
from tests.conftest import make_receipt


def codes(promo: Promo, history: History = History(), **overrides: object) -> list[RejectCode]:
    return [rejection.code for rejection in evaluate(make_receipt(**overrides), promo, history).rejections]


def test_valid_receipt_is_approved_with_item_price(promo: Promo) -> None:
    decision = evaluate(make_receipt(), promo, History())
    assert decision.approved
    assert decision.refund == Decimal("2.49")


def test_refund_is_capped_by_promo_maximum(promo: Promo) -> None:
    receipt = make_receipt(items=[LineItem(name="MILKA XXL 300G", price=Decimal("4.99"))], promo_item_index=0)
    assert evaluate(receipt, promo, History()).refund == Decimal("3.00")


def test_not_a_receipt_reports_only_that(promo: Promo) -> None:
    assert codes(promo, is_receipt=False, purchase_date=None, promo_item_index=None) == [RejectCode.NOT_RECEIPT]


@pytest.mark.parametrize("day", [date(2026, 10, 31), date(2026, 12, 1)])
def test_purchase_outside_period_is_rejected(promo: Promo, day: date) -> None:
    assert codes(promo, purchase_date=day) == [RejectCode.OUTSIDE_PERIOD]


@pytest.mark.parametrize("day", [date(2026, 11, 1), date(2026, 11, 30)])
def test_period_boundaries_are_inclusive(promo: Promo, day: date) -> None:
    assert codes(promo, purchase_date=day) == []


def test_missing_date_is_rejected(promo: Promo) -> None:
    assert codes(promo, purchase_date=None) == [RejectCode.UNREADABLE_DATE]


def test_store_outside_list_is_rejected(promo: Promo) -> None:
    assert codes(promo, store="REWE Markt") == [RejectCode.STORE_NOT_ALLOWED]


def test_store_match_ignores_case(promo: Promo) -> None:
    assert codes(promo, store="LIDL") == []


def test_unknown_store_is_rejected_when_list_is_set(promo: Promo) -> None:
    assert codes(promo, store=None) == [RejectCode.STORE_NOT_ALLOWED]


def test_any_store_allowed_when_list_is_empty(promo: Promo) -> None:
    open_promo = promo.model_copy(update={"stores": []})
    assert codes(open_promo, store="REWE Markt") == []


def test_missing_promo_item_is_rejected(promo: Promo) -> None:
    assert codes(promo, promo_item_index=None) == [RejectCode.NO_PROMO_ITEM]


def test_history_rejections_are_reported(promo: Promo) -> None:
    history = History(receipt_used=True, participant_used=True)
    assert codes(promo, history) == [RejectCode.RECEIPT_USED, RejectCode.PARTICIPANT_USED]


def test_all_failures_are_collected(promo: Promo) -> None:
    result = codes(promo, purchase_date=date(2026, 1, 1), store="REWE", promo_item_index=None)
    assert result == [RejectCode.OUTSIDE_PERIOD, RejectCode.STORE_NOT_ALLOWED, RejectCode.NO_PROMO_ITEM]


def test_rejected_decision_has_zero_refund(promo: Promo) -> None:
    assert evaluate(make_receipt(promo_item_index=None), promo, History()).refund == Decimal("0.00")


def test_promo_end_before_start_is_invalid() -> None:
    with pytest.raises(ValueError):
        Promo(title="t", product="p", start=date(2026, 2, 1), end=date(2026, 1, 1), max_refund=Decimal("1"))
