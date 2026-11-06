from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: SecretStr
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str | None = "gemini-3.5-flash-lite"

    aws_region: str = "eu-central-1"
    textract_region: str | None = "eu-central-1"
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    s3_bucket: str
    dynamodb_table: str = "cashback-submissions"

    promo_file: Path = PROJECT_ROOT / "promo.json"

    @field_validator("textract_region", "gemini_fallback_model", mode="before")
    @classmethod
    def _empty_means_disabled(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


def gemini_models(settings: Settings) -> list[str]:
    fallback = settings.gemini_fallback_model
    return [settings.gemini_model, *([fallback] if fallback and fallback != settings.gemini_model else [])]


def missing_variables(error: ValidationError) -> list[str]:
    return sorted({str(issue["loc"][0]).upper() for issue in error.errors() if issue["loc"]})
