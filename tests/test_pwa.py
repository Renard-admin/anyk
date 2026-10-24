"""Tests for PWA endpoints: manifest.json, sw.js, share-target."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

# Import after sys.path update
from main import app

client = TestClient(app, follow_redirects=False)


# ── /manifest.json ────────────────────────────────────────────────────────────

def test_manifest_status():
    resp = client.get("/manifest.json")
    assert resp.status_code == 200


def test_manifest_content_type():
    resp = client.get("/manifest.json")
    ct = resp.headers.get("content-type", "")
    assert "json" in ct or "manifest" in ct, f"Unexpected content-type: {ct}"


def test_manifest_required_fields():
    resp = client.get("/manifest.json")
    data = resp.json()
    for field in ("name", "short_name", "start_url", "display", "icons"):
        assert field in data, f"Missing field: {field}"


def test_manifest_display_standalone():
    resp = client.get("/manifest.json")
    assert resp.json()["display"] == "standalone"


def test_manifest_has_share_target():
    resp = client.get("/manifest.json")
    data = resp.json()
    assert "share_target" in data, "manifest.json must include share_target for PWA sharing"
    st = data["share_target"]
    assert st.get("action") == "/share-target"
    assert st.get("method", "").upper() == "POST"


def test_manifest_share_target_accepts_pdf_and_images():
    resp = client.get("/manifest.json")
    files_params = resp.json()["share_target"]["params"]["files"]
    all_accept = []
    for fp in files_params:
        all_accept.extend(fp.get("accept", []))
    for mime in ("application/pdf", "image/png", "image/jpeg"):
        assert mime in all_accept, f"{mime} not in share_target accept list"


def test_manifest_theme_color():
    resp = client.get("/manifest.json")
    data = resp.json()
    assert "theme_color" in data
    assert data["theme_color"].startswith("#")


def test_manifest_icons_present():
    resp = client.get("/manifest.json")
    icons = resp.json().get("icons", [])
    assert len(icons) >= 1, "At least one icon required for PWA"
    for icon in icons:
        assert "src" in icon


# ── /sw.js ────────────────────────────────────────────────────────────────────

def test_sw_js_status():
    resp = client.get("/sw.js")
    assert resp.status_code == 200


def test_sw_js_content_type():
    resp = client.get("/sw.js")
    ct = resp.headers.get("content-type", "")
    assert "javascript" in ct, f"Expected JavaScript content-type, got: {ct}"


def test_sw_js_allowed_header():
    resp = client.get("/sw.js")
    assert resp.headers.get("service-worker-allowed") == "/", \
        "SW must declare Service-Worker-Allowed: / to scope the whole app"


def test_sw_js_has_install_event():
    resp = client.get("/sw.js")
    assert "install" in resp.text


def test_sw_js_has_fetch_event():
    resp = client.get("/sw.js")
    assert "fetch" in resp.text


# ── /share-target ─────────────────────────────────────────────────────────────

def _minimal_png() -> bytes:
    """1×1 white PNG for testing."""
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )


def test_share_target_post_no_file_redirects_home():
    """POST without a file → redirect to / with share_error."""
    resp = client.post("/share-target")
    assert resp.status_code == 303
    loc = resp.headers.get("location", "")
    assert "/" in loc


def test_share_target_post_image_redirects():
    """POST a valid PNG → either redirect to /report/ or home with error (pipeline may fail in test env)."""
    png = _minimal_png()
    resp = client.post(
        "/share-target",
        files={"receipt": ("test_receipt.png", io.BytesIO(png), "image/png")},
    )
    assert resp.status_code == 303
    loc = resp.headers.get("location", "")
    assert "/" in loc  # either /report/<id> or /?share_error=...


def test_share_target_post_large_file_redirects_with_error():
    """File > 20 MB → redirect to / with share_error=too_large."""
    big = b"A" * (21 * 1024 * 1024)
    resp = client.post(
        "/share-target",
        files={"receipt": ("big.png", io.BytesIO(big), "image/png")},
    )
    assert resp.status_code == 303
    loc = resp.headers.get("location", "")
    assert "too_large" in loc


def test_share_target_post_unsupported_format_redirects():
    """A text file → redirect to / with share_error=format."""
    resp = client.post(
        "/share-target",
        files={"receipt": ("doc.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert resp.status_code == 303
    loc = resp.headers.get("location", "")
    assert "/" in loc


# ── Static assets ─────────────────────────────────────────────────────────────

def test_icon_svg_served():
    resp = client.get("/static/icon.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers.get("content-type", "").lower()


def test_index_links_manifest():
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'rel="manifest"' in resp.text


def test_index_links_sw_registration():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "serviceWorker" in resp.text and "sw.js" in resp.text


def test_index_has_theme_color_meta():
    resp = client.get("/")
    assert 'name="theme-color"' in resp.text


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
