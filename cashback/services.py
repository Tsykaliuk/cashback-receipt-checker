from dataclasses import dataclass

import boto3
from botocore.config import Config
from google import genai
from google.genai import types

from .gemini_interpreter import GeminiInterpreter
from .pipeline import CashbackPipeline
from .settings import Settings, gemini_models
from .storage import AwsStorage, Storage
from .textract_reader import TextractReader


@dataclass(frozen=True)
class Services:
    settings: Settings
    pipeline: CashbackPipeline
    storage: Storage


def aws_session(settings: Settings) -> boto3.Session:
    return boto3.Session(
        aws_access_key_id=settings.aws_access_key_id.get_secret_value() if settings.aws_access_key_id else None,
        aws_secret_access_key=(
            settings.aws_secret_access_key.get_secret_value() if settings.aws_secret_access_key else None
        ),
        region_name=settings.aws_region,
    )


GEMINI_HTTP_OPTIONS = types.HttpOptions(
    timeout=30_000,
    retry_options=types.HttpRetryOptions(attempts=2, initial_delay=1.0, max_delay=4.0),
)


def gemini_client(settings: Settings) -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key.get_secret_value(), http_options=GEMINI_HTTP_OPTIONS)


def build_services(settings: Settings) -> Services:
    session = aws_session(settings)
    storage = AwsStorage(
        s3=session.client(
            "s3",
            endpoint_url=f"https://s3.{settings.aws_region}.amazonaws.com",
            config=Config(s3={"addressing_style": "virtual"}),
        ),
        dynamodb=session.client("dynamodb"),
        bucket=settings.s3_bucket,
        table=settings.dynamodb_table,
    )
    reader = (
        TextractReader(session.client("textract", region_name=settings.textract_region))
        if settings.textract_region
        else None
    )
    pipeline = CashbackPipeline(
        reader=reader,
        interpreter=GeminiInterpreter(gemini_client(settings), gemini_models(settings)),
        storage=storage,
    )
    return Services(settings=settings, pipeline=pipeline, storage=storage)
