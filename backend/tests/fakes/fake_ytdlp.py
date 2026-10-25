#!/usr/bin/env python3
"""Local yt-dlp stand-in controlled by JSON state for deterministic tests."""
import json
import os
import sys
import time
import signal
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def load_state() -> dict:
    state_path = os.environ.get("FAKE_YTDLP_STATE")
    if not state_path:
        return {}
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


def log_call(argv: list[str]) -> None:
    target = os.environ.get("FAKE_YTDLP_CALLS")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"argv": argv, "started_at": time.time()}, ensure_ascii=False) + "\n")


def fail(kind: str) -> int:
    errors = {
        "rate_limited": "ERROR: HTTP Error 429: Too Many Requests\n",
        "bot": "ERROR: Sign in to confirm you're not a bot\n",
        "network_error": "ERROR: Unable to download webpage: network unreachable\n",
        "no_streams_tab": "ERROR: [youtube:tab] @channel: This channel does not have a streams tab\n",
        "private": "ERROR: [youtube] video: Private video. Sign in if you've been granted access\n",
        "deleted": "ERROR: [youtube] video: This video has been removed\n",
        "unavailable": "ERROR: [youtube] video: This video is unavailable\n",
        "members_only": "ERROR: [youtube] video: This video is available to this channel's members\n",
        "upcoming_error": "ERROR: [youtube] video: This live event will begin in 2 hours\n",
    }
    sys.stderr.write(errors.get(kind, f"ERROR: {kind}\n"))
    return 1


def _terminal_error_exit(recording: dict) -> int | None:
    error = recording.get("terminal_error")
    if not error:
        return None
    sys.stderr.write(f"ERROR: {error}\n")
    sys.stderr.flush()
    return int(recording.get("terminal_exit_code", 1))


_INTERRUPTED = False


def _interrupt(_signum, _frame):
    global _INTERRUPTED
    _INTERRUPTED = True


def _media_fixture(start_offset_seconds: float = 0) -> bytes:
    offset_tag = str(start_offset_seconds).replace(".", "_")
    path = Path(tempfile.gettempdir()) / f"yla-fake-media-{offset_tag}.ts"
    if not path.exists():
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1",
                        "-output_ts_offset", str(start_offset_seconds),
                        "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpegts", str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return path.read_bytes()


