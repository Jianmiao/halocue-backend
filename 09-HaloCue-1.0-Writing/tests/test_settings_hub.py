import json
import pytest
from halocue_writing.service import WritingService
from halocue_writing.model_settings import WritingModelSettings, UserPreferencesStore, ModelSecretStore
from halocue_writing.errors import DomainError


def test_model_secret_store_and_settings(tmp_path):
    settings = WritingModelSettings(tmp_path)

    # Initially empty
    pub = settings.public()
    assert pub["ok"] is True
    assert pub["model"]["configured"] is False
    assert len(pub["presets"]) >= 8

    # Save DeepSeek config with key
    saved = settings.save({
        "preset_id": "deepseek",
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "sk-test-secret-key-123456",
        "max_tokens": 4096,
        "timeout": 60,
        "reasoning_mode": "balanced",
    })

    assert saved["model"]["configured"] is True
    assert saved["model"]["model"] == "deepseek-chat"
    assert saved["model"]["secret_source"] == "dpapi"

    # Verify secret is loaded without leaking into public JSON
    provider_type, conf = settings.provider_settings()
    assert provider_type == "openai"
    assert conf["api_key"] == "sk-test-secret-key-123456"
    assert conf["model"] == "deepseek-chat"

    # Public JSON file does NOT contain api_key
    file_content = json.loads(settings.path.read_text(encoding="utf-8"))
    assert "api_key" not in file_content


def test_user_preferences_store(tmp_path):
    store = UserPreferencesStore(tmp_path)

    # Defaults
    defaults = store.load()
    assert defaults["writing_tone"] == "bond_short"
    assert defaults["char_warning_threshold"] == 35

    # Save update
    updated = store.save({
        "writing_tone": "main_battle",
        "char_warning_threshold": 40,
        "aa_pacing_wait_ms": 3000,
        "max_stage_characters": 3,
    })

    assert updated["writing_tone"] == "main_battle"
    assert updated["char_warning_threshold"] == 40
    assert updated["aa_pacing_wait_ms"] == 3000
    assert updated["max_stage_characters"] == 3


def test_service_settings_and_diagnostics(tmp_path):
    service = WritingService(tmp_path)

    # Initial settings
    state = service.writing_model_settings_public()
    assert state["ok"] is True
    assert state["model"]["configured"] is False

    # Configure
    configured = service.configure_writing_model({
        "preset_id": "siliconflow",
        "provider": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "api_key": "sk-siliconflow-test",
    })
    assert configured["model"]["configured"] is True

    # Preferences
    prefs = service.user_preferences()
    assert prefs["ok"] is True
    assert prefs["preferences"]["writing_tone"] == "bond_short"

    saved_prefs = service.save_user_preferences({"char_warning_threshold": 50})
    assert saved_prefs["preferences"]["char_warning_threshold"] == 50

    # System Diagnostics
    diag = service.system_diagnostics()
    assert diag["ok"] is True
    assert "writing_service" in diag
    assert "production_service" in diag
    assert "data_dir" not in diag["writing_service"]
    assert str(tmp_path.resolve()) not in json.dumps(diag, ensure_ascii=False)
