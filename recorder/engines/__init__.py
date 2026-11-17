"""Engine registry.

A-iq4, A-q8  Nemotron 3.5 Transcribe in NeMo-Speech.cpp (Vulkan), decoder in IQ4_NL or Q8_0
B            Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B + Nemotron 3 Diarization
fake         no recognition, for tests
"""
from .base import Engine, EngineError

NAMES = ("A-iq4", "A-q8", "B", "fake")


class Engines:
    def __init__(self, settings):
        self.s = settings
        self._cache = {}

    def get(self, name):
        if name not in self._cache:
            self._cache[name] = self._make(name)
        return self._cache[name]

    def _make(self, name):
        if name == "fake":
            from .fake import FakeEngine
            return FakeEngine()
        if name in ("A-iq4", "A-q8"):
            from .nemo import NemoEngine
            return NemoEngine(self.s, variant=name[2:])
        if name == "B":
            from .qwen import QwenEngine
            return QwenEngine(self.s)
        raise EngineError(f"unknown engine {name!r}, expected one of {', '.join(NAMES)}")


__all__ = ["Engine", "EngineError", "Engines", "NAMES"]
