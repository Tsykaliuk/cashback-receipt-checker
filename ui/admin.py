from collections import Counter
from datetime import date
from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from cashback.models import REJECT_LABELS, Promo, Submission, to_money
from cashback.promo_store import save_promo
from cashback.services import Services
from cashback.storage import StorageError
from ui.state import READER_LABELS, STEP_LABELS, current_promo, require_services


def show_stats(submissions: list[Submission]) -> None:
    approved = [submission for submission in submissions if submission.approved]
    paid = sum((submission.refund for submission in approved), Decimal("0.00"))
    currency = approved[0].currency if approved else ""

    total_col, approved_col, rejected_col, paid_col = st.columns(4)
    total_col.metric("Заявок", len(submissions), border=True)
    approved_col.metric("Схвалено", len(approved), border=True)
    rejected_col.metric("Відхилено", len(submissions) - len(approved), border=True)
    paid_col.metric(f"Виплачено, {currency}" if currency else "Виплачено", str(paid), border=True)

    reasons = Counter(REJECT_LABELS[rejection.code] for submission in submissions for rejection in submission.rejections)
    if reasons:
        st.subheader("Причини відмов")
        data = pd.DataFrame({"Причина": list(reasons), "Кількість": list(reasons.values())})
        chart = (
            alt.Chart(data)
            .mark_bar()
            .encode(
                x=alt.X("Кількість:Q", axis=alt.Axis(tickMinStep=1, format="d")),
                y=alt.Y("Причина:N", sort="-x", axis=alt.Axis(title=None, labelLimit=320)),
                tooltip=["Причина", "Кількість"],
            )
            .properties(height=36 * len(reasons) + 40)
        )
        st.altair_chart(chart, width="stretch")


def show_table(submissions: list[Submission]) -> None:
    st.subheader("Заявки")
    rows = [
        {
            "Час": submission.created_at.astimezone().strftime("%d.%m %H:%M:%S"),
            "Email": submission.email,
            "Статус": "✅ схвалено" if submission.approved else "❌ відхилено",
            "Кешбек": float(submission.refund),
            "Магазин": submission.store or "",
            "Дата покупки": f"{submission.purchase_date:%d.%m.%Y}" if submission.purchase_date else "",
            "Акційний товар": submission.matched_item or "",
            "Розпізнавання": READER_LABELS[submission.reader],
            "Модель": submission.gemini_model or "",
            "Причини": "; ".join(REJECT_LABELS[rejection.code] for rejection in submission.rejections),
        }
        for submission in submissions
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={"Кешбек": st.column_config.NumberColumn(format="%.2f")},
    )


def show_timings(submissions: list[Submission]) -> None:
    rows = [
        {"Розпізнавання": READER_LABELS[submission.reader], "Крок": STEP_LABELS.get(key, key), "мс": millis}
        for submission in submissions
        for key, millis in submission.timings_ms.items()
    ]
    if not rows:
        return
    st.subheader("Середній час кроків")
    table = pd.DataFrame(rows).groupby(["Розпізнавання", "Крок"], as_index=False)["мс"].mean().round()
    st.dataframe(table, hide_index=True, width="stretch")


def show_photo(services: Services, submissions: list[Submission]) -> None:
    st.subheader("Фото чека")
    chosen = st.selectbox(
        "Заявка",
        submissions,
        format_func=lambda submission: (
            f"{submission.created_at.astimezone():%d.%m %H:%M} · {submission.email} · "
            f"{'✅' if submission.approved else '❌'}"
        ),
    )
    if chosen is not None:
        st.image(services.storage.image_url(chosen.image_key), width=360)


def edit_promo(services: Services, promo: Promo) -> None:
    st.subheader("Умови акції")
    with st.form("promo"):
        title = st.text_input("Назва акції", promo.title)
        product = st.text_input("Акційний товар (так, як його назвала б людина)", promo.product)
        start_col, end_col = st.columns(2)
        start = start_col.date_input("Початок", promo.start, format="DD.MM.YYYY")
        end = end_col.date_input("Кінець", promo.end, format="DD.MM.YYYY")
        refund_col, currency_col = st.columns(2)
        max_refund = refund_col.number_input("Максимальний кешбек", 0.01, value=float(promo.max_refund), step=0.5)
        currency = currency_col.text_input("Валюта", promo.currency)
        stores = st.text_area("Магазини-учасники (по одному в рядку, порожньо = будь-який)", "\n".join(promo.stores))
        if not st.form_submit_button("Зберегти умови", type="primary"):
            return

    if not isinstance(start, date) or not isinstance(end, date):
        st.error("Вкажи дати початку й кінця акції.")
        return
    try:
        updated = Promo(
            title=title.strip(),
            product=product.strip(),
            start=start,
            end=end,
            max_refund=to_money(max_refund),
            currency=currency.strip().upper(),
            stores=[line.strip() for line in stores.splitlines() if line.strip()],
        )
    except ValidationError as error:
        st.error(f"Умови не збережено: {error.errors()[0]['msg']}")
        return
    save_promo(services.settings.promo_file, updated)
    st.success("Умови акції оновлено.")


services = require_services()
st.title("📊 Панель адміністратора")
st.button("Оновити дані", icon="🔄")

try:
    all_submissions = services.storage.list_submissions()
except StorageError as error:
    st.error(f"Не вдалося завантажити заявки: {error}")
    all_submissions = []

if all_submissions:
    show_stats(all_submissions)
    show_table(all_submissions)
    show_timings(all_submissions)
    show_photo(services, all_submissions)
else:
    st.info("Заявок поки немає.")

edit_promo(services, current_promo(services))
