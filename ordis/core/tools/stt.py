"""Speech-to-text tool using faster-whisper + sounddevice microphone recording.

Auto-approved: reads from the microphone, transcribes locally, returns text.
No confirmation required — it only captures audio when explicitly invoked.
"""

from __future__ import annotations

import pathlib
import tempfile
import wave

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

_MODEL = None  # lazy-loaded once


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    return _MODEL


def speech_to_text(self, duration: float = 10.0, language: str | None = None) -> str:
    """Record from the default microphone and transcribe it.

    Args:
        duration: Seconds to record (max 60).
        language: Optional language code (e.g. "en", "id"). None = auto-detect.
    """
    duration = max(1.0, min(float(duration), 60.0))
    try:
        fs = 16000
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        return f"error: microphone recording failed: {e}"

    audio = np.squeeze(recording)
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
    try:
        with wave.open(str(tmp_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(fs)
            pcm = (audio * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        model = _model()
        segments, _ = model.transcribe(str(tmp_path), language=language, vad_filter=True)
        text = " ".join(seg.text for seg in segments).strip()
        return text or "(no speech detected)"
    except Exception as e:
        return f"error: transcription failed: {e}"
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
