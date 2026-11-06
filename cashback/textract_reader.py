from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, Field

from .models import RawField, RawLineItem, RawReceipt

UNREADABLE_DOCUMENT_ERRORS = {"UnsupportedDocumentException", "BadDocumentException", "InvalidParameterException"}


class ReaderError(RuntimeError):
    pass


class TextractClient(Protocol):
    def analyze_expense(self, *, Document: dict[str, bytes]) -> dict[str, object]: ...


class _Detection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    text: str = Field(default="", alias="Text")
    confidence: float = Field(default=0.0, alias="Confidence")


class _ExpenseField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    type: _Detection = Field(default_factory=_Detection, alias="Type")
    label: _Detection | None = Field(default=None, alias="LabelDetection")
    value: _Detection = Field(default_factory=_Detection, alias="ValueDetection")


class _LineItem(BaseModel):
    fields: list[_ExpenseField] = Field(default_factory=list, alias="LineItemExpenseFields")


class _LineItemGroup(BaseModel):
    line_items: list[_LineItem] = Field(default_factory=list, alias="LineItems")


class _ExpenseDocument(BaseModel):
    summary_fields: list[_ExpenseField] = Field(default_factory=list, alias="SummaryFields")
    line_item_groups: list[_LineItemGroup] = Field(default_factory=list, alias="LineItemGroups")


class _AnalyzeExpenseResponse(BaseModel):
    documents: list[_ExpenseDocument] = Field(default_factory=list, alias="ExpenseDocuments")


def parse_analyze_expense(response: dict[str, object]) -> RawReceipt:
    parsed = _AnalyzeExpenseResponse.model_validate(response)
    summary: list[RawField] = []
    line_items: list[RawLineItem] = []
    for document in parsed.documents:
        summary.extend(
            RawField(
                kind=field.type.text,
                label=field.label.text if field.label and field.label.text else None,
                value=field.value.text,
                confidence=field.value.confidence,
            )
            for field in document.summary_fields
            if field.value.text
        )
        for group in document.line_item_groups:
            for item in group.line_items:
                fields = {
                    field.type.text: field.value.text
                    for field in item.fields
                    if field.value.text and field.type.text != "EXPENSE_ROW"
                }
                if fields:
                    line_items.append(RawLineItem(fields=fields))
    return RawReceipt(summary=summary, line_items=line_items)


class TextractReader:
    def __init__(self, client: TextractClient) -> None:
        self._client = client

    def read(self, image: bytes) -> RawReceipt:
        try:
            response = self._client.analyze_expense(Document={"Bytes": image})
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in UNREADABLE_DOCUMENT_ERRORS:
                return RawReceipt()
            raise ReaderError(f"AWS Textract повернув помилку: {error}") from error
        except BotoCoreError as error:
            raise ReaderError(f"Не вдалося звернутися до AWS Textract: {error}") from error
        return parse_analyze_expense(response)
