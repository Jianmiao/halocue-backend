import json
import pytest
from halocue_production.model_settings import DirectionModelSettings, VENDOR_PRESETS
from halocue_production.errors import ProductionError
from halocue_production.direction_models import DirectionModelGateway


def test_direction_model_settings_and_presets(tmp_path):
    settings = DirectionModelSettings(tmp_path)

    pub = settings.public()
    assert pub["ok"] is True
    assert pub["model"]["configured"] is False
    assert len(pub["presets"]) >= 8

    # Save
    saved = settings.save({
        "preset_id": "deepseek",
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-direction-test-key",
        "max_tokens": 4096,
        "timeout": 60,
    })

    assert saved["model"]["configured"] is True
    assert saved["model"]["model"] == "deepseek-chat"
    assert saved["model"]["secret_source"] == "dpapi"

    # Verify secret is kept in dpapi, not in public file
    provider, creds = settings.provider_settings()
    assert provider == "openai"
    assert creds["api_key"] == "sk-direction-test-key"

    file_content = json.loads(settings.path.read_text(encoding="utf-8"))
    assert "api_key" not in file_content


def test_public_settings_redact_tampered_secret_fields(tmp_path):
    settings = DirectionModelSettings(tmp_path)
    settings.path.parent.mkdir(parents=True, exist_ok=True)
    settings.path.write_text(
        json.dumps(
            {
                "provider": "openai",
                "base_url": "https://example.invalid/v1",
                "model": "test-model",
                "api_key": "persisted-secret",
                "secret": "another-secret",
                "private_path": "C:/Users/creator/private.json",
            }
        ),
        encoding="utf-8",
    )

    public = settings.public()

    serialized = json.dumps(public, ensure_ascii=False)
    assert "persisted-secret" not in serialized
    assert "another-secret" not in serialized
    assert "private_path" not in serialized
    assert "api_key" not in public["model"]


def test_fetch_models_error_does_not_expose_secret_or_exception_text(tmp_path, monkeypatch):
    settings = DirectionModelSettings(tmp_path)

    def fail(_request, timeout):
        raise RuntimeError(
            "request failed with fetch-secret at C:/Users/creator/model-response.json"
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)

    with pytest.raises(ProductionError) as raised:
        settings.fetch_models(
            {
                "provider": "openai",
                "base_url": "https://example.invalid/v1",
                "api_key": "fetch-secret",
            }
        )

    error = raised.value
    serialized = json.dumps(error.to_payload(), ensure_ascii=False)
    assert error.code == "fetch_models_failed"
    assert error.status == 502
    assert "fetch-secret" not in serialized
    assert "model-response.json" not in serialized
    assert error.details == {"type": "RuntimeError"}


def test_model_gateway_errors_are_generic_and_do_not_persist_provider_details(tmp_path, monkeypatch):
    settings = DirectionModelSettings(tmp_path)
    settings._load_public = lambda: {
        "provider": "openai",
        "base_url": "https://example.invalid/v1",
        "model": "test-model",
    }
    settings.public = lambda: {
        "ok": True,
        "model": {
            "provider": "openai",
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "configured": True,
        },
    }
    settings.provider_settings = lambda: (
        "openai",
        {"base_url": "https://example.invalid/v1", "model": "test-model", "api_key": "gateway-secret"},
    )
    gateway = DirectionModelGateway(settings, tmp_path)

    class BrokenModule:
        @staticmethod
        def make_provider_from_settings(_provider, _settings):
            raise RuntimeError("gateway-secret C:/Users/creator/llm.py")

    monkeypatch.setitem(__import__("sys").modules, "llm", BrokenModule())
    with pytest.raises(ProductionError) as raised:
        gateway.provider()

    error = raised.value
    serialized = json.dumps(error.to_payload(), ensure_ascii=False)
    assert error.code == "model_provider_unavailable"
    assert "gateway-secret" not in serialized
    assert "llm.py" not in serialized
