import pytest

from scholarforge import paths
from scholarforge.errors import ConfigError
from scholarforge.settings import ImageProviderSettings, Settings, load_settings, save_settings, set_setting


def test_defaults_are_valid_and_home_is_isolated(sf_home):
    s = load_settings()
    assert isinstance(s, Settings)
    assert str(paths.data_dir()).startswith(str(sf_home))
    assert s.workflow.max_revision_rounds == 2
    assert s.images.enabled is False  # paid provider is opt-in
    assert s.license.enforcement == "off"


def test_file_and_env_overrides(sf_home, monkeypatch):
    cfg = paths.config_file()
    cfg.write_text("workflow:\n  lease_minutes: 5\nnetwork:\n  timeout_s: 7\n", encoding="utf-8")
    monkeypatch.setenv("SCHOLARFORGE_OFFLINE", "1")
    s = load_settings()
    assert s.workflow.lease_minutes == 5
    assert s.network.timeout_s == 7
    assert s.network.enabled is False


def test_invalid_config_is_reported(sf_home):
    paths.config_file().write_text("workflow:\n  lease_minutes: -3\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert exc.value.details["errors"]


def test_unknown_keys_rejected(sf_home):
    paths.config_file().write_text("nonsense: true\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings()


def test_set_setting_and_save_roundtrip(sf_home):
    s = load_settings()
    s2 = set_setting(s, "workflow.max_revision_rounds", "4")
    assert s2.workflow.max_revision_rounds == 4
    save_settings(s2)
    assert load_settings().workflow.max_revision_rounds == 4
    with pytest.raises(ConfigError):
        set_setting(s, "workflow.no_such_key", "1")
    with pytest.raises(ConfigError):
        set_setting(s, "workflow.max_revision_rounds", "many")


def test_image_size_validation():
    assert ImageProviderSettings(size="1536x1024").size == "1536x1024"
    with pytest.raises(ValueError):
        ImageProviderSettings(size="1000x1000")
    with pytest.raises(ValueError):
        ImageProviderSettings(size="large")
