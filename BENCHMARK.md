# Benchmark: engine A (IQ4_NL, Q8_0) vs engine B on aibox1

Reproduce with `bench/run.sh` on aibox1 (resumable; numbers below are from the
run on 2026-11-17). Raw results: `data/bench/results.json`, per-item
transcripts in `data/bench/runs/`, LLM probe in `data/bench/runs/llm-probe.jsonl`.

## Setup

- Box: aibox1, Ryzen AI Max+ 395, Radeon 8060S (gfx1151), 128 GB unified
  memory (102 GiB usable as GTT), Ubuntu 24.04.3, kernel 6.14.0-1013-oem.
  Other services kept running as usual (Ollama with the embedding and
  reranker models for ai-knowledge, Open WebUI, ComfyUI idle).
- Engine A: NeMo-Speech.cpp 0.3.0 (NVIDIA's prebuilt Vulkan archive), Mesa
  25.2.8 RADV, Nemotron 3.5 Transcribe 30B-A4B with the F16 encoder and the
  AtomicChat AD-IQ4_NL (19.7 GB) or Q8_0 (35.0 GB) decoder, `--diarize`,
  word timestamps, one sequence of 73,728 tokens (one hour).
- Engine B: Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B (qwen-asr 0.0.6, bf16)
  on torch 2.12.0a0+rocm7.13 (AMD TheRock gfx1151 wheels) with AOTriton
  attention and MIOpen FAST find; speakers from Nemotron 3 Diarization
  (q8_0 GGUF, `nemo-speech diarize`, Vulkan); words get the speaker of the
  turn they overlap most.
- One GPU job at a time (shared lock); timings are the engine's own wall time
  for the whole file, including model load.

## Data

| Set | Items | Audio | Reference |
|---|---|---|---|
| ami-sdm | 10 AMI test meetings (ES2004a-c, IS1009a-c, TS3003a-c, EN2002a), single distant mic (Array1-01), like a phone on the table | 5.1 h | AMI manual words (NXT 1.6.2) per speaker; BUT only_words RTTM |
| ami-ihm-mix | the same meetings, headset mix: the clean upper bound | 5.1 h | same |
| mls-de | 4 simulated German conversations (2, 2, 3, 4 speakers, about 6 min each) stitched from Multilingual LibriSpeech German test utterances with 0.25 to 0.9 s gaps | 0.4 h | exact (utterance text, speaker, times); sanity check only: read speech, no overlap |

AMI audio from the CC BY 4.0 Hugging Face mirror `diarizers-community/ami`
(the Edinburgh mirror refuses scripted downloads), references from AMI's
annotation package and BUT's AMI-diarization-setup. Contains material from the
AMI Meeting Corpus (University of Edinburgh et al.) and Multilingual
LibriSpeech, both CC BY 4.0; audio converted and transcripts normalized by us.

## Metrics

- WER: speaker-agnostic; reference utterances in time order against the
  hypothesis words in time order (jiwer).
- cpWER: concatenated minimum-permutation WER (meeteval 0.4.3), the usual
  speaker-attributed meeting metric.
- DER: pyannote.metrics, collar 0.25 s (pyannote's convention, 0.125 s either
  side), overlapping speech scored; hypothesis segments are the engine's words
  merged across gaps up to 0.5 s.
- Text normalization: Whisper's English normalizer for English (lowercase,
  no punctuation, numbers and spellings unified, fillers such as "uh", "um",
  "hmm" dropped), Whisper's basic normalizer for German.
- Pooled over each set: WER weighted by reference words, cpWER as total errors
  over total reference words, DER weighted by duration.

## Results

(filled in when the run finishes)
