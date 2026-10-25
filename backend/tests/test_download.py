from app.services import download


def test_aria2c_missing_falls_back_to_native(monkeypatch):
    # aria2c が無ければ native の並列断片ダウンロードに落ちる。
    monkeypatch.setattr(download.settings, "downloader", "aria2c")
    monkeypatch.setattr(download.settings, "aria2c_path", "aria2c")
    monkeypatch.setattr(download.settings, "concurrent_fragments", 16)
    monkeypatch.setattr(download.shutil, "which", lambda _: None)
    assert download.downloader_args() == ["--concurrent-fragments", "16"]


def test_native_is_default_and_uses_concurrent_fragments(monkeypatch):
    monkeypatch.setattr(download.settings, "downloader", "native")
    monkeypatch.setattr(download.settings, "concurrent_fragments", 8)
    assert download.downloader_args() == ["--concurrent-fragments", "8"]


def test_live_never_parallelises(monkeypatch):
    monkeypatch.setattr(download.settings, "downloader", "native")
    monkeypatch.setattr(download.settings, "concurrent_fragments", 16)
    assert download.downloader_args(is_live=True) == []


def test_live_always_uses_native_even_when_aria2c_exists(monkeypatch):
    monkeypatch.setattr(download.settings, "downloader", "aria2c")
    monkeypatch.setattr(download.shutil, "which", lambda _: "aria2c")
    assert download.downloader_args(is_live=True) == []


def test_static_download_uses_configured_aria2c(monkeypatch):
    monkeypatch.setattr(download.settings, "downloader", "aria2c")
    # .env の実パス設定に依存しないよう、テスト内で固定する。
    monkeypatch.setattr(download.settings, "aria2c_path", "aria2c")
    monkeypatch.setattr(download.shutil, "which", lambda _: "aria2c")
    args = download.downloader_args()
    assert args[0:2] == ["--downloader", "aria2c:aria2c"]
    assert "-x16 -s16 -k1M -j4" in args[-1]
