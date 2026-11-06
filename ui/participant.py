import pandas as pd
import streamlit as st
from pydantic import EmailStr, TypeAdapter, ValidationError

from cashback.gemini_interpreter import InterpretationError
from cashback.images import InvalidImageError
from cashback.models import Promo, ReaderKind
from cashback.pipeline import CheckResult, Step
from cashback.storage import StorageError
from cashback.textract_reader import ReaderError
from ui.state import READER_LABELS, STEP_LABELS, current_promo, require_services

EMAIL_ADAPTER = TypeAdapter(EmailStr)
MAX_UPLOAD_MB = 5
PROCESSING_ERRORS = (InvalidImageError, ReaderError, InterpretationError, StorageError)

READER_EXPLANATIONS: dict[ReaderKind, str] = {
    ReaderKind.TEXTRACT: "AWS Textract прочитав чек, а Gemini розібрав текст і знайшов акційний товар.",
    ReaderKind.GEMINI_VISION: (
        "Textract не зміг нормально прочитати чек (він розуміє лише англомовні чеки), "
        "тому Gemini прочитав фото напряму."
    ),
    ReaderKind.SKIPPED: "Заявку відхилено ще до розпізнавання, тож хмарні API не викликались.",
}


def valid_email(value: str) -> bool:
    try:
        EMAIL_ADAPTER.validate_python(value.strip())
    except ValidationError:
        return False
    return True


def show_promo(promo: Promo) -> None:
    st.title(f"🧾 {promo.title}")
    st.markdown(
        f"Купи **{promo.product}** з {promo.start:%d.%m.%Y} по {promo.end:%d.%m.%Y}, завантаж фото чека "
        f"й отримай назад вартість товару, але не більше **{promo.max_refund} {promo.currency}**. "
        "Один раз на людину."
    )
    if promo.stores:
        st.caption("Магазини-учасники: " + ", ".join(promo.stores))


def show_receipt_tab(result: CheckResult) -> None:
    receipt = result.receipt
    if receipt is None:
        st.write("Чек не розпізнавали.")
        return
    if not receipt.is_receipt:
        st.write("Gemini вирішив, що на фото не касовий чек.")
        return

    store, day, total = st.columns(3)
    store.metric("Магазин", receipt.store or "?")
    day.metric("Дата", f"{receipt.purchase_date:%d.%m.%Y}" if receipt.purchase_date else "?")
    total.metric("Сума", f"{receipt.total} {receipt.currency or ''}" if receipt.total is not None else "?")

    rows = [
        {"Товар": item.name, "Ціна": float(item.price), "Акційний": index == receipt.promo_item_index}
        for index, item in enumerate(receipt.items)
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={"Ціна": st.column_config.NumberColumn(format="%.2f")},
    )
    if receipt.match_reason:
        st.info(receipt.match_reason, icon="🤖")


def show_result(result: CheckResult, preview: bytes) -> None:
    submission = result.submission
    if submission.approved:
        st.success(f"Чек прийнято! Повернемо **{submission.refund} {submission.currency}**.", icon="✅")
    else:
        reasons = "\n".join(f"- {rejection.message}" for rejection in submission.rejections)
        st.error(f"Чек не прийнято:\n\n{reasons}", icon="❌")
    if result.raw is None and submission.reader == ReaderKind.GEMINI_VISION:
        st.caption("Gemini прочитав фото чека і знайшов акційний товар (Textract вимкнено).")
    else:
        st.caption(READER_EXPLANATIONS[submission.reader])

    data_tab, raw_tab, time_tab, photo_tab = st.tabs(["Розпізнані дані", "Що прочитав Textract", "Час обробки", "Фото"])
    with data_tab:
        show_receipt_tab(result)
    with raw_tab:
        if result.raw is None:
            st.write("Textract у цьому запуску не використовувався.")
        elif result.raw.summary or result.raw.line_items:
            st.code(result.raw.to_text(), language=None)
        else:
            st.write("Textract нічого корисного не повернув.")
    with time_tab:
        for key, millis in submission.timings_ms.items():
            st.metric(STEP_LABELS.get(key, key), f"{millis / 1000:.1f} с")
        st.caption(f"Шлях розпізнавання: {READER_LABELS[submission.reader]}")
        if submission.gemini_model:
            st.caption(f"Відповіла модель: {submission.gemini_model}")
    with photo_tab:
        st.image(preview, width="stretch")


services = require_services()
promo = current_promo(services)
show_promo(promo)

with st.form("receipt"):
    email = st.text_input("Email", placeholder="you@example.com")
    upload = st.file_uploader("Фото чека", type=["jpg", "jpeg", "png"], max_upload_size=MAX_UPLOAD_MB)
    submitted = st.form_submit_button("Перевірити чек", type="primary", width="stretch")

if submitted:
    if not valid_email(email):
        st.error("Вкажи коректний email.")
    elif upload is None:
        st.error("Додай фото чека.")
    else:
        image = upload.getvalue()
        with st.status("Обробляємо чек…", expanded=True) as status:

            def report(step: Step) -> None:
                status.write(step.value)

            try:
                result = services.pipeline.process(email, image, promo, on_step=report)
            except PROCESSING_ERRORS as error:
                status.update(label="Не вдалося обробити чек", state="error")
                st.error(str(error))
            else:
                status.update(label="Готово", state="complete", expanded=False)
                st.session_state["result"] = result
                st.session_state["preview"] = image
                if result.submission.approved:
                    st.balloons()

last_result = st.session_state.get("result")
last_preview = st.session_state.get("preview")
if isinstance(last_result, CheckResult) and isinstance(last_preview, bytes):
    show_result(last_result, last_preview)
