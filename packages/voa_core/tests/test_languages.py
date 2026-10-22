"""Patient languages (docs/LANGUAGES-PLAN.md, step 1): the 24 codes, normalising,
availability per use case (the language-pack seam) and the hold."""

import pytest
from voa_core import languages


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("es-US", "es-US"),
        ("es", "es-US"),
        ("ES-us", "es-US"),
        ("es_us", "es-US"),
        ("Spanish", "es-US"),
        ("spanish (us)", "es-US"),
        ("  Spanish  (US) ", "es-US"),
        ("Español", "es-US"),
        ("en", "en-US"),
        ("English", "en-US"),
        ("en-in", "en-IN"),
        ("English (India)", "en-IN"),
        ("pt", "pt-BR"),
        ("Portuguese", "pt-BR"),
        ("ar", "ar-EG"),
        ("Arabic (Egyptian)", "ar-EG"),
        ("fr", "fr-FR"),
        ("de", "de-DE"),
        ("vi", "vi-VN"),
        ("Vietnamese", "vi-VN"),
        ("zh", None),
        ("zh-CN", None),
        ("Mandarin", None),
        ("tl", None),
        ("ht", None),
        ("es-MX", None),
        ("", None),
        ("   ", None),
        (None, None),
        (42, None),
    ],
)
def test_normalize(value, code):
    assert languages.normalize(value) == code


def test_the_24_codes_in_order_with_names():
    assert len(languages.SUPPORTED) == 24 == len(set(languages.CODES))
    assert languages.CODES[0] == "ar-EG" and languages.CODES[-1] == "vi-VN"
    es = languages.BY_CODE["es-US"]
    assert (es.name, es.native) == ("Spanish (US)", "Español")
    assert all(languages.normalize(c) == c for c in languages.CODES)
    assert languages.DEFAULT == "en-US"


def test_display_labels():
    assert languages.display("es-US") == "Español (Spanish US)"
    assert languages.display("vi") == "Tiếng Việt (Vietnamese)"
    assert languages.display("en-US") == "English (US)"
    assert languages.name("es") == "Spanish (US)"


def _pack(tmp_path, name, body):
    d = tmp_path / "campaigns" / "i18n"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)


def test_only_english_is_available_without_packs(tmp_path):
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN"}
    # The real config has no packs yet either.
    assert languages.available("post_op") == {"en-US", "en-IN"}


def test_an_approved_pack_makes_a_language_available_for_its_use_case_only(tmp_path):
    _pack(tmp_path, "post_op.es-US.yaml", "status: approved\n")
    _pack(tmp_path, "post_op.vi-VN.yaml", "status: in_review\n")  # not approved
    _pack(tmp_path, "post_op.zh-CN.yaml", "status: approved\n")  # not a supported code
    _pack(tmp_path, "post_op.ko-KR.yaml", ": not yaml : [")  # unreadable: fail closed
    assert languages.available("post_op", tmp_path) == {"en-US", "en-IN", "es-US"}
    assert languages.available("pre_op", tmp_path) == {"en-US", "en-IN"}
    assert languages.hold_reason("es-US", "post_op", config_dir=tmp_path) is None
    assert languages.hold_reason("es-US", "pre_op", config_dir=tmp_path) == (
        "Waiting for Spanish (US) support"
    )


def test_hold_reason(tmp_path):
    assert languages.hold_reason("en-US", "post_op", config_dir=tmp_path) is None
    assert languages.hold_reason("en-IN", "post_op", config_dir=tmp_path) is None
    assert languages.hold_reason(None, "post_op", config_dir=tmp_path) is None  # default
    assert languages.hold_reason("es", None, config_dir=tmp_path) == (
        "Waiting for Spanish (US) support"
    )
    assert languages.hold_reason("es-US", "post_op", True, config_dir=tmp_path) is None
    assert "not supported" in languages.hold_reason("zh", "post_op", config_dir=tmp_path)
    entry = {"language": "vi-VN", "call_in_english": "true", "campaign_id": "post_op"}
    assert languages.entry_hold_reason(entry, config_dir=tmp_path) is None
    assert languages.entry_hold_reason({**entry, "call_in_english": False}, config_dir=tmp_path)


def test_held_message():
    assert languages.held_message("Maria Lopez", "es-US") == (
        "Maria Lopez's language is Spanish (US). Calls in Spanish (US) aren't available yet. "
        "Turn on 'Call in English' to call in English."
    )


def test_catalog_rows():
    rows = languages.catalog("post_op")
    assert [r["code"] for r in rows] == list(languages.CODES)
    by = {r["code"]: r for r in rows}
    assert by["en-US"]["available"] and by["en-IN"]["available"]
    assert by["es-US"] == {
        "code": "es-US",
        "name": "Spanish (US)",
        "native": "Español",
        "available": False,
    }
