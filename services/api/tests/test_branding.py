"""Admin → Branding: the staff consoles' title, names, colours and images."""

import base64
import struct

import pytest
from test_api import make
from voa_api import routes_branding as rb

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
WEBP = b"RIFF" + struct.pack("<I", 40) + b"WEBPVP8 " + b"\x00" * 32
ICO = b"\x00\x00\x01\x00\x01\x00" + b"\x00" * 40
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture
def setup():
    client, api = make()
    fake = rb.MemoryBlobs()
    client.app.state.branding_blobs = fake  # the bucket is empty in tests
    return client, api, fake


def test_defaults_when_nothing_is_set(setup):
    client, _, _ = setup
    body = client.get("/api/branding").json()
    assert body["product_title"] is None and body["staff_org_name"] is None
    assert body["colors"] == {}
    assert body["default_colors"]["brand"] == "#22873b"
    assert set(body["default_colors"]) == set(rb.DEFAULT_COLORS)
    assert body["assets"] == {"logo": None, "logo_wide": None, "favicon": None}
    assert body["is_default"]["all"] is True
    assert body["is_default"]["colors"]["brand"] is True


def test_partial_update_keeps_what_was_not_sent(setup):
    client, _, _ = setup
    r = client.put(
        "/api/branding",
        json={"product_title": "  Riverside   Recovery Line ", "colors": {"brand": "#1A5FB4"}},
        headers={"X-Acting-User": "admin@example.org"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product_title"] == "Riverside Recovery Line"
    assert body["colors"] == {"brand": "#1a5fb4"}
    assert body["is_default"]["product_title"] is False
    assert body["is_default"]["colors"]["brand"] is False
    assert body["updated_by"] == "admin@example.org"

    r = client.put(
        "/api/branding",
        json={"staff_org_name": "Riverside Clinic", "colors": {"urgent": "#c0392b"}},
    )
    body = r.json()
    assert body["product_title"] == "Riverside Recovery Line"  # untouched
    assert body["staff_org_name"] == "Riverside Clinic"
    assert body["colors"] == {"brand": "#1a5fb4", "urgent": "#c0392b"}

    # "" or null puts one field back to its default
    body = client.put("/api/branding", json={"product_title": "", "colors": {"brand": None}}).json()
    assert body["product_title"] is None and body["colors"] == {"urgent": "#c0392b"}
    assert body["staff_org_name"] == "Riverside Clinic"


@pytest.mark.parametrize(
    "patch",
    [
        {"colors": {"brand": "green"}},
        {"colors": {"brand": "#12345"}},
        {"colors": {"brand": "#1234567"}},
        {"colors": {"brand": "#12345g"}},
        {"colors": {"brand": "rgb(0,0,0)"}},
        {"colors": {"not-a-token": "#123456"}},
        {"product_title": "A" * 61},
        {"product_title": "<script>x</script>"},
        {"staff_org_name": "Tab\there"},
        {"staff_org_name": "Bell\x07"},
        {"logo": "x"},  # unknown field
    ],
)
def test_validation(setup, patch):
    client, api, _ = setup
    r = client.put("/api/branding", json=patch)
    assert r.status_code == 422, (patch, r.text)
    assert client.get("/api/branding").json()["is_default"]["all"] is True


def test_title_at_the_limit_is_accepted(setup):
    client, _, _ = setup
    r = client.put("/api/branding", json={"product_title": "É" * 60})
    assert r.status_code == 200 and r.json()["product_title"] == "É" * 60


def test_asset_round_trip_and_version_bump(setup):
    client, _, fake = setup
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/png", "data_base64": b64(PNG)}
    )
    assert r.status_code == 200, r.text
    v1 = r.json()["assets"]["logo"]
    assert v1["content_type"] == "image/png" and v1["bytes"] == len(PNG)
    assert fake.objects["branding/logo"] == (PNG, "image/png")

    g = client.get("/api/branding/assets/logo?v=1")
    assert g.status_code == 200
    assert g.content == PNG
    assert g.headers["content-type"] == "image/png"
    assert g.headers["cache-control"] == "private, max-age=300"
    assert g.headers["x-content-type-options"] == "nosniff"

    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/webp", "data_base64": b64(WEBP)}
    )
    v2 = r.json()["assets"]["logo"]
    assert v2["version"] > v1["version"] and v2["content_type"] == "image/webp"
    assert client.get("/api/branding/assets/logo").content == WEBP

    for kind, ctype, data in (
        ("logo_wide", "image/jpeg", JPEG),
        ("favicon", "image/x-icon", ICO),
        ("favicon", "image/vnd.microsoft.icon", ICO),
    ):
        r = client.put(
            f"/api/branding/assets/{kind}", json={"content_type": ctype, "data_base64": b64(data)}
        )
        assert r.status_code == 200, (kind, r.text)
    body = client.get("/api/branding").json()
    assert body["assets"]["favicon"]["content_type"] == "image/x-icon"
    assert "data" not in str(body["assets"])  # versions only, never bytes


