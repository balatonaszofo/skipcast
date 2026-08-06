"""Speaker diarization via pyannote.audio 3.1.

Produces a flat list of {start, end, speaker_label}. The labels here are
per-episode cluster ids (SPEAKER_00, SPEAKER_01, ...) with no meaning across
episodes — mapping them to real people is Phase 1's job.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path

from . import audio
from .config import Config, hf_token

# pyannote falls back to CPU for the handful of ops MPS lacks. Has to be set
# before torch is imported, which is why it lives at module scope rather than
# inside the function that needs it.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# pyannote/speechbrain still call torchaudio's pre-TorchCodec backend APIs,
# which torchaudio warns on every single call since it started removing them
# in 2.9. Sliding-window inference re-triggers this per window, so one
# diarization run prints it dozens of times and buries real errors under it.
# Cosmetic until pyannote itself migrates — safe to silence.
warnings.filterwarnings("ignore", message=r".*consolidated into TorchCodec.*",
                        category=UserWarning)


class DiarizationError(RuntimeError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    speaker_label: str

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class SpeakerStats:
    speaker_label: str
    total_seconds: float
    segment_count: int
    share: float  # fraction of total diarized speech, not of episode length


def _pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _allowlist_checkpoint_globals(torch) -> None:
    """Permit the handful of non-tensor types pyannote stores in checkpoints.

    All of these are plain metadata — a version string and the enums/dataclass
    describing what the model predicts. None of them execute anything on load.
    """
    import torch.torch_version
    from pyannote.audio.core.task import Problem, Resolution, Specifications

    safe = [torch.torch_version.TorchVersion, Specifications, Problem, Resolution]
    try:
        torch.serialization.add_safe_globals(safe)
    except AttributeError:  # torch < 2.6 has no allowlist and needs none
        pass


def load_pipeline(cfg: Config):
    token = hf_token()
    if not token:
        raise DiarizationError(
            "No HuggingFace token. The diarization model is gated:\n"
            "  1. Accept the terms at https://hf.co/pyannote/speaker-diarization-3.1\n"
            "  2. Accept the terms at https://hf.co/pyannote/segmentation-3.0\n"
            "     (both, with the same account — missing the second one is the\n"
            "      usual cause of a confusing failure later)\n"
            "  3. Either: export HF_TOKEN=hf_...\n"
            "     or:     uv run hf auth login"
        )

    import torch
    from huggingface_hub.utils import GatedRepoError
    from pyannote.audio import Pipeline

    # torch 2.6 flipped torch.load to weights_only=True. The pyannote
    # checkpoints carry a TorchVersion (a str subclass) in their metadata,
    # which is not on the default allowlist. Allowlisting that one benign
    # class keeps the unpickling protection on for everything else — better
    # than the usual advice of setting weights_only=False.
    _allowlist_checkpoint_globals(torch)

    # pyannote 3.x passes use_auth_token= to hf_hub_download, which
    # huggingface_hub 0.26+ silently drops (and 1.x rejects outright). Putting
    # the token in the environment makes the hub pick it up ambiently, which is
    # the one path that works across all of those versions.
    os.environ.setdefault("HF_TOKEN", token)

    try:
        pipeline = Pipeline.from_pretrained(cfg.diarize.pipeline, use_auth_token=token)
    except GatedRepoError as exc:
        raise DiarizationError(
            f"{cfg.diarize.pipeline} is gated and this account is not on the "
            "authorized list yet.\n"
            "Visit both of these while logged in, fill in the access form, and "
            "retry — approval is automatic:\n"
            "  https://hf.co/pyannote/speaker-diarization-3.1\n"
            "  https://hf.co/pyannote/segmentation-3.0\n"
            "Being able to see a repo is not the same as being granted it."
        ) from exc

    if pipeline is None:
        # from_pretrained swallows some failures and returns None instead.
        raise DiarizationError(
            f"{cfg.diarize.pipeline} could not be loaded. Most likely the terms "
            "have not been accepted for this account on both gated repos:\n"
            "  https://hf.co/pyannote/speaker-diarization-3.1\n"
            "  https://hf.co/pyannote/segmentation-3.0"
        )

    device = _pick_device(cfg.diarize.device)
    print(f"[diarize] device: {device}", file=sys.stderr)
    pipeline.to(torch.device(device))
    return pipeline


def _speaker_kwargs(cfg: Config) -> dict:
    d = cfg.diarize
    if d.num_speakers:
        return {"num_speakers": d.num_speakers}
    kwargs = {}
    if d.min_speakers:
        kwargs["min_speakers"] = d.min_speakers
    if d.max_speakers:
        kwargs["max_speakers"] = d.max_speakers
    return kwargs


def diarize_file(path: Path, cfg: Config, meta: dict | None = None) -> dict:
    """Run diarization on an audio file and return the segments document."""
    audio.require_ffmpeg()
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    duration = audio.duration_seconds(path)
    print(
        f"[diarize] {path.name} ({duration / 60:.1f} min) — this runs slower than "
        "realtime, expect several minutes per hour of audio",
        file=sys.stderr,
    )

    pipeline = load_pipeline(cfg)

    from pyannote.audio.pipelines.utils.hook import ProgressHook

    with tempfile.TemporaryDirectory(prefix="skipcast-") as tmp:
        wav = audio.to_wav(path, Path(tmp) / "audio.wav", cfg.diarize.sample_rate)
        try:
            with ProgressHook() as hook:
                # The pipeline computes speaker embeddings anyway — it cannot
                # cluster without them — so ask for them rather than paying for
                # a second model pass in Phase 1.
                result = pipeline(
                    str(wav), hook=hook, return_embeddings=True, **_speaker_kwargs(cfg)
                )
        except Exception as exc:  # noqa: BLE001 — re-raised with actionable advice
            device = _pick_device(cfg.diarize.device)
            hint = ""
            if device == "mps":
                hint = (
                    "\n\nThis ran on the Apple GPU (mps). If it looks like a torch/backend "
                    'error rather than a bad file, set device = "cpu" in config.toml and retry.'
                )
            raise DiarizationError(f"diarization failed: {exc}{hint}") from exc

    annotation, embeddings = result if isinstance(result, tuple) else (result, None)

    segments = [
        Segment(round(turn.start, 3), round(turn.end, 3), str(label))
        for turn, _, label in annotation.itertracks(yield_label=True)
    ]
    segments.sort(key=lambda s: (s.start, s.end))

    return build_document(
        path, duration, segments, cfg, meta, _embedding_map(annotation, embeddings)
    )


def _embedding_map(annotation, embeddings) -> dict[str, list[float]]:
    """Pair each cluster label with its embedding.

    pyannote returns embeddings row-aligned with annotation.labels(). Rows can
    be all-NaN for a cluster with too little clean speech to embed; those are
    dropped rather than stored, since a NaN profile would poison every later
    similarity comparison.
    """
    if embeddings is None:
        return {}

    import numpy as np

    out = {}
    for i, label in enumerate(annotation.labels()):
        if i >= len(embeddings):
            break
        vec = np.asarray(embeddings[i], dtype=float)
        if vec.size == 0 or not np.isfinite(vec).all():
            print(f"[diarize] no usable embedding for {label}, skipping", file=sys.stderr)
            continue
        out[str(label)] = [float(x) for x in vec]
    return out


def summarize(segments: list[Segment]) -> list[SpeakerStats]:
    totals: dict[str, list[float]] = {}
    for seg in segments:
        entry = totals.setdefault(seg.speaker_label, [0.0, 0])
        entry[0] += seg.duration
        entry[1] += 1

    speech = sum(v[0] for v in totals.values()) or 1.0
    stats = [
        SpeakerStats(label, round(total, 2), count, round(total / speech, 4))
        for label, (total, count) in totals.items()
    ]
    stats.sort(key=lambda s: s.total_seconds, reverse=True)
    return stats


def build_document(
    audio_path: Path,
    duration: float,
    segments: list[Segment],
    cfg: Config,
    meta: dict | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> dict:
    """`meta` is the fetch metadata, when the audio came from a URL."""
    stats = summarize(segments)
    embeddings = embeddings or {}
    doc = {
        "schema": 1,
        "audio_file": audio_path.name,
        "audio_path": str(audio_path),
        "title": (meta or {}).get("title") or audio_path.name,
        "source_url": (meta or {}).get("source_url"),
        "uploader": (meta or {}).get("uploader"),
        "duration": round(duration, 3),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "pipeline": cfg.diarize.pipeline,
        "speakers": [
            {**asdict(s), "embedding": embeddings.get(s.speaker_label)} for s in stats
        ],
        "segments": [asdict(s) for s in segments],
    }
    return doc


def segments_from_document(doc: dict) -> list[Segment]:
    return [
        Segment(float(s["start"]), float(s["end"]), str(s["speaker_label"]))
        for s in doc["segments"]
    ]
