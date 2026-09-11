"""PipeWire capture (mic + system audio) and PCM helpers."""
from __future__ import annotations

import array
import asyncio
import io
import shutil
import wave

RATE = 16000
BYTES_PER_SEC = RATE * 2  # s16 mono


def pipewire_available() -> bool:
    return shutil.which("pw-record") is not None


class PwCapture:
    """Streams raw s16/16k/mono PCM from PipeWire (pw-record → stdout).

    sink=False -> default source (your microphone)
    sink=True  -> monitor of the default sink (what you hear: the other side of the call)
    """

    def __init__(self, sink: bool):
        self.sink = sink
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        args = ["pw-record", "--raw", "--format=s16", f"--rate={RATE}", "--channels=1",
                "-P", "{ application.name = muesli }"]
        if self.sink:
            args += ["-P", "{ stream.capture.sink = true }"]
        args.append("-")
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )

    async def read(self, n: int = 8192) -> bytes:
        assert self.proc and self.proc.stdout
        return await self.proc.stdout.read(n)

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                self.proc.kill()


def pcm_to_wav(pcm: bytes, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def interleave_stereo(left: bytes, right: bytes) -> bytes:
    n = min(len(left), len(right)) // 2
    lo = array.array("h"); lo.frombytes(left[: n * 2])
    ro = array.array("h"); ro.frombytes(right[: n * 2])
    out = array.array("h", [0] * (n * 2))
    out[0::2] = lo
    out[1::2] = ro
    return out.tobytes()


def peak(pcm: bytes) -> int:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return 0
    return max(abs(min(samples)), abs(max(samples)))