def test_unset_asset_is_404_and_unknown_kind_too(setup):
    client, _, _ = setup
    assert client.get("/api/branding/assets/logo").status_code == 404
    assert client.get("/api/branding/assets/banner").status_code == 404
    r = client.put(
        "/api/branding/assets/banner", json={"content_type": "image/png", "data_base64": b64(PNG)}
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("ctype", "data", "status"),
    [
        ("image/svg+xml", SVG, 415),  # declared SVG
        ("image/png", SVG, 415),  # SVG disguised as PNG: the magic bytes say otherwise
        ("image/png", b"GIF89a" + b"\x00" * 20, 415),  # GIF is not on the list
        ("image/png", JPEG, 415),  # declared type and content disagree
        ("text/html", PNG, 415),
        ("image/png", b"", 422),
    ],
)
def test_images_are_checked_by_their_magic_bytes(setup, ctype, data, status):
    client, _, fake = setup
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": ctype, "data_base64": b64(data)}
    )
    assert r.status_code == status, r.text
    assert fake.objects == {}


def test_bad_base64_is_refused(setup):
    client, _, _ = setup
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/png", "data_base64": "@@@notb64"}
    )
    assert r.status_code == 422


def test_size_limits(setup):
    client, _, fake = setup
    big = PNG + b"\x00" * (512 * 1024)
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/png", "data_base64": b64(big)}
    )
    assert r.status_code in (413, 422)
    fav = ICO + b"\x00" * (128 * 1024)
    r = client.put(
        "/api/branding/assets/favicon",
        json={"content_type": "image/x-icon", "data_base64": b64(fav)},
    )
    assert r.status_code == 413
    ok = PNG + b"\x00" * (300 * 1024)  # under 512 KB is fine for a logo...
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/png", "data_base64": b64(ok)}
    )
    assert r.status_code == 200
    r = client.put(  # ...but too big for a favicon
        "/api/branding/assets/favicon", json={"content_type": "image/png", "data_base64": b64(ok)}
    )
    assert r.status_code == 413
    assert set(fake.objects) == {"branding/logo"}


def test_reset_deletes_the_doc_and_the_images(setup):
    client, api, fake = setup
    client.put("/api/branding", json={"product_title": "Riverside", "colors": {"info": "#0a58ca"}})
    client.put(
        "/api/branding/assets/favicon",
        json={"content_type": "image/x-icon", "data_base64": b64(ICO)},
    )
    assert fake.objects
    r = client.delete("/api/branding")
    assert r.status_code == 200 and r.json()["is_default"]["all"] is True
    assert fake.objects == {}
    assert client.get("/api/branding/assets/favicon").status_code == 404
    assert client.get("/api/branding").json()["is_default"]["all"] is True


def test_without_a_bucket_tests_fall_back_to_memory():
    (
        client,
        _,
    ) = make()
    r = client.put(
        "/api/branding/assets/logo", json={"content_type": "image/png", "data_base64": b64(PNG)}
    )
    assert r.status_code == 200
    assert client.get("/api/branding/assets/logo").content == PNG


def test_sniff_image():
    assert rb.sniff_image(PNG) == "image/png"
    assert rb.sniff_image(JPEG) == "image/jpeg"
    assert rb.sniff_image(WEBP) == "image/webp"
    assert rb.sniff_image(ICO) == "image/x-icon"
    assert rb.sniff_image(SVG) is None
    assert rb.sniff_image(b"\x00\x00\x01\x00\x00\x00") is None  # zero-image ICO header
    assert rb.sniff_image(b"") is None


@pytest.mark.parametrize(
    ("who", "method", "path", "allowed"),
    [
        ("nurse_ui", "GET", "/api/branding", True),
        ("agent", "GET", "/api/branding", True),
        ("admin_ui", "GET", "/api/branding", True),
        ("nurse_ui", "GET", "/api/branding/assets/favicon", True),
        ("nurse_ui", "PUT", "/api/branding", False),
        ("agent", "PUT", "/api/branding", False),
        ("admin_ui", "PUT", "/api/branding", True),
        ("nurse_ui", "DELETE", "/api/branding", False),
        ("nurse_ui", "PUT", "/api/branding/assets/logo", False),
        ("admin_ui", "PUT", "/api/branding/assets/logo", True),
    ],
)
def test_only_the_admin_console_changes_branding(monkeypatch, who, method, path, allowed):
    from fastapi.testclient import TestClient
    from test_route_policy import ADMIN_UI, AGENT, NURSE_UI, _app, _token

    email = {"nurse_ui": NURSE_UI, "admin_ui": ADMIN_UI, "agent": AGENT}[who]
    monkeypatch.setenv("VOA_ENV", "demo")
    app = _app("demo")
    with TestClient(app) as c:
        c.app.state.branding_blobs = rb.MemoryBlobs()
        r = c.request(method, path, headers=_token(email), json={})
    assert (r.status_code != 403) is allowed, (who, method, path, r.status_code)
