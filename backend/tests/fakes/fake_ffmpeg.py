#!/usr/bin/env python3
"""Small tee/remux stand-in for recorder interruption tests."""
import os
from pathlib import Path
import signal
import sys

stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
args = sys.argv[1:]
if "-i" in args and args[args.index("-i") + 1] == "pipe:0" and os.environ.get("FAKE_FFMPEG_TEE_FAIL") == "1":
    raise SystemExit(1)
if "-i" in args and args[args.index("-i") + 1] != "pipe:0":
    source = Path(args[args.index("-i") + 1])
    if os.environ.get("FAKE_FFMPEG_REMUX_FAIL") == "1":
        raise SystemExit(1)
    Path(args[-1]).write_bytes(source.read_bytes())
    raise SystemExit(0)

outputs = args[-1].split("|")
transport_stream = Path(outputs[0].split("]", 1)[1])
hls_dir = Path(outputs[1].split("]", 1)[1]).parent
transport_stream.parent.mkdir(parents=True, exist_ok=True)
transport_stream.write_bytes(b"fake transport stream\n")
hls_dir.mkdir(parents=True, exist_ok=True)
(hls_dir / "segment_000000.ts").write_bytes(b"fake hls segment\n")
(hls_dir / "index.m3u8").write_text(
    "#EXTM3U\n#EXTINF:6.0,\nsegment_000000.ts\n", encoding="utf-8")
while not stopping:
    chunk = sys.stdin.buffer.read(65536)
    if not chunk:
        break
    with transport_stream.open("ab") as stream:
        stream.write(chunk[:1024])
