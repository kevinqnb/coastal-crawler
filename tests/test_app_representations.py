"""Route tests for the Representations tab (no DB: reads a tiny JSON dir)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coastal_crawler.site import app as app_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    d = tmp_path / "aid"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"id": "aid"}))
    monkeypatch.setattr(
        app_module, "get_settings",
        lambda: SimpleNamespace(repr_site_dir=str(tmp_path), repr_site_analysis_id="aid"),
    )
    return TestClient(app_module.app)


def test_page_renders_with_data_base_and_active_tab(client):
    r = client.get("/representations")
    assert r.status_code == 200
    assert "/representations/data/aid/" in r.text
    assert 'href="/representations" class="active"' in r.text


def test_data_served_and_missing_is_404(client):
    assert client.get("/representations/data/aid/meta.json").json() == {"id": "aid"}
    assert client.get("/representations/data/aid/main_layer_24.json").status_code == 404


@pytest.mark.parametrize("aid,fn", [("..", "meta.json"), ("aid", "evil.json"), ("aid", "..%2Fmeta.json")])
def test_data_rejects_bad_paths(client, aid, fn):
    assert client.get(f"/representations/data/{aid}/{fn}").status_code == 404
