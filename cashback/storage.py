from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from .models import Submission

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client

SUBMISSION_PREFIX = "SUB#"


class ClaimKind(StrEnum):
    PARTICIPANT = "EMAIL"
    RECEIPT = "RECEIPT"
    IMAGE = "IMAGE"


class AlreadyClaimedError(RuntimeError):
    def __init__(self, kinds: list[ClaimKind]) -> None:
        super().__init__(f"Already claimed: {', '.join(kinds)}")
        self.kinds = kinds


class StorageError(RuntimeError):
    pass


class Storage(Protocol):
    def is_claimed(self, kind: ClaimKind, value: str) -> bool: ...

    def save(self, submission: Submission, image: bytes, claims: dict[ClaimKind, str]) -> None: ...

    def list_submissions(self) -> list[Submission]: ...

    def image_url(self, key: str) -> str: ...


def claim_key(kind: ClaimKind, value: str) -> str:
    return f"{kind}#{value}"


class AwsStorage:
    """One DynamoDB table holds submissions (SUB#id) and uniqueness claims (EMAIL#, RECEIPT#, IMAGE#)."""

    def __init__(self, s3: S3Client, dynamodb: DynamoDBClient, bucket: str, table: str) -> None:
        self._s3 = s3
        self._dynamodb = dynamodb
        self._bucket = bucket
        self._table = table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def is_claimed(self, kind: ClaimKind, value: str) -> bool:
        try:
            response = self._dynamodb.get_item(
                TableName=self._table, Key={"pk": {"S": claim_key(kind, value)}}, ConsistentRead=True
            )
        except ClientError as error:
            raise StorageError(f"DynamoDB: {error}") from error
        return "Item" in response

    def save(self, submission: Submission, image: bytes, claims: dict[ClaimKind, str]) -> None:
        try:
            self._s3.put_object(Bucket=self._bucket, Key=submission.image_key, Body=image, ContentType="image/jpeg")
        except ClientError as error:
            raise StorageError(f"S3: {error}") from error

        record = {"pk": f"{SUBMISSION_PREFIX}{submission.id}", **submission.model_dump(mode="json")}
        submission_put = {"Put": {"TableName": self._table, "Item": self._serialize(record)}}
        claim_kinds = list(claims)
        claim_puts = [
            {
                "Put": {
                    "TableName": self._table,
                    "Item": self._serialize({"pk": claim_key(kind, value), "submission_id": submission.id}),
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            }
            for kind, value in claims.items()
        ]
        try:
            self._dynamodb.transact_write_items(TransactItems=[submission_put, *claim_puts])
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise StorageError(f"DynamoDB: {error}") from error
            reasons = error.response.get("CancellationReasons", [])
            taken = [
                claim_kinds[index - 1]
                for index, reason in enumerate(reasons)
                if index > 0 and reason.get("Code") == "ConditionalCheckFailed"
            ]
            if not taken:
                raise StorageError(f"DynamoDB: {error}") from error
            raise AlreadyClaimedError(taken) from error

    def list_submissions(self) -> list[Submission]:
        submissions: list[Submission] = []
        paginator = self._dynamodb.get_paginator("scan")
        try:
            pages = paginator.paginate(
                TableName=self._table,
                FilterExpression="begins_with(pk, :prefix)",
                ExpressionAttributeValues={":prefix": {"S": SUBMISSION_PREFIX}},
            )
            for page in pages:
                for item in page.get("Items", []):
                    record = {key: self._deserializer.deserialize(value) for key, value in item.items()}
                    submissions.append(Submission.model_validate(record))
        except ClientError as error:
            raise StorageError(f"DynamoDB: {error}") from error
        return sorted(submissions, key=lambda submission: submission.created_at, reverse=True)

    def image_url(self, key: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=600
        )

    def _serialize(self, record: dict[str, object]) -> dict[str, object]:
        return {key: self._serializer.serialize(value) for key, value in record.items()}
