# indie-recorder

A private meeting recorder for indie.box. A phone records, the audio goes over
the VPN to the box (aibox1: Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified
memory), the box transcribes it with speaker labels and summarizes it with its
own LLM. Per meeting the person chooses where it lives: only on the box, or
also in Google Drive (summary, action items, transcript, optionally the
audio). A meeting agent in brainbot:agent command does the
Drive part and writes a better summary from its own context and memory; the
Meetings app in brainbot:agent command shows and steers it all.

```
phone (Chrome PWA, VPN)                         aibox1 (10.1.9.7)
  record, 30 s chunks,  --HTTPS, VPN only-->  Caddy :8443 (private CA, allowlist ALLOW_CIDRS)
  offline buffer                                 |
                                               recorder (FastAPI): upload API, queue (1 job), web view, MCP
                                                 |  ffmpeg -> 16 kHz mono WAV
                                                 |-> engine A: NeMo-Speech.cpp (Vulkan) + Nemotron 3.5 Transcribe
                                                 |-> engine B: Qwen3-ASR + ForcedAligner (ROCm) + Nemotron 3 Diarization (Vulkan)
                                                 |  transcript.json / .md with Speaker 1..N
                                                 |-> summary v1: qwen3.8:27b on the box's Ollama
                                               data/meetings/<id>/ + index.sqlite
                                                 |
       follow-up agent: meeting set to Drive -> outbox -> meeting agent (brainbot:agent command) -> Google Drive
                        GitHub (optional): /review -> approved -> outbox -> agent -> issue or comment
       meeting agent (MCP) -> better summary v2, v3 ... (its context and memory)
       Meetings app in brainbot:agent command (MCP) -> meetings, summaries, speakers, Drive or local
```

## What stays on the box

- ASR, diarization and the first summary run on aibox1 with its own models;
  there are no cloud ASR or LLM calls.
- Each meeting is local or in Drive. Local: audio, transcript and summaries
  stay on aibox1. Drive: the meeting agent copies `Summary.md`,
  `Action items.md` and `Transcript.md` (and the recording when
  `drive_include_audio` is on) into a Drive folder per meeting. The person
  decides per meeting (web view, Meetings app, phone app); the default for new
  recordings starts as `STORAGE_DEFAULT` and is a setting after that.
- Everything listens on the box only: Caddy binds 10.1.9.7 and admits only
  `ALLOW_CIDRS` (the VPN client pools, ai-knowledge for
  brainbot:agent command, the box itself for the simulator). The recorder listens on
  127.0.0.1:18000, the engines only on an internal Docker network. aibox1 has
  no VPN interface of its own; VPN clients reach its LAN address unNATed,
  which is why the allowlist works.
- brainbot:agent command reaches the recorder through a route on ai-knowledge that
  passes only the MCP endpoint (token) and signed file links (two hours); see
  [integrations/indie-chat](integrations/indie-chat/README.md).
- Agents that read transcripts must use aibox1's LLM (the meeting agent uses
  engine "aibox1 Ollama" with `qwen3.8:27b-ctx65536`), never StudioLLM or
  another host. An agent's run history keeps what its tools returned, so the
  meeting agent is a private agent of the person whose meetings it handles.
- GitHub (optional, off unless `FOLLOWUP_TARGETS` names it): only after a
  person approves it on `/review`, and only the text of an issue or comment
  (action item, owner, due date, a short description, a link back to the
  meeting), never transcript text.
- No voice enrollment: speakers stay "Speaker 1..N" until a person names them
  (voiceprints are biometric data under GDPR).

## Setup from scratch

On the box (any indie.box with an AMD GPU; tested on aibox1 with Ubuntu 24.04,
kernel 6.14 OEM, Mesa 25.2 RADV, ROCm 7.0.1, Docker 2x), as a user in the
`docker`, `render` and `video` groups:

1. Ollama with `qwen3.8:27b-ctx65536` on port 11434 (aibox1 has it). Use the
   tag other services already use: a different tag or context size makes
   Ollama load a second copy.
2. `git clone git@github.com:brainbot-com/indie-recorder.git ~/indie-recorder`
   and `cp .env.example .env`; set `BIND_IP`, `ALLOW_CIDRS`, `PUBLIC_URL`, and
   check `RENDER_GID` / `VIDEO_GID` against `getent group render video`.
