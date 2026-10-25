"""Central yt-dlp format presets and profile resolution."""
PRESETS = [
    {"id": "original", "label": "オリジナル（自動選択）", "selector": "", "description": "yt-dlp に任せる。ライブの取りこぼしを抑えます。サイズ・形式は配信次第。"},
    {"id": "av1", "label": "AV1 優先 高画質", "selector": "bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b", "description": "AV1、VP9 の順に高画質を選択。容量を抑えやすい一方、再生・エンコード負荷が高め。"},
    {"id": "vp9", "label": "VP9 優先 高画質", "selector": "bv*[vcodec^=vp9]+ba/bv*+ba/b", "description": "VP9 を優先し高画質。AV1 より再生互換性が広めで、サイズは大きくなる場合があります。"},
    {"id": "h264", "label": "H.264 優先", "selector": "bv*[vcodec^=avc1]+ba/b[ext=mp4]/b", "description": "H.264 を優先。ブラウザや古い機器で再生しやすく、同解像度の AV1 より容量が大きめ。"},
    {"id": "1080p", "label": "1080p まで", "selector": "bv*[height<=1080]+ba/b[height<=1080]/b", "description": "最大 1080p。容量と画質のバランスを取りたい場合。"},
    {"id": "720p", "label": "720p まで", "selector": "bv*[height<=720]+ba/b[height<=720]/b", "description": "最大 720p。容量を抑えたい場合。"},
    {"id": "audio", "label": "音声のみ", "selector": "ba/b", "description": "音声のみ保存。映像は保存されません。"},
]
PRESET_MAP = {p["id"]: p for p in PRESETS}
DEFAULT_ARCHIVE = PRESET_MAP["av1"]["selector"]

def preset_selector(value):
    if value is None: return ""
    return PRESET_MAP.get(value, {}).get("selector", value)

def resolve_quality(video=None, channel=None, profile="live", global_value=None):
    """Return (selector, source), honoring video > rule > channel > global."""
    keys = (f"{profile}_quality", "quality")
    metadata = (getattr(video, "raw_metadata", None) or {}) if video else {}
    for key in keys:
        val = metadata.get(key)
        if val is not None: return preset_selector(val), "video"
    for key in keys:
        val = getattr(channel, key, None) if channel else None
        if val not in (None, ""): return preset_selector(val), "channel"
    if global_value is None: global_value = "" if profile == "live" else DEFAULT_ARCHIVE
    return preset_selector(global_value), "global"
