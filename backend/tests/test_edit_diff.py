import numpy as np
import pytest

from app.services.edit_diff import RATE, analyze, envelope_from_pcm


def _speech_like(seconds: float, seed: int) -> np.ndarray:
    """Log-energy envelope with irregular loud/quiet stretches, like a stream's audio."""
    rng = np.random.default_rng(seed)
    count = int(seconds * RATE)
    steps = rng.normal(0, 2.5, count)
    walk = np.convolve(steps, np.ones(5) / 5, mode="same").cumsum()
    walk -= np.convolve(walk, np.ones(301) / 301, mode="same")
    pauses = rng.random(count) < 0.04
    envelope = np.clip(-30 + walk, -60, 0)
    envelope[pauses] = -60
    return envelope


def _reencode(envelope: np.ndarray, seed: int = 99) -> np.ndarray:
    """YouTube re-encodes: a constant gain shift plus small per-window noise."""
    rng = np.random.default_rng(seed)
    return envelope + 3.0 + rng.normal(0, 0.6, envelope.size)


def _cut(envelope: np.ndarray, start: float, end: float) -> np.ndarray:
    return np.concatenate([envelope[:int(start * RATE)], envelope[int(end * RATE):]])


def _seconds(regions, key="start"):
    return [round(region[key]) for region in regions]


def test_unedited_archive_has_no_differences():
    local = _speech_like(1800, 1)
    result = analyze(local, _reencode(local))
    assert result["removed"] == []
    assert result["added"] == []
    assert result["matched_ratio"] > 0.95


def test_middle_cut_is_located_in_local_and_remote_time():
    local = _speech_like(3600, 2)
    remote = _reencode(_cut(local, 1000, 1300))
    result = analyze(local, remote)
    assert len(result["removed"]) == 1
    cut = result["removed"][0]
    assert cut["start"] == pytest.approx(1000, abs=1.5)
    assert cut["end"] == pytest.approx(1300, abs=1.5)
    # YouTube 版で、カットされた箇所がどこにつながっているか。
    assert cut["remote_at"] == pytest.approx(1000, abs=1.5)
    assert result["added"] == []


def test_waiting_screen_trim_at_the_head_is_a_removed_region():
    local = _speech_like(2400, 3)
    result = analyze(local, _reencode(local[600 * RATE:]))
    assert len(result["removed"]) == 1
    assert result["removed"][0]["start"] == pytest.approx(0, abs=1.5)
    assert result["removed"][0]["end"] == pytest.approx(600, abs=1.5)
    assert result["added"] == []


def test_several_cuts_are_all_reported_in_order():
    local = _speech_like(3600, 4)
    remote = _reencode(_cut(_cut(local, 2500, 2560), 400, 700))
    result = analyze(local, remote)
    assert _seconds(result["removed"]) == pytest.approx([400, 2500], abs=2)
    assert _seconds(result["removed"], "end") == pytest.approx([700, 2560], abs=2)
    assert result["removed"][1]["remote_at"] == pytest.approx(2200, abs=2)


def test_content_only_in_remote_is_an_added_region():
    local = _speech_like(1800, 5)
    insert = _speech_like(120, 50)
    remote = _reencode(np.concatenate([local[:900 * RATE], insert, local[900 * RATE:]]))
    result = analyze(local, remote)
    assert result["removed"] == []
    assert len(result["added"]) == 1
    added = result["added"][0]
    assert added["start"] == pytest.approx(900, abs=1.5)
    assert added["end"] == pytest.approx(1020, abs=1.5)
    assert added["local_at"] == pytest.approx(900, abs=1.5)


def test_replaced_section_is_both_removed_and_added():
    local = _speech_like(1800, 6)
    remote = local.copy()
    remote[600 * RATE:690 * RATE] = _speech_like(90, 60)
    result = analyze(local, _reencode(remote))
    assert len(result["removed"]) == 1 and len(result["added"]) == 1
    assert result["removed"][0]["start"] == pytest.approx(600, abs=2)
    assert result["removed"][0]["end"] == pytest.approx(690, abs=2)
    assert result["added"][0]["start"] == pytest.approx(600, abs=2)


def test_unrelated_audio_matches_nothing_without_failing():
    result = analyze(_speech_like(900, 7), _speech_like(900, 8))
    assert result["matched_ratio"] < 0.2
    assert result["removed"][0]["start"] == 0


def test_envelope_from_pcm_is_one_log_energy_value_per_tenth_of_a_second():
    sample_rate = 8000
    loud = np.full(sample_rate, 10000, dtype=np.int16)
    quiet = np.zeros(sample_rate, dtype=np.int16)
    envelope = envelope_from_pcm(np.concatenate([loud, quiet]), sample_rate)
    assert envelope.size == 2 * RATE
    assert envelope[:RATE].min() > envelope[RATE:].max() + 30
