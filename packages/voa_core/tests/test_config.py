from pathlib import Path

import pytest
from voa_core.config import ConfigError, load_config


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_layers_and_env_override(tmp_path):
    _write(tmp_path / "app.yaml", "gcp: {project_id: base-project}\nvoice: {voice_name: Aoede}\n")
    _write(tmp_path / "site.yaml", "site: {org_name: Example Clinic}\n")
    _write(tmp_path / "providers" / "twilio.yaml", "from_number: '+12025550100'\n")
    _write(tmp_path / "providers" / "twilio.example.yaml", "from_number: ignored\n")
    _write(tmp_path / "environments" / "demo.yaml", "voice: {max_call_seconds: 600}\n")
    cfg = load_config(tmp_path, {"VOA_ENV": "demo", "VOA__VOICE__VOICE_NAME": "Kore"})
    assert cfg.gcp.project_id == "base-project"
    assert cfg.site.org_name == "Example Clinic"
    assert cfg.voice.voice_name == "Kore"
    assert cfg.voice.max_call_seconds == 600
    assert cfg.providers["twilio"]["from_number"] == "+12025550100"
    assert cfg.env == "demo"


def test_invalid_config_fails_closed_without_echoing_values(tmp_path):
    _write(
        tmp_path / "app.yaml",
        "gcp: {project_id: ok-project}\nvoice: {max_call_seconds: 5}\nbogus: secret-value\n",
    )
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path, {})
    assert "max_call_seconds" in str(exc.value)
    assert "secret-value" not in str(exc.value)


def test_repo_app_yaml_is_valid():
    cfg = load_config(Path(__file__).parents[3] / "config", {"VOA__GCP__PROJECT_ID": "any-project"})
    assert cfg.models.live_location != "global"


def test_env_values_stay_strings(tmp_path):
    _write(tmp_path / "app.yaml", "gcp: {project_id: base-project}\n")
    cfg = load_config(
        tmp_path,
        {
            "VOA__DIALING__ALLOWLIST": "+12025550101",
            "VOA__PROVIDERS__TWILIO__FROM_NUMBER": "+12025550100",
            "VOA__AMD__VM_DROP_VERDICTS": '["machine_end_beep", "machine_end_other"]',
            "VOA__AMD__ENABLED": "false",
            "VOA__DIALING__MAX_DIALS_PER_HOUR": "12",
        },
    )
    assert cfg.dialing.allowlist == "+12025550101"
    assert cfg.providers["twilio"]["from_number"] == "+12025550100"
    assert cfg.amd.vm_drop_verdicts == ["machine_end_beep", "machine_end_other"]
    assert cfg.amd.enabled is False and cfg.dialing.max_dials_per_hour == 12


def test_voa_env_cannot_be_overridden_by_files(tmp_path):
    _write(tmp_path / "app.yaml", "env: dev\ngcp: {project_id: base-project}\n")
    assert load_config(tmp_path, {"VOA_ENV": "demo"}).env == "demo"
    assert load_config(tmp_path, {}).env == "dev"