def _recording(argv: list[str], video: dict, video_id: str) -> int:
    global _INTERRUPTED
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _interrupt)
    if video.get("live_status") == "is_upcoming":
        return fail("upcoming_error")
    template = argv[argv.index("-o") + 1]
    stem_path = Path(template.replace("%(ext)s", "mkv"))
    stem_path.parent.mkdir(parents=True, exist_ok=True)
    stem = stem_path.stem
    metadata = {key: value for key, value in video.items() if key not in ("recording", "status", "error", "sleep")}
    metadata.setdefault("id", video_id)
    metadata.setdefault("release_timestamp", int(time.time()) - 5)
    if "live_status" in metadata and metadata["live_status"] == "is_live":
        metadata.pop("duration", None)
    else:
        metadata.setdefault("duration", 3)
    if template != "-" and "--write-info-json" in argv:
        (stem_path.parent / f"{stem}.info.json").write_text(json.dumps(metadata), encoding="utf-8")
    if "--print-to-file" in argv:
        target = Path(argv[argv.index("--print-to-file") + 2])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(metadata), encoding="utf-8")
    if "--write-thumbnail" in argv:
        thumbnail_template = next((argv[index + 1].split(":", 1)[1]
                                   for index, value in enumerate(argv[:-1])
                                   if value == "-o" and argv[index + 1].startswith("thumbnail:")), None)
        if template == "-":
            thumbnail_path = Path.cwd() / "-.webp"
        else:
            thumbnail_path = (Path(thumbnail_template.replace("%(ext)s", "webp"))
                              if thumbnail_template else stem_path.parent / f"{stem}.webp")
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        thumbnail_path.write_bytes(b"fake-thumbnail")
    description_template = next((argv[index + 1].split(":", 1)[1]
                                 for index, value in enumerate(argv[:-1])
                                 if value == "-o" and argv[index + 1].startswith("description:")), None)
    if template == "-" and "--write-description" in argv:
        (Path.cwd() / "-.description").write_text(
            str(metadata.get("description") or ""), encoding="utf-8")
    elif description_template:
        description_path = Path(description_template.replace("%(ext)s", "description"))
        description_path.parent.mkdir(parents=True, exist_ok=True)
        description_path.write_text(str(metadata.get("description") or ""), encoding="utf-8")
    if "--skip-download" in argv and "--write-subs" in argv:
        state = load_state().get("videos", {}).get(video_id, {})
        replay = state.get("chat_replay")
        if replay:
            time.sleep(state.get("chat_replay_delay", 0.5))
            target = Path(template.replace("%(id)s", video_id).replace("%(ext)s", "live_chat.json"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(json.dumps(item) + "\n" for item in replay), encoding="utf-8")
            return 0
        output = stem_path.parent / f"{stem}.live_chat.json.part"
        fragment = stem_path.parent / f"{stem}.live_chat.json.part-Frag5"
        capture_started_epoch = time.time()
        while not _INTERRUPTED:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8") as stream:
                state = load_state().get("videos", {}).get(video_id, {})
                published_usec = int(time.time() * 1_000_000)
                record = {"isLive": True,
                          "videoOffsetTimeMsec": str(int((published_usec / 1_000_000 - capture_started_epoch) * 1000)),
                          "replayChatItemAction": {"actions": [{"addChatItemAction": {
                              "item": {"liveChatTextMessageRenderer": {
                                  "id": f"fake-{time.time_ns()}", "timestampUsec": str(published_usec),
                                  "authorName": {"simpleText": "Fake viewer"},
                                  "message": {"runs": [{"text": "fake chat"}]}}}}}]}}
                stream.write(json.dumps(record) + "\n")
            fragment.write_text('{"partial":true}\n', encoding="utf-8")
            state = load_state().get("videos", {}).get(video_id, {})
            if state.get("recording", {}).get("ended"):
                output.replace(stem_path.parent / f"{stem}.live_chat.json")
                fragment.unlink(missing_ok=True)
                return 0
            time.sleep(0.2)
        output.parent.mkdir(parents=True, exist_ok=True)
        return 1
    recording = video.get("recording", {})
    if recording.get("format_error") and "-f" in argv:
        return fail("Requested format is not available")
    if "--live-from-start" in argv and recording.get("live_from_start_unsupported"):
        return fail("live-from-start is unsupported")
    if recording.get("stderr_bytes"):
        sys.stderr.write("x" * int(recording["stderr_bytes"]))
        sys.stderr.flush()
    if recording.get("delay"):
        time.sleep(float(recording["delay"]))
    fixture = _media_fixture()
    if "--ignore-no-formats-error" in argv and "-o" in argv and "--dump-single-json" in argv:
        pass
    if template == "-":
        while not _INTERRUPTED:
            sys.stdout.buffer.write(fixture)
            sys.stdout.buffer.flush()
            if load_state().get("videos", {}).get(video_id, {}).get("recording", {}).get("ended"):
                return _terminal_error_exit(recording) or 0
            time.sleep(0.2)
        return 1
    output = stem_path.parent / f"{stem}.mkv"
    partial = stem_path.parent / f"{stem}.mkv.part"
    mode = recording.get("mode", "live" if metadata.get("live_status") == "is_live" else "archive")
    if mode == "live":
        if "--live-from-start" in argv:
            video_part = stem_path.parent / f"{stem}.f134.mkv.part"
            audio_part = stem_path.parent / f"{stem}.f140.mkv.part"
            concurrent_index = argv.index("--concurrent-fragments") if "--concurrent-fragments" in argv else -1
            concurrent = int(argv[concurrent_index + 1]) if concurrent_index >= 0 else 1
            fragment_files: list[Path] = []
            start_offset = float(recording.get("start_offset_seconds", 0))
            fixture = _media_fixture(start_offset)

            if concurrent > 1:
                fragment_files = [
                    stem_path.parent / f"{stem}.f134.mkv.part-Frag{index}"
                    for index in range(1, 3)
                ] + [
                    stem_path.parent / f"{stem}.f140.mkv.part-Frag{index}"
                    for index in range(1, 3)
                ]

            def write_live_payload() -> None:
                if concurrent > 1:
                    for fragment in fragment_files:
                        with fragment.open("ab") as stream:
                            stream.write(fixture)
                    video_part.touch(exist_ok=True)
                    audio_part.touch(exist_ok=True)
                    return
                for part in (video_part, audio_part):
                    with part.open("ab") as stream:
                        stream.write(fixture)

            def finalize_from_start() -> int:
                command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                           "-i", str(video_part), "-i", str(audio_part), "-map", "0:v?", "-map", "1:a?",
                           "-c", "copy", "-avoid_negative_ts", "make_zero", str(output)]
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0 and output.is_file() and output.stat().st_size:
                    video_part.unlink(missing_ok=True)
                    audio_part.unlink(missing_ok=True)
                    return 0
                return 1

            started = time.monotonic()
            while not _INTERRUPTED:
                write_live_payload()
                state = load_state().get("videos", {}).get(video_id, {})
                if state.get("recording", {}).get("ended"):
                    if concurrent > 1:
                        for part in (video_part, audio_part):
                            with part.open("ab") as stream:
                                stream.write(fixture)
                        for fragment in fragment_files:
                            fragment.unlink(missing_ok=True)
                    result = finalize_from_start()
                    return (_terminal_error_exit(recording) or 0) if result == 0 else result
                if recording.get("early_exit") and time.monotonic() - started >= float(recording.get("early_exit_after", 0)):
                    return 0
                time.sleep(0.2)
            if recording.get("interrupt_behavior", "leave_part") == "finalize":
                if concurrent > 1:
                    for part in (video_part, audio_part):
                        with part.open("ab") as stream:
                            stream.write(fixture)
                    for fragment in fragment_files:
                        fragment.unlink(missing_ok=True)
                return finalize_from_start()
            return 1
        started = time.monotonic()
        while not _INTERRUPTED:
            with partial.open("ab") as stream:
                stream.write(fixture)
            state = load_state().get("videos", {}).get(video_id, {})
            if state.get("recording", {}).get("ended"):
                if recording.get("part_only_on_end"):
                    with partial.open("ab") as stream:
                        stream.write(fixture)
                else:
                    partial.replace(output)
                return _terminal_error_exit(recording) or 0
            if recording.get("early_exit") and time.monotonic() - started >= float(recording.get("early_exit_after", 0)):
                return 0
            time.sleep(0.2)
        if recording.get("interrupt_behavior", "leave_part") == "finalize" and partial.is_file():
            partial.replace(output)
            return 0
        return 1
    if recording.get("format_error") and "-f" in argv:
        return fail("Requested format is not available")
    if recording.get("fail"):
        partial.write_bytes(fixture)
        return fail(recording.get("fail"))
    if partial.exists():
        with partial.open("ab") as stream:
            stream.write(fixture)
        partial.replace(output)
    else:
        output.write_bytes(fixture)
    return 0