3. `deploy/fetch-models.sh` downloads about 63 GB into `models/` (pinned
   revisions): Nemotron 3.5 Transcribe Encoder-F16, AD-IQ4_NL and Q8_0
   (AtomicChat GGUFs), Nemotron 3 Diarization, Qwen3-ASR-1.7B,
   Qwen3-ForcedAligner-0.6B.
4. From a machine that can ssh to the box: `deploy/deploy.sh aibox1`. It
   copies the checkout, creates the private CA and server certificate
   (`deploy/make-ca.sh`), builds and starts the stack, writes an admin token to
   `secrets/admin_token` and enables the hourly follow-up timer
   (`systemctl --user`, needs `loginctl enable-linger`). Build the engines once
   with `docker compose build engine-nemo engine-qwen` (engine B pulls about
   4 GB of ROCm wheels) and start them with `docker compose up -d`.
5. Follow-ups: `.env.example` runs the follow-up loop against the mock
   GitHub and Drive on the box (`FOLLOWUP_EXECUTOR=direct`). For the real
   Drive (and GitHub), set up the meeting agent in brainbot:agent command
   ([integrations/indie-chat](integrations/indie-chat/README.md)) and switch
   to `FOLLOWUP_EXECUTOR=agent`; the box itself holds no GitHub or Drive login.
6. Check: `curl -s http://127.0.0.1:18000/api/v1/health` and
   `docker compose exec engine-nemo nemo-speech doctor` (must list the Radeon
   8060S on RADV).

## Phone (Chrome on Android)

1. Connect the phone to the VPN.
2. Install the box's CA certificate once: open `http://10.1.9.7:8480/ca.crt`,
   then Settings, Security and privacy, More security settings, Install a
   certificate, CA certificate. Compare the SHA-256 fingerprint with the one
   `deploy/make-ca.sh` printed. Android then shows "network may be
   monitored"; that refers to this CA.
3. On a laptop, sign in at `https://10.1.9.7:8443` with the admin token, open
   Devices, create a token for the phone and scan the QR code with the phone.
   That opens the recorder app paired (`/app/`). Install it from Chrome's menu
   (Add to home screen).
4. Record, Stop. Chunks are stored on the phone (IndexedDB) until the box has
   them, so a dead network only delays the upload. The app keeps the screen
   on with a Screen Wake Lock while recording; recording with the screen off
   is the main job of the native app (see below). The list shows upload and
   processing state and links to the summary.

The phone simulator streams any audio file through the same upload API (Opus
in 30 s chunks by default): `python3 sim/phone_sim.py --cacert secrets/tls/ca.crt
--token-file <token> meeting.wav --wait`. `--flaky 0.3` drops uploads on purpose
to exercise the resume path.

## Web view

- Meetings: one line per meeting (title, start, length, speakers), a chip
  only for processing, failed or Drive.
- A meeting: the summary first (v1 by the recorder, later versions by the
  meeting agent; earlier ones stay linked), "Store in Google Drive" with a
  link to the meeting's Drive folder, the recording; folded away below:
  speaker names, the transcript with click-to-play timestamps, and details
  (engine and timing, follow-ups, files, "Transcribe again" with another
  engine, "New summary").
- Speaker names replace "Speaker N" everywhere (transcript, summary, action
  items) at once, without a new LLM run.
- Follow-ups (footer): proposals to approve or reject.
- Devices (admin): tokens for phones and agents, revoke.

Meeting folder (`data/meetings/<id>/`): `original.webm`, `audio.wav`,
`transcript.json` (words with start, end, speaker), `transcript.md`,
`speakers.json`, `summaries/v001.json`..., `summary.json`, `summary.md`,
`actions.json` (items with stable keys), `meta.json`, `engine/` (raw engine
output and logs). The SQLite index is `data/index.sqlite`.

## Follow-up agent

`python -m recorder.followup run` (hourly via `indie-recorder-followup.timer`,
and right after each meeting) turns every new summary version into follow-ups,
for the targets in `FOLLOWUP_TARGETS` (default `drive`):

