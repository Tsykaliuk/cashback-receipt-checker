import pytest

from cashback.settings import Settings, gemini_models


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("S3_BUCKET", "cashback-receipts-test")


def test_textract_defaults_to_frankfurt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXTRACT_REGION", raising=False)
    assert Settings().textract_region == "eu-central-1"


def test_empty_textract_region_disables_textract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXTRACT_REGION", "")
    assert Settings().textract_region is None


def test_fallback_model_is_added_after_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "backup")
    assert gemini_models(Settings()) == ["main", "backup"]


def test_empty_fallback_leaves_single_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "main")
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "")
    assert gemini_models(Settings()) == ["main"]