def main(argv: list[str]) -> int:
    log_call(argv)
    if argv == ["--version"]:
        print("fake-yt-dlp 1.0")
        return 0
    state = load_state()
    url = next((arg for arg in reversed(argv) if arg.startswith(("https://", "http://"))), "")
    if "-o" in argv and "--dump-single-json" not in argv:
        video_id = parse_qs(urlsplit(url).query).get("v", [""])[0]
        video = state.get("videos", {}).get(video_id, {})
        return _recording(argv, video, video_id)
    if "--flat-playlist" in argv and "--dump-single-json" in argv:
        channels = state.get("channels", {})
        key = urlsplit(url).path.rstrip("/").split("/")[-2] if urlsplit(url).path.endswith("/streams") else urlsplit(url).path.rstrip("/").split("/")[-1]
        config = channels.get(key) or channels.get(url) or channels.get(urlsplit(url).path) or {}
        if not config and urlsplit(url).path == "/watch":
            video_key = parse_qs(urlsplit(url).query).get("v", [""])[0]
            video = state.get("videos", {}).get(video_key, {})
            if video.get("channel_id"):
                config = {"channel_id": video["channel_id"]}
        if not config and urlsplit(url).hostname == "youtu.be":
            video_key = urlsplit(url).path.strip("/")
            video = state.get("videos", {}).get(video_key, {})
            if video.get("channel_id"):
                config = {"channel_id": video["channel_id"]}
        if not config:
            video_key = urlsplit(url).path.rstrip("/").split("/")[-1]
            video = state.get("videos", {}).get(video_key, {})
            if video.get("channel_id"):
                config = {"channel_id": video["channel_id"]}
        if config.get("sleep"):
            time.sleep(float(config["sleep"]))
        if config.get("error"):
            return fail(config["error"])
        entries = []
        for entry in config.get("entries", []):
            # Flat playlist data intentionally omits timestamp/release_timestamp/thumbnail.
            entries.append({key: value for key, value in entry.items() if key not in ("timestamp", "release_timestamp", "thumbnail")})
        output = {key: config[key] for key in ("channel", "channel_id", "channel_follower_count", "channel_video_count", "channel_view_count") if key in config}
        output["entries"] = entries
        print(json.dumps(output, ensure_ascii=False))
        return 0

    if "--dump-single-json" in argv and "--skip-download" in argv:
        videos = state.get("videos", {})
        video_id = parse_qs(urlsplit(url).query).get("v", [""])[0]
        video = videos.get(video_id, {"live_status": "was_live", "duration": 3600})
        if video.get("sleep"):
            time.sleep(float(video["sleep"]))
        if video.get("error"):
            return fail(video["error"])
        if video.get("status") in ("private", "deleted", "unavailable", "members_only", "rate_limited"):
            return fail(video["status"])
        if video.get("live_status") == "is_upcoming" and "--ignore-no-formats-error" not in argv:
            return fail("upcoming_error")
        output = {key: value for key, value in video.items() if key not in ("sleep", "error", "status")}
        if video.get("recording", {}).get("ended"):
            output["live_status"] = "was_live"
        print(json.dumps(output, ensure_ascii=False))
        return 0

    if "--list-formats" in argv:
        print("format code  extension  resolution")
        return 0
    return fail("unsupported fake yt-dlp arguments")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
