"""Transcript normalization, serialization, and faster-whisper adapter."""
import re


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
    from faster_whisper import WhisperModel
    actual_device = device
    actual_compute = compute_type
    if device == "auto":
        try:
            import ctranslate2
            actual_device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
        except Exception:
            actual_device = "cpu"
    if actual_device == "cpu" and compute_type == "auto":
        actual_compute = "int8"
    return WhisperModel(model_name, device=actual_device, compute_type=actual_compute)


def transcribe_audio(model, audio_path, *, language=None, timestamp_offset=0):
    """Transcribe a single durable chunk and offset all timestamps to video time."""
    rows, info = model.transcribe(str(audio_path), language=language or None, vad_filter=True)
    return [{"start": s.start + timestamp_offset, "end": s.end + timestamp_offset, "text": s.text} for s in rows], getattr(info, "language", None)
