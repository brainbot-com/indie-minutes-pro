# indie-recorder

A private meeting recorder for indie.box. A phone records, the audio goes over
the VPN to the box (aibox1: Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified
memory), the box transcribes it with speaker labels and summarizes it with its
own LLM. Per meeting the person chooses where it lives: only on the box, or
also in Google Drive (summary, action items, transcript, optionally the
audio). A meeting agent in brainbot:agent command does the
Drive part and writes a better summary from its own context and memory; the
indie-recorder app in brainbot:agent command shows and steers it all. Everyone has
their own account there: a person's phones record into it, and each person
sees only their own meetings.

```
phone (Android app or PWA, VPN)                 aibox1 (10.1.9.7)
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
       indie-recorder app in brainbot:agent command (connector, per person) -> meetings, summaries, speakers, phones
```

## What stays on the box

- ASR, diarization and the first summary run on aibox1 with its own models;
  there are no cloud ASR or LLM calls.
- Each meeting is local or in Drive. Local: audio, transcript and summaries
  stay on aibox1. Drive: the meeting agent copies `Summary.md`,
  `Action items.md` and `Transcript.md` (and the recording when
  `drive_include_audio` is on) into a Drive folder per meeting. The person
  decides per meeting (web view, indie-recorder app, phone app); the default for new
  recordings starts as `STORAGE_DEFAULT` and is a setting after that.
- Everything listens on the box only: Caddy binds 10.1.9.7 and admits only
  `ALLOW_CIDRS` (the VPN client pools, ai-knowledge for
  brainbot:agent command, the box itself for the simulator). The recorder listens on
  127.0.0.1:18000, the engines only on an internal Docker network. aibox1 has
  no VPN interface of its own; VPN clients reach its LAN address unNATed,
  which is why the allowlist works.
- brainbot:agent command reaches the recorder through a route on ai-knowledge that
  passes only what the indie-recorder app, meeting agents and phones need (tokens,
  connect codes, signed file links); see
  [integrations/indie-chat](integrations/indie-chat/README.md).
- Encrypted wherever it waits: on the phone, on the way and on the box (see
  "Encryption").
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

## Encryption

- On the phone: each recording gets its own key pair, and every 30 s chunk is
  encrypted (AES-256-GCM) with a key agreed with the box's sealing key (ECDH
  P-256, HKDF-SHA256) before it is stored or sent. The box publishes the key
  in `/api/v1/health`; the web app keeps it for offline starts. What waits in
  the phone's storage or passes brain-tls is ciphertext only aibox1 opens.
