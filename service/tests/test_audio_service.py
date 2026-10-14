"""Behaviour tests for ``tts_speak_model``.

Asserts the concurrency contract: generation runs in parallel (one output file
per request) while playback is serialized (one clip at a time). The external
``llama-tts.exe`` and ``powershell`` processes are replaced by a fake so the
contract can be checked deterministically.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path

import app.services.audio as audio_service


class _Recorder:
    """Captures output paths and the peak playback concurrency."""

    def __init__(self) -> None:
        self.gen_outputs: list[str] = []
        self.playback_active = 0
        self.playback_max = 0
        self._lock = threading.Lock()


def _make_fake_run(recorder: _Recorder):
    def fake_run(cmd, capture_output=True, text=True):
        program = cmd[0]
        if program.endswith("llama-tts.exe"):
            output = cmd[cmd.index("--output") + 1]
            recorder.gen_outputs.append(output)
            time.sleep(0.05)  # keep both generations overlapping
            Path(output).write_bytes(b"RIFF")  # pretend a wav was produced
        else:  # powershell playback
            with recorder._lock:
                recorder.playback_active += 1
                recorder.playback_max = max(
                    recorder.playback_max, recorder.playback_active
                )
            time.sleep(0.05)  # clip duration
            with recorder._lock:
                recorder.playback_active -= 1
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    return fake_run


async def test_generation_parallel_and_playback_serialized(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_service, "LLAMA_DIR", str(tmp_path))
    recorder = _Recorder()
    monkeypatch.setattr(audio_service.subprocess, "run", _make_fake_run(recorder))

    outputs = await asyncio.gather(
        audio_service.tts_speak_model("uno"),
        audio_service.tts_speak_model("dos"),
    )

    assert outputs == ["ok", "ok"]
    # Per-request output files: distinct paths, no clobbering.
    assert len(recorder.gen_outputs) == 2
    assert len(set(recorder.gen_outputs)) == 2
    # Playback never overlapped.
    assert recorder.playback_max == 1
    # Temp directories are cleaned up.
    assert list(tmp_path.glob("tts-*")) == []
