"""yt-dlp のダウンローダ選択。

YouTube の映像は HLS/DASH の断片（fragment）に分かれているため、
yt-dlp 内蔵の並列断片ダウンロード (`-N`/`--concurrent-fragments`) のほうが
aria2c の単一ファイル分割戦略より速く、安定して動く。
そのため既定は native + `-N` とし、aria2c は明示的に選んだ場合のみ使う。

ライブ配信の実時間録画では、断片が到着し次第取得するため並列化の効果は無い。
取りこぼしを避けるため何も付けない。
"""
import shutil

from ..config import settings


def downloader_args(*, is_live: bool = False) -> list[str]:
    if is_live:
        return []
    if settings.downloader == "aria2c" and shutil.which(settings.aria2c_path):
        args = (f"-x{settings.aria2c_connections} -s{settings.aria2c_split} "
                f"-k{settings.aria2c_min_split_size} -j{settings.aria2c_max_concurrent_downloads} "
                "--file-allocation=none --console-log-level=warn")
        return ["--downloader", f"aria2c:{settings.aria2c_path}", "--downloader-args", f"aria2c:{args}"]
    fragments = max(1, int(settings.concurrent_fragments))
    if fragments <= 1:
        return []
    return ["--concurrent-fragments", str(fragments)]
