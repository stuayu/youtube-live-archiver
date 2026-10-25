"""Learn recurring UTC live start periods from recent channel history."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Profile:
    samples: int
    mode: str
    levels: tuple[str, ...]

    def level_at(self, value: datetime) -> str:
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        index = value.weekday() * 24 + value.hour
        return self.levels[index]

    def next_hot_start(self, value: datetime) -> datetime | None:
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        boundary = value.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for offset in range(24 * 8):
            candidate = boundary + timedelta(hours=offset)
            if self.level_at(candidate) == "hot":
                return candidate
        return None


def _classify_shares(shares: list[float]) -> list[str]:
    levels = ["cold"] * len(shares)
    order = sorted(range(len(shares)), key=lambda i: (-shares[i], i))
    cumulative = 0.0
    for index in order:
        if shares[index] == 0:
            continue
        levels[index] = "hot" if cumulative < .70 else "warm" if cumulative < .95 else "cold"
        cumulative += shares[index]
    return levels


def build_profile(starts: list[datetime]) -> Profile:
    normalized = [(start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc))
                  for start in starts]
    samples = len(normalized)
    if samples < 8:
        return Profile(samples, "insufficient", ("normal",) * 168)
    mode = "weekhour" if samples >= 24 else "hour"
    count = 168 if mode == "weekhour" else 24
    bins = [0.0] * count
    for start in normalized:
        index = start.weekday() * 24 + start.hour if mode == "weekhour" else start.hour
        bins[index] += 1
    smoothed = [bins[(i - 1) % count] * .5 + bins[i] + bins[(i + 1) % count] * .5
                for i in range(count)]
    total = sum(smoothed)
    shares = [value / total for value in smoothed]
    levels = _classify_shares(shares)
    if mode == "hour":
        levels *= 7
    return Profile(samples, mode, tuple(levels))
