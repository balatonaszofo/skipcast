"""Configuration loading.

Every tunable that affects output lives in config.toml. This module reads it
and nothing else does. If you find yourself hardcoding a threshold somewhere,
put it here instead.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "config.toml"
ENV_FILENAME = ".env"


def load_env_file(path: Path | None = None) -> None:
    """Read KEY=value lines from .env into the environment.

    API keys cannot live in ~/.zshrc — launchd does not read shell profiles, so
    the background service would never see them — and a launchd plist is
    world-readable. A gitignored file next to config.toml is the one location
    that works for both the CLI and the service without exposing the secret.

    Existing environment variables always win, so an explicit export still
    overrides the file.
    """
    if path is None:
        here = Path(__file__).resolve()
        path = here.parent.parent.parent / ENV_FILENAME
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class FetchConfig:
    source_dir: str = "sources"
    format: str = "bestaudio/best"
    keep_original: bool = True


@dataclass
class DiarizeConfig:
    pipeline: str = "pyannote/speaker-diarization-3.1"
    device: str = "auto"
    num_speakers: int = 0
    min_speakers: int = 0
    max_speakers: int = 0
    sample_rate: int = 16000


@dataclass
class IdentityConfig:
    match_threshold: float = 0.70


@dataclass
class CutConfig:
    merge_gap_seconds: float = 1.5
    min_skip_seconds: float = 15.0
    boundary_padding_seconds: float = 0.25
    crossfade_seconds: float = 0.15
    max_skip_fraction: float = 0.5
    merge_adjacent_cuts: bool = True
    # Keep-only mode, used by person feeds. Its own minimum and ceiling: it
    # governs how much of everyone else goes, where the settings above govern
    # how much of one person goes, and the right numbers are different.
    keep_only_min_cut_seconds: float = 8.0
    keep_only_max_skip_fraction: float = 0.98


@dataclass
class ServeConfig:
    host: str = "0.0.0.0"
    port: int = 8730
    base_url: str = "http://localhost:8730"
    enable_ui: bool = True
    enable_search: bool = True


@dataclass
class PollConfig:
    max_episodes: int = 5
    max_failures: int = 3
    interval_hours: float = 6.0


@dataclass
class TranscribeConfig:
    enabled: bool = True
    model: str = "small"
    compute_type: str = "int8"
    language: str = "en"
    beam_size: int = 5


@dataclass
class SummaryConfig:
    enabled: bool = True
    provider: str = "gemini"
    model: str = ""          # blank resolves to the provider's default
    max_tokens: int = 16000
    thinking: str = "low"    # gemini: minimal|low|medium|high|default
    effort: str = "high"     # anthropic only
    scope: str = "original"


@dataclass
class InterstitialConfig:
    """Removing the parts that are not the show — ads, housekeeping, intros."""
    enabled: bool = True
    # Which kinds to act on. "banter" is deliberately not in the default: it is
    # the one the model is worst at, and the one where a false positive costs
    # actual content.
    remove: list[str] = field(
        default_factory=lambda: ["ad", "housekeeping", "intro", "outro"]
    )
    min_confidence: str = "likely"   # certain | likely | unsure
    # Refuse the lot if they add up to more than this share of the episode.
    # A podcast is not 40% advertising; a number that high means the model has
    # started marking the show itself.
    max_fraction: float = 0.4


@dataclass
class HighlightConfig:
    """Saving the moment you just heard.

    Capture is retroactive because you only know a passage was worth keeping
    after it has been said, so the button reaches backwards. The default
    reaches back further than the moment usually needs: trimming a clip down
    is easy and lossless, recovering a beginning that was never captured
    means finding the spot in the episode again.
    """
    enabled: bool = True
    lookback_seconds: float = 40.0
    # Ceiling on a single clip, before and after trimming. Long enough for an
    # exchange, short enough that a highlight stays an excerpt.
    max_seconds: float = 300.0
    # Two taps this close together are one moment noticed twice; the second
    # extends the first rather than making a near-duplicate.
    merge_window_seconds: float = 20.0


@dataclass
class EncodeConfig:
    bitrate: str = "128k"
    channels: int = 1


@dataclass
class Config:
    data_dir: Path = Path("data")
    fetch: FetchConfig = field(default_factory=FetchConfig)
    diarize: DiarizeConfig = field(default_factory=DiarizeConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    cut: CutConfig = field(default_factory=CutConfig)
    encode: EncodeConfig = field(default_factory=EncodeConfig)
    serve: ServeConfig = field(default_factory=ServeConfig)
    poll: PollConfig = field(default_factory=PollConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    interstitial: InterstitialConfig = field(default_factory=InterstitialConfig)
    highlight: HighlightConfig = field(default_factory=HighlightConfig)
    source_path: Path | None = None


def _candidate_paths() -> list[Path]:
    """Where to look for config.toml, most specific first."""
    here = Path(__file__).resolve()
    # src/skipcast/config.py -> project root is two levels up from src/
    project_root = here.parent.parent.parent
    return [
        Path.cwd() / CONFIG_FILENAME,
        project_root / CONFIG_FILENAME,
        Path.home() / ".config" / "skipcast" / CONFIG_FILENAME,
    ]


def _fill(cls, table: dict):
    """Build a dataclass from a TOML table, ignoring unknown keys."""
    known = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in table.items() if k in known})


def load_config(explicit: Path | None = None) -> Config:
    load_env_file()
    path = None
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
    else:
        for candidate in _candidate_paths():
            if candidate.is_file():
                path = candidate.resolve()
                break

    if path is None:
        # Defaults are usable; you only lose the ability to tune.
        return Config()

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    cfg = Config(
        fetch=_fill(FetchConfig, raw.get("fetch", {})),
        diarize=_fill(DiarizeConfig, raw.get("diarize", {})),
        identity=_fill(IdentityConfig, raw.get("identity", {})),
        cut=_fill(CutConfig, raw.get("cut", {})),
        encode=_fill(EncodeConfig, raw.get("encode", {})),
        serve=_fill(ServeConfig, raw.get("serve", {})),
        poll=_fill(PollConfig, raw.get("poll", {})),
        transcribe=_fill(TranscribeConfig, raw.get("transcribe", {})),
        summary=_fill(SummaryConfig, raw.get("summary", {})),
        interstitial=_fill(InterstitialConfig, raw.get("interstitial", {})),
        highlight=_fill(HighlightConfig, raw.get("highlight", {})),
        source_path=path,
    )

    data_dir = Path(raw.get("paths", {}).get("data_dir", "data")).expanduser()
    # Relative data_dir is relative to the config file, not the cwd, so that
    # running skipcast from anywhere writes to the same place.
    cfg.data_dir = data_dir if data_dir.is_absolute() else (path.parent / data_dir)
    return cfg


def hf_token() -> str | None:
    """The pyannote models are gated.

    Environment first, then whatever `huggingface-cli login` cached, so either
    way of setting it up works. The token is never written to any file we
    produce and never logged.
    """
    for var in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:  # noqa: BLE001 — an optional convenience, never fatal
        return None
