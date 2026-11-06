import streamlit as st
from pydantic import ValidationError

from cashback.models import Promo, ReaderKind
from cashback.promo_store import load_promo
from cashback.services import Services, build_services
from cashback.settings import Settings, missing_variables

READER_LABELS: dict[ReaderKind, str] = {
    ReaderKind.TEXTRACT: "AWS Textract → Gemini",
    ReaderKind.GEMINI_VISION: "Gemini (фото напряму)",
    ReaderKind.SKIPPED: "Не розпізнавали",
}

STEP_LABELS: dict[str, str] = {
    "textract": "AWS Textract",
    "gemini_text": "Gemini (текст)",
    "gemini_vision": "Gemini (фото)",
    "storage": "S3 + DynamoDB",
}


@st.cache_resource(show_spinner=False)
def _services() -> Services:
    return build_services(Settings())


def require_services() -> Services:
    try:
        return _services()
    except ValidationError as error:
        st.error("Застосунок ще не налаштовано. Заповни у файлі .env: " + ", ".join(missing_variables(error)))
        st.info("Покрокова інструкція є в README.md, розділ «Налаштування».")
        st.stop()


def current_promo(services: Services) -> Promo:
    try:
        return load_promo(services.settings.promo_file)
    except (OSError, ValidationError) as error:
        st.error(f"Не вдалося прочитати умови акції з {services.settings.promo_file.name}: {error}")
        st.stop()
