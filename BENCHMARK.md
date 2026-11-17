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

Our own recordings stay on aibox1 and are reported only as numbers:

- own: whole recordings without a human reference (so far a 15-minute
  excerpt of a retreat session recorded with a phone, 5 speakers). Reported:
  speed, memory, speakers found, and how far the engines agree with engine A
  (agreement, not accuracy).
- own-ref: 5-minute excerpts with a reference a person corrected
  (`bench/excerpts.py cut` and `import`). The drafts come from engine A, so a
  reference corrected from them can lean towards A; correct while listening.
  The Drive call recordings join both sets once they are on the box
  (`bench/drive_inbox.py`).

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

| Set | Engine | Items | Audio h | WER | cpWER | DER | Wall s per audio h | RTF | Peak GTT delta GiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ami-sdm | A-iq4 | 10 | 5.24 | 14.1% | 18.1% | 27.3% | 297 | 0.082 | 22.3 |
| ami-sdm | A-q8 | 10 | 5.24 | 13.9% | 18.1% | 26.8% | 327 | 0.091 | 36.0 |
| ami-sdm | B | 10 | 5.24 | 40.9% | 47.4% | 48.8% | 1040 | 0.289 | 3.4 |
| ami-sdm | A-iq4-diar | 10 | 5.24 | 33.1% | 56.3% | 45.9% | 267 | 0.074 | 22.3 |
| ami-ihm-mix | A-iq4 | 10 | 5.24 | 9.5% | 10.3% | 21.1% | 286 | 0.079 | 22.3 |
| ami-ihm-mix | A-q8 | 10 | 5.24 | 10.8% | 11.6% | 22.4% | 327 | 0.091 | 36.0 |
| ami-ihm-mix | B | 10 | 5.24 | 25.7% | 27.5% | 34.6% | 898 | 0.250 | 2.7 |
| mls-de | A-iq4 | 4 | 0.41 | 4.1% | 4.1% | 10.5% | 312 | 0.087 | 19.9 |
| mls-de | A-q8 | 4 | 0.41 | 4.0% | 4.0% | 10.5% | 410 | 0.114 | 33.6 |
| mls-de | B | 4 | 0.41 | 12.4% | 12.4% | 12.5% | 1140 | 0.317 | 2.4 |

A-iq4-diar is the attempt described below, run on the distant mic only.
Engine B's speed in this table was measured while the box's Ollama kept
serving other clients on the same GPU (see "Where the time goes").

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
| engine B | 191 | 0.23 s, 0.25 s | 12.3 | 4.1 s |

Chat answers come about 30 % slower while a transcription runs; no request
failed. Long prompts (prefill) were not measured.

One GPU reset during the follow-up runs (2026-11-17 22:38:56 CET), after the
benchmark itself: Ollama's ROCm runner hit GPU page faults while it wrote a
summary, at a moment when an engine-A Q8_0 job (36 GB of GTT) ran next to it
and the embedding and reranker models (21 GB) were resident, close to the
102 GiB GTT limit; the kernel had logged ROCm memory evictions under that
pressure earlier. The driver reset the GPU and recovered without a reboot,
but every GPU process on the box lost its context: the running engine job
failed (Vulkan "device lost"), the engine-B container and ComfyUI restarted,
Ollama restarted its runners. Since then the recorder's own LLM calls take
the same GPU lock as the engines, and heavy jobs run one at a time. In normal
use (IQ4_NL, one meeting at a time, LLM afterwards) GTT stays around 70 GiB.

### More than one hour

ES2004b and ES2004c joined into one 78-minute file (the same four people):
engine A cut it into two windows overlapping by 60 s, linked all four
speakers through 206 words both windows heard, and created no new labels;
8.3 minutes in total (RTF 0.106). The transcript carries
`speaker_scope: per_chunk` and a note.

### Action items against AMI's human summaries

Our summary of engine A's distant-mic transcript (qwen3.8:27b on the box)
next to the action sentences of AMI's human abstractive summaries, for the
three of our ten meetings with the most human action items. The box's LLM
marked which human items our list covers and which of ours match a human
one (`data/bench/spotcheck.md`).

| Meeting | Human actions covered | Our items that match a human one |
|---|---|---|
| IS1009a | 3 of 3 | 2 of 3 |
| IS1009c | 3 of 4 | 3 of 5 |
| TS3003c | 0 of 3 | 0 of 3 |

