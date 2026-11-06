import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2000
JPEG_MIME = "image/jpeg"


class InvalidImageError(ValueError):
    pass


def prepare_image(data: bytes) -> bytes:
    """Re-encodes the upload as an upright JPEG under Textract's 10 MB sync limit; phone photos are often 4000px+ with EXIF rotation."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError) as error:
        raise InvalidImageError("Файл не схожий на зображення (підтримуються JPG і PNG).") from error

    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
