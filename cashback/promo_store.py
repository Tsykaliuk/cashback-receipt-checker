from pathlib import Path

from .models import Promo


def load_promo(path: Path) -> Promo:
    return Promo.model_validate_json(path.read_text(encoding="utf-8"))


def save_promo(path: Path, promo: Promo) -> None:
    path.write_text(promo.model_dump_json(indent=2) + "\n", encoding="utf-8")
