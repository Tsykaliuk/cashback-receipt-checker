import hashlib
import re

from .models import Receipt, to_money


def _normalize(text: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яіїєґ]", "", (text or "").casefold())


def image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def receipt_fingerprint(receipt: Receipt) -> str | None:
    """Identifies the purchase itself, so a re-photographed receipt maps to the same key as the original file."""
    if not receipt.is_receipt or receipt.purchase_date is None:
        return None
    total = "" if receipt.total is None else str(to_money(receipt.total))
    key = "|".join(
        [
            _normalize(receipt.store),
            receipt.purchase_date.isoformat(),
            total,
            _normalize(receipt.receipt_number),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()
