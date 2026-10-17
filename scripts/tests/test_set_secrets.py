import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "set_secrets", Path(__file__).parents[1] / "set_secrets.py"
)
ss = importlib.util.module_from_spec(_spec)
sys.modules["set_secrets"] = ss
_spec.loader.exec_module(ss)

AC = "AC" + "0" * 31 + "1"  # fictional
SK = "SK" + "0" * 31 + "2"
KEY_SECRET = "a" * 32
TOKEN = "b" * 32


def test_old_system_naming_is_mapped_by_shape(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"# old layout\nTWILIO_ACCOUNT_SID={SK}\nTWILIO_AUTH_TOKEN={KEY_SECRET}\n"
        f"TWILIO_PRIMARY_ACCOUNT_SID={AC}\nTWILIO_FROM_NUMBER=+12025550100\n"
    )
    found = ss.from_dotenv(ss.parse_dotenv(env))
    assert found == {
        "twilio-account-sid": AC,
        "twilio-api-key-sid": SK,
        "twilio-api-key-secret": KEY_SECRET,
        "from_number": "+12025550100",
    }  # no auth token: the old file never held it


def test_plain_naming_maps_auth_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f'TWILIO_ACCOUNT_SID="{AC}"\nTWILIO_AUTH_TOKEN={TOKEN}\n')
    assert ss.from_dotenv(ss.parse_dotenv(env)) == {
        "twilio-account-sid": AC,
        "twilio-auth-token": TOKEN,
    }


def test_shapes_refuse_misplaced_values():
    assert ss.SHAPES["twilio-account-sid"][0].fullmatch(AC)
    assert not ss.SHAPES["twilio-account-sid"][0].fullmatch(SK)
    assert not ss.SHAPES["twilio-auth-token"][0].fullmatch(KEY_SECRET.upper())


def test_tfvars_update_replaces_or_appends(tmp_path):
    tf = tmp_path / "terraform.tfvars"
    tf.write_text('project_id = "p"\n')
    ss.update_tfvars(tf, "+12025550100", dry_run=False)
    ss.update_tfvars(tf, "+12025550101", dry_run=False)
    assert tf.read_text().count("twilio_from_number") == 1 and "+12025550101" in tf.read_text()
