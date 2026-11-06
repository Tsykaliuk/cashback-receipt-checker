from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from cashback.aws_setup import ensure_bucket, ensure_table
from cashback.models import ReaderKind, Rejection, RejectCode, Submission
from cashback.storage import AlreadyClaimedError, AwsStorage, ClaimKind

REGION = "eu-central-1"
BUCKET = "cashback-test-bucket"
TABLE = "cashback-test"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[boto3.Session]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SECURITY_TOKEN", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield boto3.Session(region_name=REGION)


@pytest.fixture
def storage(aws: boto3.Session) -> AwsStorage:
    s3 = aws.client("s3")
    dynamodb = aws.client("dynamodb")
    ensure_bucket(s3, BUCKET, REGION)
    ensure_table(dynamodb, TABLE)
    return AwsStorage(s3=s3, dynamodb=dynamodb, bucket=BUCKET, table=TABLE)


def submission(submission_id: str, approved: bool = True, minute: int = 0) -> Submission:
    return Submission(
        id=submission_id,
        created_at=datetime(2026, 11, 6, 12, minute, tzinfo=UTC),
        email="ivan@example.com",
        approved=approved,
        refund=Decimal("2.49") if approved else Decimal("0.00"),
        currency="EUR",
        rejections=[] if approved else [Rejection(code=RejectCode.NO_PROMO_ITEM, message="Немає Milka")],
        reader=ReaderKind.TEXTRACT,
        store="Lidl",
        total=Decimal("23.40"),
        matched_item="MILKA ALPMI 100G",
        image_key=f"receipts/{submission_id}.jpg",
        image_hash="hash-" + submission_id,
        fingerprint="fp-1",
        timings_ms={"textract": 1200, "gemini_text": 2300},
    )


def test_setup_is_idempotent(aws: boto3.Session) -> None:
    s3 = aws.client("s3")
    dynamodb = aws.client("dynamodb")
    assert ensure_bucket(s3, BUCKET, REGION) is True
    assert ensure_bucket(s3, BUCKET, REGION) is False
    assert ensure_table(dynamodb, TABLE) is True
    assert ensure_table(dynamodb, TABLE) is False


def test_submission_roundtrip_and_image_upload(storage: AwsStorage, aws: boto3.Session) -> None:
    saved = submission("a1")
    storage.save(saved, b"jpeg-bytes", {ClaimKind.PARTICIPANT: "ivan@example.com", ClaimKind.RECEIPT: "fp-1"})

    assert storage.list_submissions() == [saved]
    assert storage.is_claimed(ClaimKind.PARTICIPANT, "ivan@example.com")
    assert not storage.is_claimed(ClaimKind.IMAGE, "hash-a1")
    body = aws.client("s3").get_object(Bucket=BUCKET, Key="receipts/a1.jpg")["Body"].read()
    assert body == b"jpeg-bytes"


def test_conflicting_claim_cancels_whole_transaction(storage: AwsStorage) -> None:
    storage.save(submission("a1"), b"x", {ClaimKind.PARTICIPANT: "ivan@example.com", ClaimKind.RECEIPT: "fp-1"})

    with pytest.raises(AlreadyClaimedError) as raised:
        storage.save(
            submission("a2"),
            b"y",
            {ClaimKind.PARTICIPANT: "other@example.com", ClaimKind.RECEIPT: "fp-1", ClaimKind.IMAGE: "hash-a2"},
        )

    assert raised.value.kinds == [ClaimKind.RECEIPT]
    assert [saved.id for saved in storage.list_submissions()] == ["a1"]
    assert not storage.is_claimed(ClaimKind.PARTICIPANT, "other@example.com")


def test_rejected_submissions_are_listed_newest_first(storage: AwsStorage) -> None:
    storage.save(submission("old", approved=False, minute=1), b"x", {})
    storage.save(submission("new", approved=False, minute=5), b"y", {})

    assert [saved.id for saved in storage.list_submissions()] == ["new", "old"]


def test_image_url_points_to_bucket(storage: AwsStorage) -> None:
    assert BUCKET in storage.image_url("receipts/a1.jpg")
