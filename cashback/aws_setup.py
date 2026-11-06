from __future__ import annotations

from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client


class SetupError(RuntimeError):
    pass


def ensure_bucket(s3: S3Client, bucket: str, region: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
        return False
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code == "403":
            raise SetupError(f"Бакет «{bucket}» уже належить іншому акаунту AWS, обери іншу назву.") from error
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return True


def ensure_table(dynamodb: DynamoDBClient, table: str) -> bool:
    try:
        dynamodb.describe_table(TableName=table)
        return False
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    dynamodb.create_table(
        TableName=table,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamodb.get_waiter("table_exists").wait(TableName=table)
    return True