- On the way: TLS on every hop (the Android app pins the box's CA, brain-tls
  trusts only the recorder's CA).
- On the box: every file under `data/meetings`, `data/uploads` and
  `data/outbox` is AES-256-GCM in 1 MiB segments, each bound to its file, its
  place and whether it is the last (`recorder/vault.py`); the index is
  SQLCipher. ffmpeg and the engines get plain audio only in `/scratch`, a
  tmpfs (RAM) shared with the engine containers, for the length of a job.
- Keys: `deploy/make-keys.sh` makes `secrets/data.key` and `secrets/seal.pem`
  once. Keep a copy of both off the box: without `data.key` the meetings
  cannot be read. The recorder refuses to start without the key, or with
  another key than the data was written with. `recorder.cli encrypt` encrypts
  meetings from before (each file checked, in place); the index converts
  itself on the first start.
- Limits: root on the running box holds the key. The web app served through
  ai-knowledge is only as trustworthy as ai-knowledge, which delivers its
  code; the Android app talks to aibox1 directly and pins it. Drive copies are
  plain in Drive (the person chose Drive). The indie-recorder app and the meeting
  agent read summaries and transcripts in brainbot:agent command, which keeps
  its own data encrypted. Phones that do not seal (Android before 0.4.0)
  are still accepted, over TLS.

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

## Phone web app (PWA)

For any phone with a current browser; on Android the native app (below) also
records with the screen off.

1. Connect the phone to the VPN.
2. Install the box's CA certificate once: open `http://10.1.9.7:8480/ca.crt`,
   then Settings, Security and privacy, More security settings, Install a
   certificate, CA certificate. Compare the SHA-256 fingerprint with the one
   `deploy/make-ca.sh` printed. Android then shows "network may be
   monitored"; that refers to this CA.
3. Connect it (see "Connecting a phone"): scan the code from Meetings and
   choose "Record in the browser", or type the code on the web app's first
   screen. Install the web app from the browser's menu (Add to home screen).
4. Record, Pause, Resume, Stop. A pause releases the microphone (the browser's
   mic indicator goes off) and the clock counts recorded time only; the
   waveform shows the last seconds, grey while paused. Chunks are stored on
   the phone (IndexedDB) until the box has them, so a dead network only delays
   the upload. The app keeps the screen on with a Screen Wake Lock while
   recording; recording with the screen off is the main job of the native app
   (see below).
5. Meetings (the button under Record) lists all of the person's meetings on
   the box, from any of their phones, with what this phone has not handed
   over yet on top. A meeting opens with its summary, speaker names (play a
   speaker's voice, name them) and the transcript: the same views and words as
   indie-recorder in brainbot:agent command (see Design). Settings has Google
   Drive (connect or disconnect).

The phone apps reach those meetings with the same ops as indie-recorder,
for the person their token belongs to: `POST /api/v1/app {op, args}` with the
device token (`list_meetings`, `get_meeting` with the transcript,
`rename_speakers`, `set_storage`, `new_summary`, `status`, the settings, Google
Drive). Not the agent's ops and not pairing; a phone that belongs to nobody
gets 403. Only a person names speakers: an agent suggests (`suggest_speakers`).

The phone simulator streams any audio file through the same upload API (Opus
in 30 s chunks by default): `python3 sim/phone_sim.py --cacert secrets/tls/ca.crt
--token-file <token> meeting.wav --wait`. `--flaky 0.3` drops uploads on purpose
to exercise the resume path.

## Connecting a phone

One code for every phone and both apps. In brainbot:agent command, indie-recorder,
Connect a phone (on the empty list, or in Settings) shows a QR code and the
same code to type, for example `K7Q4-M2XB`:

1. The phone (VPN on) scans the QR code. It opens the connect page,
   `https://ai-knowledge.mz.brainbot.com/indie-recorder/connect#K7Q4M2XB`,
   through brain-tls, so the browser trusts it without the box's CA.
2. The page says whose account the phone will record into. On Android:
   "Open the app" (or "Install the app" first, then "Open the app"); the app
   asks once more, "Record into Luis's account", and connects. On any phone:
   "Record in the browser" opens the web app, which connects at once.
3. The indie-recorder app shows "Connected: Pixel 8" (the phone names itself) and
   lists the phone under Settings, Phones, where "Remove" locks it out.

Without a camera at hand, type the code in the Android app's or the web app's
first screen. On the phone that shows brainbot:agent command itself, the
indie-recorder app offers "Connect this phone" instead of the QR code.

- A code works once, for 15 minutes, and a new one replaces the person's
  older ones. It carries no token, address or certificate: the phone gets
  its own token, the box's address and its CA when it spends the code
  (`GET /api/v1/pair/{code}` shows whose it is, `POST /api/v1/pair` spends
  it). 30 wrong codes in 10 minutes close the door for everyone for a while
  (8 characters from 31: about 10^12 codes).
- Recordings go from the phone straight to the box (`https://10.1.9.7:8443`,
  the app pins its CA); only the connect page, the two code endpoints, the
  APK and the page's stylesheet pass through ai-knowledge. The web app still
  runs on the box's own address, so a phone needs the box's CA once to use it
  (see "Phone web app").
- Settings: `CONNECT_URL` (where the code's link points; on aibox1 the
  brain-tls address, default the box's `/connect`), `WEBAPP_URL`, `APK_URL`.
  An admin makes a code on the Devices page or with `recorder.cli connect
  --user <id>`.

## Web view

- Meetings: a numbered row per meeting (title, start, length, and Processing,
  Failed, Drive or Local). A person sees their own meetings, an admin all.
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
- Google Drive, by the recorder itself: each person connects their own
  Google Drive once in the indie-recorder app (Settings, Storage, Connect
  Google Drive). That opens Google's sign-in in a tab of its own (OAuth with
  PKCE through `/oauth/google/start` and `/oauth/google/callback`, routed by
  brain-tls), asking only for `drive.file`: the files the recorder makes,
  nothing else in their Drive. An account with an email address on the box
  takes that Google account only. From then on that person's `drive_store`
  and `drive_log` items are carried out right away by the box (`gdrive.py`),
  with no agent in between: a folder per meeting in their Drive folder, a
  file that changed moves into "Older versions" first, and the "Meeting log"
  is a Google Doc written whole each time (one entry per meeting, the
  newest). The refresh token sits in the encrypted index; access tokens only
  in memory; Disconnect revokes it at Google. The OAuth client is the one
  brainbot:agent command uses (`secrets/google_oauth.json`,
  `{"client_id", "client_secret"}`), with the redirect address
  `https://ai-knowledge.mz.brainbot.com/indie-recorder/oauth/google/callback`
  added to it in the Google Cloud console. Without the file the app shows no
  Connect button and Drive stays the agent's (below). A newer summary sets
  aside Drive items an older one still waited for ("superseded").
- Otherwise the box holds no GitHub or Drive login. Who carries out approved
  proposals is set by `FOLLOWUP_EXECUTOR`:
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

## brainbot:agent command (meeting agent and indie-recorder app)

Setup, step by step: [integrations/indie-chat](integrations/indie-chat/README.md)
(a script for ai-knowledge, your own MCP server, the indie-recorder app, the meeting
agent with its instructions and hourly routine). In short:

- The recorder's MCP endpoint (`/mcp`, streamable HTTP, plain JSON, no
  sessions) takes an agent token. Tools: `pending_work` (empty when there is
  nothing to do, for a routine's pre-check), `list_meetings`, `get_meeting`,
  `update_summary`, `status`, `get_settings`, `update_settings`,
  `set_storage`, `rename_speakers`, `new_summary`, `drive_files` (signed
  download links for a `drive_store` item), `list_outbox`, `ack_outbox_item`,
  `sync_issues`.
- The indie-recorder app is one shared app for everyone. It uses the deployment's
  "indie-recorder" connector (`/api/v1/indiechat`): brainbot:agent command
  sends the recorder's service token and names the signed-in person, so the
  recorder scopes everything to that person (their meetings, settings,
  phones, name). Tokens of the role agent with `--trusts-identity` may do
  that; a phone's token belongs to one person.
- A person's meeting agent reaches the MCP endpoint as
  `https://ai-knowledge.mz.brainbot.com/indie-recorder/mcp`, a personal MCP
  server with their own recorder token (only their agents get it).
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

Accent hints (`engine-accent`, `engines/accent`), to help a person put names to
"Speaker N": mavleo96/whisper-accent-medium.en (Apache-2.0; Whisper medium.en
with an accent head, 23 accents of English, 95.7% on its own test set) listens
to up to 30 s of each speaker's longest turns and gives its three likeliest
guesses; the speaker cards show those of 10% or more ("Accent, a guess:
German 71% · Dutch 12%"). On the CPU, so it never takes or waits for the GPU;
only the encoder runs, nothing is transcribed. English meetings only (the
model knows accents of English), right after a meeting is transcribed;
`recorder.cli accents MEETING...` does earlier ones. The guesses are stored
encrypted with the meeting (`accents.json`), shown only to its owner (and
their own agent, as a hint for its name suggestions), and never go to Drive
or into a summary. An accent can hint at someone's origin, which the GDPR
treats as special: they are hints for naming, nothing else, and `ACCENT=0`
switches them off for the box. The model is gated on Hugging Face (accepted
at once): accept its terms with any account, put a read token in
`secrets/hf_token`, then `deploy/fetch-accent-model.sh` downloads only its own
files (about 3.4 GB, not the training checkpoints in the same repo) into
`models/` and starts the engine. Without the model the engine says
`ready: false` and the cards show no accent.

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
- Tokens: `docker compose exec recorder python -m recorder.cli token add|list|revoke`
  (phones connect with codes, see above; tokens are for agents).
- People: `recorder.cli user add|list|claim|remove`. A person appears on their
  first visit to the indie-recorder app; `claim` gives a token, and the meetings its
  phone recorded, to a person; `remove` revokes their phones and deletes them
  (refused while meetings still belong to them).
- Delete meetings (folder, index, follow-ups): `docker compose exec recorder
  python -m recorder.cli delete <meeting id> ...`; refused while a job runs
  for it.
- Backup: `data/` (meetings and index, encrypted) and, separately, `secrets/`
  (the keys).

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
- brainbot:agent command passes only a person's id to the recorder, no name or
  email. Each person picks the name their phones show (indie-recorder, Settings,
  Your name); until then the phone shows only its own name.
- New people start with the box's defaults for storage (on aibox1: Google
  Drive, folder "indie-recorder test"). Drive uploads need a meeting agent of
  their own; without one, a Drive meeting waits and stays on aibox1.
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
what the PWA cannot do: record with the screen off. Version 0.5.0 has the
shared Swiss look (see Design): Connect, Ready, Recording, Paused, a meeting,
Settings; it connects with a connect code (see "Connecting a phone"), scanned
(the connect page opens the app) or typed as XXXX-XXXX, asks "Record into
<person>'s account" before it connects, and names itself (the device name set in the phone's settings, else maker and
model). It seals each chunk on the phone like the web app (see Encryption;
the box's key comes from `/api/v1/health` and is kept on the phone), and the
recording's key sits under an Android Keystore key until the box has the
recording; if the Keystore loses it, a part cut short by a crash is dropped,
never kept in the clear. The main screen does not scroll: the timer and the
blocks fit the phone, and the meetings are one button at the bottom (how
many, and in red how many are uploading or processing). It opens all of the
person's meetings on the box, from any of their phones, with this phone's own
uploads on top; a meeting shows its summary, speaker names (play a voice,
name them) and the transcript, the same screens and words as the web apps
(`/api/v1/app`, see the PWA; Design). Settings has Google Drive. Typed
codes are looked up at `CONNECT_BASE` (set when it is built, default the
brain-tls address); old token links still work. Disconnect on the phone
forgets the token; remove the phone in indie-recorder to lock it out.

- Records in a foreground service of type microphone (a notification with a
  timer, Pause or Resume, and Stop; a partial wake lock): 16 kHz mono from the speech
  recognition microphone source, AAC LC at 48 kbit/s in 30 s chunk files
  (ADTS frames, so the box joins them as `audio/aac`; about 22 MB an hour).
- Each chunk goes to the box as soon as it is complete, with the PWA's
  resumable API. After Stop, WorkManager keeps trying (whenever there is a
  network, every few minutes, across app restarts and reboots) until the box
  has the whole recording; only then are the chunks deleted. When the app is
  killed mid-recording, its whole chunks are kept (a half-written one is cut
  back to its last whole frame) and sent at the next start.
- Pause stops the microphone (Android's mic indicator goes off) until Resume;
  the timer and the stored length count recorded audio only, and the chunks
  stay one continuous recording. A crash while paused is recovered like one
  while recording.
- A live waveform (the spec under Design) replaces the level meter.
- Says when a call takes the microphone.
- Connecting: see "Connecting a phone". The app gets the box's address and
  CA certificate with its token and trusts that CA for the box and nothing
  else, so the phone needs no CA in its settings. The Ready screen shows
  "For <their name>" (from `/api/v1/me`, read again whenever the app comes to
  the front), or the phone's name while they have set none. The token is
  encrypted with an Android Keystore key; recordings and token are excluded
  from cloud backup.
- Per recording a title and "Save to Google Drive" (the box's default); the
  list shows upload and processing state; a meeting shows its summary and the
  Drive switch. English and German, light and dark.

Install: the connect page offers it ("Install the app"), from
`https://ai-knowledge.mz.brainbot.com/indie-recorder/indie-recorder.apk` (VPN
on; Android asks to allow installs from the browser; the same file is at
`http://10.1.9.7:8480/indie-recorder.apk`). Inter is bundled under the SIL
Open Font License (`assets/licenses/Inter-OFL.txt` in the APK).

Build and test, on aibox1 from `~/indie-recorder`:

- `android/build.sh`: the build image (`android/Dockerfile`: JDK 17, Android
  SDK, Gradle, an emulator image), unit tests, a debug build and a release
  build signed with `secrets/android-release.jks` (made on the first run; keep
  it: Android installs an update only with the same key), copied to
  `secrets/tls/public/indie-recorder.apk`.
- `android/e2e/emulator-test.sh`: an Android 14 emulator (KVM) with the debug
  build connects as a test person by a typed code (a used code is then
  refused) and by the connect page's link (`recorder.cli connect`), records a 75 s speech
  file in place of the microphone (a debug-only hook), and the box transcribes
  it; then the same with the network cut during the recording, with the app
  killed after 40 s, and with a pause (30 s, about 23 s paused, 30 s: the
  phone and the box both count 60 s). Light and dark screenshots of every
  screen in `data/test/android/`. On exit it deletes its meetings, revokes its
  token and removes the test person (`recorder.cli user remove`); `KEEP=1`
  keeps everything for a look.

Not in the app yet: a VPN of its own (it shows "Box not reachable (VPN?)" and
waits), choosing the microphone (an external one is used when Android routes
to it), editing speaker names (the web view and the indie-recorder app have it).

## Design

Every face of the recorder shares one look, direction 5 of the design canvas
(Swiss): Inter, black on white (white on black in dark mode), one red
(#E1251B) for recording and the one main action of a screen, 3px rules over
sections and 1px rules between numbered rows, square switches and buttons.

- `recorder/static/swiss.css`: the tokens and components; the PWA and the web
  view load it from the box. The indie-recorder app gets it inlined, with the font,
  by `integrations/indie-chat/inline_assets.py`. The Android app has the same
  tokens in its Compose theme and the same Inter cuts in `res/font`.
- Inter 4.1, subset to Latin (`recorder/static/Inter.woff2`, 53 KB), SIL Open
  Font License (`recorder/static/Inter-OFL.txt`, also in the APK).
- The meeting views, the same in every app: the person's meetings by day, and
  a meeting with its summary, speaker cards and the transcript as turns.
  `recorder/static/meetings.js` and `meetings.css` draw them; the PWA loads
  them from the box, `inline_assets.py` puts them into the indie-recorder app,
  and the Android app mirrors them in Compose with the same order and words.
- The waveform, the same in the PWA and the Android app: one bar per 100 ms of
  recorded audio, bar 3 px wide every 5 px, the loudest sample of the window
  mapped from -50 dBFS (2 px) to 0 dBFS (88 px), centred, newest at the right;
  grey and still while paused, with a gap of three bars where the pause was.

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
