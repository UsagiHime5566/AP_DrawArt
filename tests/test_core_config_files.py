"""手動編輯的本機設定檔：格式小錯誤要能容忍，真正壞掉時不能讓 Web 服務無法就緒。"""

from __future__ import annotations

import pytest

from backend.config import (
    DEFAULT_DISPLAYS_CONFIG,
    config_file_error,
    read_displays_config,
    read_openai_key,
)

SECRET = "sk-test-not-a-real-key-0123456789"


def _write_key_file(paths, text: str, bom: bool = False) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    paths.openai_key_file.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def test_key_file_with_trailing_comma_and_bom_is_accepted(paths_env) -> None:
    # 記事本存檔常見：UTF-8 BOM＋最後一項後面多一個逗號
    _write_key_file(paths_env, '{\n  "api_key": "%s",\n}\n' % SECRET, bom=True)
    assert read_openai_key() == (SECRET, "file")
    assert config_file_error(paths_env.openai_key_file) is None


def test_broken_key_file_counts_as_not_configured(paths_env) -> None:
    _write_key_file(paths_env, '{\n  "api_key": %s\n}\n' % SECRET)
    assert read_openai_key() == (None, None)
    problem = config_file_error(paths_env.openai_key_file)
    assert problem is not None and "第 2 行" in problem
    # 錯誤訊息不能帶出金鑰內容
    assert SECRET not in problem


def test_key_file_must_be_an_object(paths_env) -> None:
    _write_key_file(paths_env, '["%s"]' % SECRET)
    assert read_openai_key() == (None, None)
    assert "JSON 物件" in config_file_error(paths_env.openai_key_file)


def test_broken_displays_config_falls_back_to_defaults(paths_env) -> None:
    paths_env.config_dir.mkdir(parents=True, exist_ok=True)
    paths_env.displays_config.write_text('{"exhibition": {"monitor": 1}', encoding="utf-8")
    assert read_displays_config() == DEFAULT_DISPLAYS_CONFIG
    assert "displays.json" in config_file_error(paths_env.displays_config)


def test_health_stays_ready_with_broken_key_file(paths_env, monkeypatch: pytest.MonkeyPatch, client) -> None:
    monkeypatch.setenv("AP_DRAWART_AI_PROVIDER", "openai")
    _write_key_file(paths_env, '{\n  "api_key": %s\n}\n' % SECRET)

    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    # 啟動器只看資料庫是否可用來判斷就緒；金鑰問題改以 setup_required 與原因呈現
    assert body["checks"]["database"]["ok"] is True
    assert body["status"] == "setup_required"
    assert "openai.local.json" in body["checks"]["openai_key"]["error"]
    assert SECRET not in response.text

    status = client.get("/api/admin/openai-key").json()
    assert status["configured"] is False and status["error"]
    # 在後臺重新設定即可覆寫壞掉的檔案
    fixed = client.put("/api/admin/openai-key", json={"api_key": SECRET}).json()
    assert fixed["configured"] is True and fixed["error"] is None
    assert client.get("/api/health").json()["checks"]["openai_key"]["ok"] is True


def test_one_failing_check_does_not_break_health(monkeypatch: pytest.MonkeyPatch, client) -> None:
    from backend.services import health

    def broken() -> dict:
        raise OSError("磁碟資訊讀取失敗")

    monkeypatch.setattr(health.disk, "disk_status", broken)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["disk"] == {"ok": False, "error": "OSError: 磁碟資訊讀取失敗"}
