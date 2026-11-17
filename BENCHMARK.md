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

### Accuracy and speed

<!-- RESULTS-TABLE -->

Distant mic, where the configurations differ (pooled over the ten meetings):

| | WER: sub / del / ins | DER: missed / false alarm / confusion |
|---|---|---|
| A-iq4 | 7.4 / 4.3 / 2.3 | 16.2 / 8.3 / 2.8 |
| A-q8 | 7.3 / 4.3 / 2.3 | 16.1 / 7.9 / 2.7 |
| A-iq4-diar | 11.6 / 18.8 / 2.6 | 42.8 / 2.0 / 1.1 |
| B | 12.0 / 21.9 / 7.0 | 39.4 / 7.6 / 1.8 |

- Engine A matches NVIDIA's published AMI numbers for the full-precision model
  (cpWER 17.3 % SDM, 10.3 % IHM): IQ4_NL loses nothing measurable against
  Q8_0, needs 22 GiB of GTT instead of 36 and is about 10 % faster.
- Engine B loses most of its accuracy to deletions: Qwen3-ASR leaves out
  overlapping speech and backchannels on the distant mic, which also leaves
  speech regions without words (DER missed). Where it does have words, its
  speakers are the cleanest (confusion 1.8 %), because they come from a
  dedicated diarizer.
- A-iq4-diar (engine A without `--diarize`, speakers from Nemotron 3
  Diarization) was a try at combining the two. It is worse on every count:
  without the speaker prompt the model transcribes far fewer words in
  crosstalk (EN2002a: 3,706 against 7,179) and many word timestamps move
  (TS3003c: 68 % of the matching words more than 2 s from where the
  `--diarize` run puts them), so the diarizer's turns land on the wrong words.
  The run was stopped after the distant-mic set; `--diarize` stays.
- Per meeting, A-iq4's cpWER on the distant mic ranges from 9 % (TS3003b) to
  43 % (ES2004a, the kick-off meeting full of crosstalk and laughter).
- MLS German is read speech without overlap: a sanity check that German works
  end to end, not a meeting benchmark.

### Where the time goes

Seconds per audio hour on the distant-mic set:

| | Load | Recognition | Alignment | Diarization | Total | RTF |
|---|---:|---:|---:|---:|---:|---:|
| A-iq4 | 4 to 5 (warm page cache) | rest of 297 | in the model | in the model | 297 | 0.082 |
| A-q8 | 11 (warm page cache) | rest of 327 | in the model | in the model | 327 | 0.091 |
| B | 7 | 971 | 17 | 52 | 1040 | 0.29 |

- Engine A is one speech-LLM pass (encoder, decoder and the CTC timestamp
  head); NeMo-Speech.cpp 0.3.0 reports no encode/prefill/decode split for it.
  `nemo-speech bench asr` on IS1009a (14 min, transcription only) separates
  load from processing: IQ4_NL 4.3 s load and 45.4 s processing (18.5x real
  time), Q8_0 11.2 s and 50.0 s (16.8x). The speaker branch of `--diarize`
  adds the rest.
- Engine B on ES2004a alone, before the benchmark: RTF 0.19 (ASR 184 s for
  17.5 min). In the benchmark run its ASR was slower (RTF 0.27 for ASR); the
  GPU was shared with the box's Ollama, which kept serving its other clients.
  Without the ROCm settings in compose.yaml (AOTriton attention, MIOpen FAST
  find) it ran at RTF 0.56.
- Peak extra GTT while a job runs: A-iq4 22.3 GiB, A-q8 36.0 GiB, B 3.4 GiB.
  Process RSS stays below 2 GiB: the weights live in GTT.

### The GPU is really used

The same 2-minute clip (ES2004a, distant mic) on the GPU and on the CPU (32
cores), wall time including model load:

| | GPU | CPU | Factor |
|---|---:|---:|---:|
| A-iq4 (Vulkan) | 15.1 s | 63.2 s | 4.2x |
| B (ROCm + Vulkan diarizer) | 15.1 s | 424.3 s | 28x |

During every GPU job GTT use rose by the model size (A-iq4 19 to 22 GiB) and
`gpu_busy_percent` averaged 90 to 100 %; on the CPU runs it did not move.

### Box impact: the main LLM while a job runs

qwen3.8:27b-ctx65536 on the box's Ollama, a short question with 48 answer
tokens once a minute (`data/bench/runs/llm-probe.jsonl`):

| GPU doing | Probes | Time to first token (median, p90) | Tokens/s (median) | Answer time (median) |
|---|---:|---|---:|---:|
| nothing | 7 | 0.17 s, 0.20 s | 17.4 | 3.0 s |
| engine A IQ4_NL | 59 | 0.23 s, 0.32 s | 12.8 | 4.0 s |
| engine A Q8_0 | 57 | 0.23 s, 0.26 s | 12.5 | 4.1 s |
| engine B | <!-- B-PROBES --> | 0.23 s, 0.25 s | 12.2 | 4.2 s |

Chat answers come about 30 % slower while a transcription runs; no request
failed. Long prompts (prefill) were not measured.

### More than one hour

ES2004b and ES2004c joined into one 78-minute file (the same four people):
engine A cut it into two windows overlapping by 60 s, linked all four
speakers through 206 words both windows heard, and created no new labels;
8.3 minutes in total (RTF 0.106). The transcript carries
`speaker_scope: per_chunk` and a note.

<!-- SPOTCHECK -->

## Recommendation

Make **engine A with the AD-IQ4_NL decoder (`A-iq4`)** the default:

- best accuracy where it matters, a phone on the table: cpWER 18.1 % against
  47.4 % for engine B on the AMI distant mic, and the same as Q8_0;
- about 5 minutes per hour of audio, three to four times faster than B;
- 22 GiB of GTT while it runs (Q8_0 needs 36 GiB), which leaves room for the
  box's LLM and embedding models (102 GiB GTT in total).

Keep engine B as the fallback when GPU memory is short (3.4 GiB) or when
punctuated text matters more than completeness; engine A with `--diarize`
writes lowercase text without punctuation. Do not use Q8_0: it costs 14 GiB
more for no measurable gain.
