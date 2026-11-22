"""Engine registry.

A-iq4, A-q8  Nemotron 3.5 Transcribe in NeMo-Speech.cpp (Vulkan), decoder in IQ4_NL or Q8_0
A-iq4-diar   the same IQ4_NL words, speakers from Nemotron 3 Diarization
B            Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B + Nemotron 3 Diarization
fake         no recognition, for tests
"""
from .base import Engine, EngineError

NAMES = ("A-iq4", "A-q8", "A-iq4-diar", "B", "fake")


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
        if name in ("A-iq4", "A-q8", "A-iq4-diar"):
            from .nemo import NemoEngine
            return NemoEngine(self.s, variant=name.split("-")[1], external_diarizer=name.endswith("-diar"))
        if name == "B":
            from .qwen import QwenEngine
            return QwenEngine(self.s)
        raise EngineError(f"unknown engine {name!r}, expected one of {', '.join(NAMES)}")


__all__ = ["Engine", "EngineError", "Engines", "NAMES"]
