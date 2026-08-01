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