- IS1009a and IS1009c: the substance is there (product evaluation, user
  interface design, the clay prototype, requirement specs). The one human
  item we miss ("their coaches will send specific instructions") is about
  the course organisers, not the team. Our extras are real tasks from the
  meeting the annotators left out (research the cost of speech recognition)
  plus one icebreaker exercise (draw your favourite animal on the whiteboard)
  that should not be an action item.
- TS3003c: the human items are vague ("continue with what he has been
  working on", "work together"); ours are specific (pricing of wooden cases,
  ask manufacturing about voice recognition costs, look and feel and user
  interface). The judge counted no overlap; read side by side, the third of
  ours covers the second and third human items in substance.
- So the recorder's simple summary finds the concrete commitments, but also
  lists small in-meeting asks. The Command Center agent's later summary,
  with its context, is the place to weed those out.

### Our own recording (no human reference yet)

A 15-minute excerpt of a retreat session, recorded with a phone voice
recorder (AAC), and its busiest 5 minutes (from 10:00: five speakers, 136
speaker changes in the engine-A draft). Only numbers left the box; the
"differs" column is the speaker-agnostic WER of each engine's words against
engine A's words, so it measures how far the engines agree, not who is right:

| Recording | Engine | Speakers found | RTF | Differs from A-iq4 |
|---|---|---:|---:|---:|
| retreat, 15.4 min | A-iq4 | 6 | 0.067 | base |
| retreat, 15.4 min | B | 5 | 0.50 (first run after a restart, model load included) | 52 % |
| retreat-10m, 5 min | A-q8 | 6 | 0.11 | 17 % |
| retreat-10m, 5 min | B | 5 | 0.19 | 50 % |

- On real phone audio engine B writes about 40 % fewer words than engine A
  (1,350 against about 2,250 for the 15 minutes), the same pattern as its
  deletions on the AMI distant mic.
- The engines disagree on the number of speakers (A 6 to 7, B 5). Which is
  right needs the person who was there; the corrected excerpt
  (`data/bench/datasets/own-ref/retreat-10m/ref.md`, then
  `python -m bench.excerpts import retreat-10m`) turns this into WER, cpWER
  and DER.
- A-iq4 on the 5-minute excerpt ran during the GPU incident, so its timing
  and its GTT peak (which counts every process on the box) are left out.

### End to end through the phone path

`bench/ami_e2e.sh` streamed the ten AMI distant-mic meetings through the
phone simulator (WebM/Opus, 30 s chunks, the upload API over HTTPS through
Caddy) into the running recorder with the default engine (A-iq4), one after
another:

- 5.2 h of audio in 633 chunks, uploaded in 151 s in total, no retries;
- 110 to 330 s from finalize to finished summary per meeting (17 to 43 min
  of audio each; transcription, then the summary with qwen3.8:27b);
- the right number of speakers (4) in nine of ten meetings, five in one;
- 2 to 5 action items per summary;
- the follow-up agent ran on its own after every meeting: 25 proposals waiting
  for review (10 meeting-log entries, 13 new issues, 2 comments on an existing
  mock issue that tracks the same work). Two meetings proposed similar new
  issues about voice-recognition cost: proposals are matched against existing
  issues, not against other proposals still waiting for review.

The first attempt of this run was cut short by the GPU reset described above
and resumed (`OUT=<results file> bench/ami_e2e.sh`).

## Recommendation

Make **engine A with the AD-IQ4_NL decoder (`A-iq4`)** the default:

- best accuracy where it matters, a phone on the table: cpWER 18.1 % against
  47.4 % for engine B on the AMI distant mic, and the same as Q8_0;
- about 5 minutes per hour of audio, three to four times faster than B
  (and still three times faster than B's best isolated run, RTF 0.19);
- 22 GiB of GTT while it runs (Q8_0 needs 36 GiB), which leaves room for the
  box's LLM and embedding models (102 GiB GTT in total).

Keep engine B as the fallback when GPU memory is short (3.4 GiB) or when
punctuated text matters more than completeness; engine A with `--diarize`
writes lowercase text without punctuation. Do not use Q8_0: it costs 14 GiB
more for no measurable gain. Do not run engine A without `--diarize` to pair
it with a separate diarizer: in NeMo-Speech.cpp 0.3.0 that loses words and
moves word timestamps on far-field audio.

What is not settled yet: German meetings. MLS is read speech; the German
ground truth from our own calls (Drive recordings, hand-corrected excerpts)
is still to come, and so is the corrected excerpt of the retreat recording.
