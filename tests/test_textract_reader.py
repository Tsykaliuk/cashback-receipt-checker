import pytest
from botocore.exceptions import ClientError

from cashback.textract_reader import ReaderError, TextractReader, parse_analyze_expense

SAMPLE_RESPONSE: dict[str, object] = {
    "DocumentMetadata": {"Pages": 1},
    "ExpenseDocuments": [
        {
            "ExpenseIndex": 1,
            "SummaryFields": [
                {
                    "Type": {"Text": "VENDOR_NAME", "Confidence": 99.1},
                    "ValueDetection": {"Text": "LIDL", "Confidence": 98.7},
                },
                {
                    "Type": {"Text": "INVOICE_RECEIPT_DATE", "Confidence": 97.0},
                    "LabelDetection": {"Text": "Datum", "Confidence": 90.0},
                    "ValueDetection": {"Text": "03.11.26", "Confidence": 96.4},
                },
                {
                    "Type": {"Text": "OTHER", "Confidence": 80.0},
                    "LabelDetection": {"Text": "Beleg-Nr.", "Confidence": 88.0},
                    "ValueDetection": {"Text": "4711-0815", "Confidence": 91.2},
                },
                {"Type": {"Text": "TAX", "Confidence": 50.0}, "ValueDetection": {"Text": "", "Confidence": 0.0}},
            ],
            "LineItemGroups": [
                {
                    "LineItemGroupIndex": 1,
                    "LineItems": [
                        {
                            "LineItemExpenseFields": [
                                {"Type": {"Text": "ITEM"}, "ValueDetection": {"Text": "MILKA ALPMI 100G"}},
                                {"Type": {"Text": "PRICE"}, "ValueDetection": {"Text": "2,49"}},
                                {"Type": {"Text": "EXPENSE_ROW"}, "ValueDetection": {"Text": "MILKA ALPMI 100G 2,49 A"}},
                            ]
                        },
                        {"LineItemExpenseFields": [{"Type": {"Text": "EXPENSE_ROW"}, "ValueDetection": {"Text": ""}}]},
                    ],
                }
            ],
        }
    ],
}


class StubTextract:
    def __init__(self, response: dict[str, object] | None = None, error: ClientError | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.received: bytes | None = None

    def analyze_expense(self, *, Document: dict[str, bytes]) -> dict[str, object]:
        self.received = Document["Bytes"]
        if self.error:
            raise self.error
        return self.response


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "AnalyzeExpense")


def test_parses_summary_and_line_items() -> None:
    raw = parse_analyze_expense(SAMPLE_RESPONSE)
    assert [(field.kind, field.value) for field in raw.summary] == [
        ("VENDOR_NAME", "LIDL"),
        ("INVOICE_RECEIPT_DATE", "03.11.26"),
        ("OTHER", "4711-0815"),
    ]
    assert [item.fields for item in raw.line_items] == [{"ITEM": "MILKA ALPMI 100G", "PRICE": "2,49"}]
    assert raw.is_usable()


def test_text_for_gemini_uses_labels_of_other_fields() -> None:
    text = parse_analyze_expense(SAMPLE_RESPONSE).to_text()
    assert "- Beleg-Nr.: 4711-0815" in text
    assert "0. ITEM=MILKA ALPMI 100G; PRICE=2,49" in text


def test_empty_response_is_not_usable() -> None:
    assert not parse_analyze_expense({"ExpenseDocuments": []}).is_usable()


def test_reader_sends_image_bytes() -> None:
    stub = StubTextract(SAMPLE_RESPONSE)
    TextractReader(stub).read(b"jpeg-bytes")
    assert stub.received == b"jpeg-bytes"


def test_unsupported_document_gives_empty_result() -> None:
    raw = TextractReader(StubTextract(error=client_error("UnsupportedDocumentException"))).read(b"x")
    assert not raw.is_usable()


def test_access_problems_raise_reader_error() -> None:
    with pytest.raises(ReaderError):
        TextractReader(StubTextract(error=client_error("AccessDeniedException"))).read(b"x")
