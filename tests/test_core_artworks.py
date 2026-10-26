"""原圖登錄與更換偵測。"""

from __future__ import annotations

import os
import shutil

from PIL import Image

from backend.services import artworks, state


def _bump_mtime(path) -> None:
    stat = path.stat()
    later = stat.st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(later, later))


def test_registers_base_image_once(paths_env, db_conn):
    info = artworks.sync_active_artwork(db_conn)
    assert info == {
        "id": 1,
        "url": "/media/artworks/1/original.webp?v=1",
        "width": 1600,
        "height": 1132,
    }
    assert (paths_env.data_dir / "artworks/1/original.webp").read_bytes() == (
        paths_env.base_image.read_bytes()
    )
    assert artworks.sync_active_artwork(db_conn)["id"] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM artworks").fetchone()[0] == 1
    assert state.get(db_conn, "base_image_stat")["size"] == paths_env.base_image.stat().st_size


def test_replacing_base_image_creates_new_version_and_reverting_reuses_old(paths_env, db_conn):
    backup = paths_env.root / "base-backup.webp"
    shutil.copyfile(paths_env.base_image, backup)
    assert artworks.sync_active_artwork(db_conn)["id"] == 1

    Image.new("RGB", (800, 600), (10, 200, 30)).save(paths_env.base_image, format="WEBP")
    _bump_mtime(paths_env.base_image)
    info = artworks.sync_active_artwork(db_conn)
    assert info["id"] == 2 and (info["width"], info["height"]) == (800, 600)
    assert (paths_env.data_dir / "artworks/2/original.webp").is_file()
    assert state.get(db_conn, "active_artwork_id") == 2

    shutil.copyfile(backup, paths_env.base_image)
    _bump_mtime(paths_env.base_image)
    assert artworks.sync_active_artwork(db_conn)["id"] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM artworks").fetchone()[0] == 2


def test_invalid_replacement_keeps_previous_version(paths_env, db_conn):
    assert artworks.sync_active_artwork(db_conn)["id"] == 1
    paths_env.base_image.write_bytes(b"not an image")
    _bump_mtime(paths_env.base_image)

    assert artworks.sync_active_artwork(db_conn)["id"] == 1
    assert db_conn.execute("SELECT COUNT(*) FROM artworks").fetchone()[0] == 1
    assert "artwork.invalid" in paths_env.runtime_log.read_text(encoding="utf-8")


def test_missing_base_image_returns_none(paths_env, db_conn):
    paths_env.base_image.unlink()
    assert artworks.sync_active_artwork(db_conn) is None
