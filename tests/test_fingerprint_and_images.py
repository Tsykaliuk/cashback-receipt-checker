import io
from decimal import Decimal

import pytest
from PIL import Image

from cashback.fingerprint import normalize_email, receipt_fingerprint
from cashback.images import MAX_SIDE, InvalidImageError, prepare_image
from tests.conftest import make_image, make_receipt


def test_fingerprint_ignores_formatting_differences() -> None:
    original = make_receipt(store="Lidl Dienstleistung GmbH", receipt_number="4711-0815", total=Decimal("23.4"))
    reshot = make_receipt(store="LIDL DIENSTLEISTUNG GMBH", receipt_number="4711 0815", total=Decimal("23.40"))
    assert receipt_fingerprint(original) == receipt_fingerprint(reshot)


def test_fingerprint_changes_with_total() -> None:
    assert receipt_fingerprint(make_receipt()) != receipt_fingerprint(make_receipt(total=Decimal("23.41")))


def test_no_fingerprint_without_date() -> None:
    assert receipt_fingerprint(make_receipt(purchase_date=None)) is None


def test_email_is_normalized() -> None:
    assert normalize_email("  Ivan.Petrenko@Gmail.COM ") == "ivan.petrenko@gmail.com"


def test_large_png_becomes_bounded_jpeg() -> None:
    prepared = prepare_image(make_image(size=(3000, 6000)))
    with Image.open(io.BytesIO(prepared)) as image:
        assert image.format == "JPEG"
        assert max(image.size) == MAX_SIDE


def test_exif_rotation_is_applied() -> None:
    buffer = io.BytesIO()
    image = Image.new("RGB", (800, 400), "white")
    exif = image.getexif()
    exif[0x0112] = 6
    image.save(buffer, format="JPEG", exif=exif)
    with Image.open(io.BytesIO(prepare_image(buffer.getvalue()))) as prepared:
        assert prepared.size == (400, 800)


def test_non_image_is_rejected() -> None:
    with pytest.raises(InvalidImageError):
        prepare_image(b"definitely not an image")
