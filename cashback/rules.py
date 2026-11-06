from dataclasses import dataclass

from .models import Decision, Promo, Receipt, Rejection, RejectCode, to_money


RECEIPT_USED = Rejection(code=RejectCode.RECEIPT_USED, message="Цей чек уже брав участь в акції.")
PARTICIPANT_USED = Rejection(code=RejectCode.PARTICIPANT_USED, message="Ви вже отримали кешбек за цією акцією.")


@dataclass(frozen=True)
class History:
    receipt_used: bool = False
    participant_used: bool = False


def _store_allowed(store: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    if not store:
        return False
    name = store.casefold()
    return any(entry.casefold() in name or name in entry.casefold() for entry in allowed)


def evaluate(receipt: Receipt, promo: Promo, history: History) -> Decision:
    if not receipt.is_receipt:
        return Decision(rejections=[Rejection(code=RejectCode.NOT_RECEIPT, message="На фото не видно касового чека.")])

    rejections: list[Rejection] = []

    if receipt.purchase_date is None:
        rejections.append(Rejection(code=RejectCode.UNREADABLE_DATE, message="Не вдалося прочитати дату покупки."))
    elif not promo.start <= receipt.purchase_date <= promo.end:
        rejections.append(
            Rejection(
                code=RejectCode.OUTSIDE_PERIOD,
                message=(
                    f"Покупка {receipt.purchase_date:%d.%m.%Y} не входить у період акції "
                    f"({promo.start:%d.%m.%Y} – {promo.end:%d.%m.%Y})."
                ),
            )
        )

    if not _store_allowed(receipt.store, promo.stores):
        rejections.append(
            Rejection(
                code=RejectCode.STORE_NOT_ALLOWED,
                message=f"Магазин «{receipt.store or 'невідомий'}» не бере участі в акції.",
            )
        )

    item = receipt.promo_item
    if item is None:
        rejections.append(
            Rejection(code=RejectCode.NO_PROMO_ITEM, message=f"У чеку не знайдено акційного товару: {promo.product}.")
        )

    if history.receipt_used:
        rejections.append(RECEIPT_USED)

    if history.participant_used:
        rejections.append(PARTICIPANT_USED)

    if rejections or item is None:
        return Decision(rejections=rejections)
    return Decision(refund=to_money(min(item.price, promo.max_refund)))