- `drive`: for a meeting set to Drive, a `drive_store` item (its files into
  its Drive folder) and a `drive_log` item (an entry in the running "Meeting
  log" doc). Choosing Drive for the meeting is the approval, so these skip
  `/review`. A local meeting gets neither.
- `github`: it reads `actions.json` and `summary.json`, matches each action
  item against the repo's existing issues and lets the LLM on the box decide
  per item (comment on an existing issue, open a new one, or nothing). Bodies
  link back to the meeting on aibox1 and never quote the transcript. Removing
  `github` from the targets keeps earlier proposals on `/review` but they
  cannot be approved or carried out.

- Dry-run by default: proposals wait on `/review`. `FOLLOWUP_AUTO=1` (or
  `run --auto`) approves them at once.
- The box holds no GitHub or Drive login. Who carries out approved proposals
  is set by `FOLLOWUP_EXECUTOR`:
  - `agent` (production): approved proposals go to the outbox; the Agent
    Command Center agent, which has GitHub and Drive access, carries them out
    and acknowledges each with its URL. It also keeps the box's copy of the
    repo's issues fresh (`sync_issues`), which is what proposals are matched
    against.
  - `direct` (demo and tests, the default in `.env.example`): the box calls
    the mock GitHub and Drive in `mock/server.py` itself, so the whole loop
    runs without accounts. The mock's issues, comments and docs are at
    `https://10.1.9.7:8443/mock/`.
- Idempotent: one proposal per (meeting, item), a mapping (meeting, item) ->
  issue, comment or doc once done, and a hidden marker in every GitHub body
  (checked before posting in direct mode; the agent posts bodies unchanged),
  so a rerun or a lost database never duplicates.
- JSON in and out for routines:
  `docker compose exec -T recorder python -m recorder.followup run --json-in - <<< '{"auto": false}'`,
  plus `list`, `approve ID`, `reject ID`, `outbox`, `ack ID --url URL`.

## brainbot:agent command (meeting agent and Meetings app)

Setup, step by step: [integrations/indie-chat](integrations/indie-chat/README.md)
(a script for ai-knowledge, your own MCP server, the Meetings app, the meeting
agent with its instructions and hourly routine). In short:

- The recorder's MCP endpoint (`/mcp`, streamable HTTP, plain JSON, no
  sessions) takes an agent token. Tools: `pending_work` (empty when there is
  nothing to do, for a routine's pre-check), `list_meetings`, `get_meeting`,
  `update_summary`, `status`, `get_settings`, `update_settings`,
  `set_storage`, `rename_speakers`, `new_summary`, `drive_files` (signed
  download links for a `drive_store` item), `list_outbox`, `ack_outbox_item`,
  `sync_issues`.
- brainbot:agent command reaches it as
  `https://ai-knowledge.mz.brainbot.com/indie-recorder/mcp`, a personal MCP
  server of each person (their token, only their agents and apps).
- `AGENT_SUMMARIES_SINCE` keeps meetings from before go-live out of
  `pending_work`; `FILES_BASE_URL` is where the agent downloads files from.
- An indie-chat on aibox1 itself can use `http://127.0.0.1:18000/mcp` as a
  managed server in `config/mcp-servers.json` (managed entries may use the
  loopback address; one added by hand in the UI may not).

With GitHub as a target (`FOLLOWUP_TARGETS=drive,github`), give the agent its
GitHub connection (only `brainbot-com/indie-recorder-sandbox` while testing)
and add to its instructions:

> Issues first: if anything is pending, read the open and recently closed
> issues of brainbot-com/indie-recorder-sandbox with your GitHub tools and
> send them with sync_issues (number, title, body, state, html_url). Outbox
> items github_issue: open an issue in payload.repo with payload.title and
> payload.body; github_comment: comment payload.body on issue
> payload.issue_number. Post the bodies unchanged (they carry a hidden marker
> that prevents duplicates), then ack_outbox_item with the URL.

## Engines

| | A | B |
|---|---|---|
| Recognition | Nemotron 3.5 Transcribe 30B-A4B (speech LLM), NeMo-Speech.cpp 0.3.0 prebuilt Vulkan archive | Qwen3-ASR-1.7B (qwen-asr 0.0.6, transformers) |
| Words and times | the model's CTC timestamp head | Qwen3-ForcedAligner-0.6B, chunks of at most 180 s (limit is 300 s) |
| Speakers | `--diarize` (the model's own speaker branch, up to 8) | Nemotron 3 Diarization (`nemo-speech diarize --no-batching`), each word gets the speaker of the turn it overlaps most |
| GPU path | Vulkan, Mesa RADV GFX1151 | PyTorch ROCm (AMD TheRock gfx1151 wheels, torch 2.12.0a0+rocm7.13), AOTriton attention, MIOpen FAST find; diarization on Vulkan |
| Limits | one hour per request: longer audio is cut into 59 min windows with 60 s overlap | none in practice (the package chunks) |
| Variants | `A-iq4` (AD-IQ4_NL decoder, 19.7 GB), `A-q8` (Q8_0, 35 GB) | `B` |

`ENGINE` in `.env` sets the default (`A-iq4`, see BENCHMARK.md); any meeting
can be transcribed again with another engine from its page. Engine B stays as
the fallback for when GPU memory is short (3.4 GiB instead of 22), but it is
no longer benchmarked: it was clearly behind engine A on every set. `A-iq4-diar`
(engine A without `--diarize`, speakers from Nemotron 3 Diarization) is kept
for comparison only: it did worse in the benchmark. Both engines run as small job services
(`engines/jobserver.py`) that run one job at a time, share one GPU lock (so
two GPU jobs never overlap), never kill a running GPU process, and sample GTT
use and GPU busy from sysfs during each job (rocm-smi shows no VRAM on this
APU; the GPU uses GTT). `bench/run.py --cpu-proof` runs the same clip on the
GPU and on the CPU to prove there is no silent CPU fallback; numbers are in
BENCHMARK.md.

Recordings longer than 59 minutes (engine A): the windows' speaker labels are
linked through the 60 s overlap (words both windows heard, matched by time
and text); labels that cannot be linked get new numbers, and the transcript
carries `speaker_scope: "per_chunk"` and a visible note.

## Benchmark

`bench/run.sh` reproduces BENCHMARK.md (test data, GPU proof, idle LLM
baseline, every engine on every set with the LLM latency probe, scores, the
action-item spot check). `bench/ami_e2e.sh` streams every prepared AMI meeting
through the phone simulator into the running recorder, end to end.

## Operations

- Logs: `docker compose logs -f recorder engine-nemo engine-qwen`; per
  meeting `data/meetings/<id>/engine/log.txt`; follow-up runs:
  `journalctl --user -u indie-recorder-followup`.
- Do not stop or restart an engine container while it runs a job (check
  `curl -s` on its `/v1/health`: `current` must be null). On this box a
  killed Vulkan job once stalled the amdgpu driver.
- After a GPU reset (`journalctl -k | grep "recovered through reset"`), check
  `docker exec active-ollama-1 ollama ps`: models that clients keep loaded
  ("Forever") may come back with the default 5 minutes until their client asks
  again.
- Server certificate: 397 days; renew with `deploy/make-ca.sh secrets/tls`
  (keeps the CA) and `docker compose restart caddy`. The CA lasts 10 years.
- Tokens: `docker compose exec recorder python -m recorder.cli token add|list|revoke`.
- Delete meetings (folder, index, follow-ups): `docker compose exec recorder
  python -m recorder.cli delete <meeting id> ...`; refused while a job runs
  for it.
- Backup: `data/` (meetings and index) and `secrets/`.

## Known issues

- The PWA cannot promise recording with the screen off or the app in the
  background; Chrome may stop the microphone. It keeps the screen on with a
  wake lock and warns. The Android app records in the background.
- Engine A with `--diarize` writes lowercase text without punctuation (that is
  NeMo-Speech.cpp's speaker prompt). The punctuated speaker-tag prompt gives
  no per-word speakers in the CLI's JSON (tested), so `--diarize` stays.
- Engine A's speaker labels flip in overlapping speech; engine B's labels come
  from a separate diarizer and are steadier, but B drops fillers and short
  backchannels. See BENCHMARK.md.
- Language: Nemotron does not report the language it heard. Without a hint the
  recorder guesses German or English from common words; other languages need
  a hint (the API takes `language`).
- Engine B is slower on the first run of new input lengths (MIOpen); the
  settings in `compose.yaml` keep that small.
- While a transcription runs, the box's main LLM answers more slowly (measured
  in BENCHMARK.md); summaries also wait for the LLM if it is busy.
- GPU memory pressure can reset the GPU for the whole box: on 2026-11-17 an
  Ollama runner hit page faults while it ran next to a 36 GB engine job with
  GTT close to its 102 GiB limit, and the driver reset the GPU (recovered
  without a reboot; ComfyUI and Ollama's runners restarted). The engines and
  the recorder's LLM calls share one GPU lock so this service never runs two
  GPU jobs at once; other services on the box (Ollama's other clients,
  ComfyUI) are outside that lock. Prefer IQ4_NL (22 GB) over Q8_0 (36 GB).
- Tokens are not per user: any phone or admin token can read every meeting
  (on top of the VPN).
- Follow-up proposals are matched against existing issues, not against other
  proposals still waiting for review: two meetings about the same work can
  each propose a new issue. Once one is approved and carried out, later
  meetings comment on it instead.
- The phone must get the CA through Settings (user certificate store).
  Chrome on Android trusts user-installed CAs for websites without asking
  for Certificate Transparency; a CA placed in the system store (rooted
  phones) would need CT and fail.
- brainbot:agent command's MCP form has no field for a header, so the personal
  server (with the recorder token) and the routine's pre-check are set once
  from the browser console (see integrations/indie-chat).
- Drive: a new summary version uploads the meeting's files again; the agent
  moves the earlier copies into "Older versions". Speaker names set after the
  upload reach Drive with the next summary version.
- NeMo-Speech.cpp issue #89 (Vulkan batching assert) concerns the realtime
  streaming batcher of `nemo-speech serve`; file transcription here runs one
  sequence at a time and diarization with `--no-batching`.
- The AMI mirror at Edinburgh refuses scripted bulk downloads; the benchmark
  takes AMI audio from the CC BY 4.0 mirror `diarizers-community/ami` on
  Hugging Face and the references from AMI's annotation package and BUT.

## Android app

`android/`: a native app (Kotlin, Jetpack Compose, Android 8 and later) for
what the PWA cannot do: record with the screen off.

- Records in a foreground service of type microphone (a notification with a
  timer and Stop, a partial wake lock): 16 kHz mono from the speech
  recognition microphone source, AAC LC at 48 kbit/s in 30 s chunk files
  (ADTS frames, so the box joins them as `audio/aac`; about 22 MB an hour).
- Each chunk goes to the box as soon as it is complete, with the PWA's
  resumable API. After Stop, WorkManager keeps trying (whenever there is a
  network, every few minutes, across app restarts and reboots) until the box
  has the whole recording; only then are the chunks deleted. When the app is
  killed mid-recording, its whole chunks are kept (a half-written one is cut
  back to its last whole frame) and sent at the next start.
- Says when a call takes the microphone.
- Pairing: the Devices page shows a second QR code for the app
  (`indierecorder://pair?...`) that carries the address, the phone's token and
  the box's CA certificate. The app trusts that CA for the box and nothing
  else, so the phone needs no CA in its settings. The token is encrypted with
  an Android Keystore key; recordings and token are excluded from cloud backup.
- Per recording a title and "Save to Google Drive" (the box's default); the
  list shows upload and processing state; a meeting shows its summary and the
  Drive switch. English and German, light and dark.

Install: on the phone (VPN on) open `http://10.1.9.7:8480/indie-recorder.apk`,
allow installs from the browser, install. Then create a phone token on the
Devices page and scan the Android code with the camera.

Build and test, on aibox1 from `~/indie-recorder`:

- `android/build.sh`: the build image (`android/Dockerfile`: JDK 17, Android
  SDK, Gradle, an emulator image), unit tests, a debug build and a release
  build signed with `secrets/android-release.jks` (made on the first run; keep
  it: Android installs an update only with the same key), copied to
  `secrets/tls/public/indie-recorder.apk`.
- `android/e2e/emulator-test.sh`: an Android 14 emulator (KVM) with the debug
  build pairs through the deep link, records a 75 s speech file in place of
  the microphone (a debug-only hook), and the box transcribes it; then the
  same with the network cut during the recording, and with the app killed
  after 40 s. Screenshots in `data/test/android/`.

Not in the app yet: a VPN of its own (it shows "Box not reachable (VPN?)" and
waits), choosing the microphone (an external one is used when Android routes
to it), editing speaker names (the web view and the Meetings app have it).

## Layout

`recorder/` service, worker, engines (clients), follow-up agent, MCP, web
templates · `pwa/` phone web app · `android/` Android app · `integrations/`
brainbot:agent command · `sim/` phone simulator · `engines/` engine
containers · `bench/` benchmark · `deploy/` Caddy, CA, systemd, deploy and
model scripts.

Licenses of what the box downloads: Nemotron 3.5 Transcribe and Nemotron 3
Diarization OpenMDW-1.1, Qwen3-ASR and the aligner Apache-2.0, NeMo-Speech.cpp
Apache-2.0. Test data: AMI Meeting Corpus (University of Edinburgh et al.) and
Multilingual LibriSpeech, both CC BY 4.0.
