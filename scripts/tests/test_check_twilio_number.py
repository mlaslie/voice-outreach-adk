import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_twilio_number", Path(__file__).parents[1] / "check_twilio_number.py"
)
ct = importlib.util.module_from_spec(_spec)
sys.modules["check_twilio_number"] = ct
_spec.loader.exec_module(ct)

GW = "https://gw.example.org"


def check(**kw):
    base = dict(
        sms_url=GW + "/twilio/sms", sms_method="POST", voice_url="", sms_app=False, gateway_url=GW
    )
    return ct.problems(**{**base, **kw})


def test_the_expected_settings_pass():
    assert check() == []
    assert check(sms_url=GW + "/twilio/sms/", sms_method="post") == []


def test_each_wrong_setting_is_named():
    assert check(sms_url="https://other.example.org/api/sms-inbound") == [
        f"SmsUrl should be {GW}/twilio/sms"
    ]
    assert check(sms_method="GET") == ["SmsMethod should be POST"]
    assert "VoiceUrl" in check(voice_url="https://x.example.org/voice")[0]
    assert "SMS application" in check(sms_app=True)[0]


def test_tfvars_are_read_without_comments():
    text = 'project_id = "p-1"\n# sms_from_number = "nope"\ntwilio_from_number = "+12025550100"\n'
    assert ct.tfvar(text, "project_id") == "p-1"
    assert ct.tfvar(text, "sms_from_number") == ""
    assert ct.tfvar(text, "twilio_from_number") == "+12025550100"
