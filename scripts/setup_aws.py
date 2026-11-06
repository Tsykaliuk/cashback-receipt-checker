from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from cashback.aws_setup import SetupError, ensure_bucket, ensure_table
from cashback.services import aws_session
from cashback.settings import Settings, missing_variables


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as error:
        print("Заповни у .env: " + ", ".join(missing_variables(error)))
        return 1

    session = aws_session(settings)
    try:
        bucket_created = ensure_bucket(session.client("s3"), settings.s3_bucket, settings.aws_region)
        table_created = ensure_table(session.client("dynamodb"), settings.dynamodb_table)
    except (SetupError, ClientError, BotoCoreError) as error:
        print(f"Помилка: {error}")
        return 1

    print(f"S3 бакет {settings.s3_bucket}: {'створено' if bucket_created else 'вже існує'}")
    print(f"DynamoDB таблиця {settings.dynamodb_table}: {'створено' if table_created else 'вже існує'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
