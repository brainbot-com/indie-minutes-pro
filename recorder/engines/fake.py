"""A CPU-only stand-in for tests and for trying the pipeline without a GPU. It
does no recognition: if <wav>.ref.json exists (words with times and speakers),
it returns that, otherwise a fixed two-speaker text spread over the audio."""
from pathlib import Path

from ..audio import wav_duration
from ..util import read_json
from .base import Engine, finish

TEXT = ("Thanks for joining. Let's go through the release plan. "
        "I will send the updated deck by Friday. "
        "Good, then we agree to ship the beta next week. "
        "Who checks the open security issue? I can take that one.").split()


class FakeEngine(Engine):
    name = "fake"
    label = "Fake engine (no recognition)"

    def transcribe(self, wav, workdir, language=None, log=print):
        duration = wav_duration(wav)
        ref = read_json(Path(str(wav) + ".ref.json"))
        if ref:
            words = ref["words"]
        else:
            step = max(0.3, duration / max(1, len(TEXT)))
            words = [{"w": w, "s": i * step, "e": i * step + step * 0.8, "spk": f"s{(i // 8) % 2}"}
                     for i, w in enumerate(TEXT) if i * step < duration]
        return finish(words, language=language or "en", duration=duration,
                      engine={"name": self.name, "label": self.label, "backend": "none"})
