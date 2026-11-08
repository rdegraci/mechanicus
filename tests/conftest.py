"""Shared fixtures: isolated app dir, database, and startup directory."""
import pytest

import mechanicus.config as config


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Temporary project root with its own Mechanicus app dir and database."""
    root = tmp_path / "project"
    root.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    db = root / ".mechanicus" / "seed.db"

    monkeypatch.setenv("MECHANICUS_APPDIR", str(app))
    monkeypatch.setenv("MECHANICUS_DB", str(db))
    monkeypatch.chdir(root)
    monkeypatch.setattr(config, "_STARTUP_DIR", root.resolve())
    monkeypatch.setattr(config, "_LOG_PATH", None)

    (app / "config.yaml").write_text(
        'model: gpt-4o\nreasoning_effort: medium\nrepair_attempts: 3\n'
        'jev_model: jev-1.14.0\npackage_root: ""\n'
    )
    (app / ".env").write_text("")

    from mechanicus.store import init_db

    init_db()
    return root
