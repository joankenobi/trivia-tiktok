"""Text-to-speech backends: pyttsx3 (local voices) and the llama-tts model."""

from __future__ import annotations
from dataclasses import dataclass
import asyncio
import os
import shutil
import subprocess
import tempfile
import threading

@dataclass
class AudioResponse:
    voice:str
    status:str = ""

@dataclass
class voice:
    id=str
    name=str
    languages=list[str]
    gender=str
    age=str

LLAMA_DIR="C:\\Users\\Public\\Documents\\Programacion\\_llama-b11351-bin-win-vulkan-x64"

# Playback must not overlap: two clips playing at once is garbled audio.
# A threading lock (not asyncio.Lock) is the right primitive here because the
# playback itself is a blocking OS subprocess running in a worker thread, and
# it stays valid across event loops.
_playback_lock = threading.Lock()

def tts_speak(text, voice_id = 2 ):
    # Imported lazily so the module (and the model-based endpoint) stay
    # importable on systems where the optional pyttsx3 backend is absent.
    from pyttsx3 import Engine

    # Local voice playback shares the model's playback lock, so no two clips
    # (from either endpoint) ever overlap on the speakers.
    with _playback_lock:
        engine = None
        try:
            engine = Engine()
            voices:list[voice] | list[object] = engine.getProperty("voices")
            print(voices[3].id)
            if not voices:
                raise(Exception("No voices available for TTS."))
            engine.setProperty("voice", voices[voice_id].id)
            engine.setProperty('rate', 150)
            engine.connect('started-word', print(True))
            engine.say(text)
            engine.runAndWait()
            return True
        finally:
            if engine:
                engine.stop()


async def tts_speak_model(text):
    # One output file per request: concurrent generations must not clobber a
    # shared path. Each call gets its own temp directory under LLAMA_DIR (the
    # same filesystem the llama-tts binary writes to).
    work_dir = tempfile.mkdtemp(prefix="tts-", dir=LLAMA_DIR)
    output_path = os.path.join(work_dir, "out.wav")

    gen_cmd = [
        f"{LLAMA_DIR}\\llama-tts.exe",
        "--model", f"{LLAMA_DIR}/models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf",
        "--mmproj", f"{LLAMA_DIR}/models/Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M/mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf",
        "-p", f"{text}",
        "--tts-lang", "spanish",
        "--output", output_path,
    ]

    def _play_blocking() -> None:
        # PlaySync() blocks until the clip ends, so holding the lock across the
        # whole call serializes playback: only one clip plays at a time.
        with _playback_lock:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(New-Object Media.SoundPlayer '{output_path}').PlaySync();",
                ],
                capture_output=True,
                text=True,
            )

    try:
        # Generation is blocking, so offload it; concurrent calls run in
        # parallel because each has its own output file.
        result = await asyncio.to_thread(
            subprocess.run, gen_cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"TTS failed: {result.stderr}")

        await asyncio.to_thread(_play_blocking)
        return result.stdout
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":

    asyncio.run(tts_speak_model("hola que hace desde python"))
