"""Transcript normalization, serialization, and faster-whisper adapter."""
import logging
import os
import re
import site
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_WINDOWS_CUDA_DLL_HANDLES = []
_WINDOWS_CUDA_DLL_PATHS = set()


def _supports_windows_dll_registration():
    return os.name == "nt" and hasattr(os, "add_dll_directory")


def register_windows_cuda_dlls():
    """Expose NVIDIA pip-wheel DLLs before CTranslate2 is imported on Windows."""
    if not _supports_windows_dll_registration():
        return

    site_packages = set()
    try:
        site_packages.update(Path(path) for path in site.getsitepackages())
    except (AttributeError, OSError):
        pass
    site_packages.add(Path(sys.prefix) / "Lib" / "site-packages")

    dll_dirs = []
    for root in site_packages:
        nvidia = root / "nvidia"
        for component in ("cublas", "cudnn", "cuda_runtime"):
            for folder in ("bin", "lib", "lib/x64"):
                candidate = nvidia / component / folder
                if candidate.is_dir():
                    dll_dirs.append(str(candidate.resolve()))

    path_entries = {part.casefold() for part in os.environ.get("PATH", "").split(os.pathsep) if part}
    for dll_dir in dll_dirs:
        key = os.path.normcase(dll_dir)
        if key not in _WINDOWS_CUDA_DLL_PATHS:
            try:
                _WINDOWS_CUDA_DLL_HANDLES.append(os.add_dll_directory(dll_dir))
                _WINDOWS_CUDA_DLL_PATHS.add(key)
            except OSError as exc:
                log.warning("Could not register CUDA DLL directory %s (%s)", dll_dir, type(exc).__name__)
        if dll_dir.casefold() not in path_entries:
            os.environ["PATH"] = os.pathsep.join((os.environ.get("PATH", ""), dll_dir)).strip(os.pathsep)
            path_entries.add(dll_dir.casefold())


def preprocess_segments(segments):
    """Conservative cleanup; keep timestamps and raw rows are stored separately."""
    candidates = []
    previous = None
    for item in segments:
        text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if not text:
            continue
        # Remove only obvious non-speech fragments; retain short meaningful words.
        if len(text) <= 1 and not re.search(r"[\w\u3040-\u30ff\u4e00-\u9fff]", text):
            continue
        if text == previous:
            continue
        else:
            previous = text
        candidates.append({"start": float(item["start"]), "end": float(item["end"]), "text": text})
    output = []
    i = 0
    while i < len(candidates):
        loop_width = 0
        repeats = 0
        for width in (1, 2, 3):
            block = candidates[i:i + width]
            if len(block) != width:
                continue
            count = 1
            pattern = [x["text"] for x in block]
            while [x["text"] for x in candidates[i + count * width:i + (count + 1) * width]] == pattern:
                count += 1
            if count >= 3:
                loop_width, repeats = width, count
                break
        if loop_width:
            output.extend(candidates[i:i + loop_width * 2])
            i += loop_width * repeats
        else:
            output.append(candidates[i])
            i += 1
    return output


def to_webvtt(segments):
    def stamp(value):
        ms = round(float(value) * 1000); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02}.{ms:03}"
    return "WEBVTT\n\n" + "\n\n".join(f"{i}\n{stamp(x['start'])} --> {stamp(x['end'])}\n{x['text']}" for i, x in enumerate(segments, 1)) + "\n"


def to_srt(segments):
    def stamp(value):
        ms = round(float(value) * 1000); h, ms = divmod(ms, 3_600_000); m, ms = divmod(ms, 60_000); s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    return "\n\n".join(f"{i}\n{stamp(x['start'])} --> {stamp(x['end'])}\n{x['text']}" for i, x in enumerate(segments, 1)) + "\n"


def load_whisper_model(*, model_name, device, compute_type):
    """Lazy import keeps normal app startup independent of model downloads/runtime."""
    register_windows_cuda_dlls()
    from faster_whisper import WhisperModel
    actual_device = device
    actual_compute = compute_type
    if device == "auto":
        cuda_available = False
        try:
            import ctranslate2
            cuda_available = bool(ctranslate2.get_cuda_device_count())
        except Exception:
            pass
        if cuda_available:
            try:
                return WhisperModel(model_name, device="cuda", compute_type=compute_type)
            except Exception as exc:
                log.warning("CUDA Whisper initialization failed; retrying with CPU (%s)", type(exc).__name__)
        actual_device = "cpu"
    if actual_device == "cpu" and compute_type == "auto":
        actual_compute = "int8"
    return WhisperModel(model_name, device=actual_device, compute_type=actual_compute)


def transcribe_audio(model, audio_path, *, language=None, timestamp_offset=0):
    """Transcribe a single durable chunk and offset all timestamps to video time."""
    rows, info = model.transcribe(str(audio_path), language=language or None, vad_filter=True)
    return [{"start": s.start + timestamp_offset, "end": s.end + timestamp_offset, "text": s.text} for s in rows], getattr(info, "language", None)
