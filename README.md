# indie-recorder

A private meeting recorder for indie.box. A phone records, the audio goes over
the VPN to the box (aibox1: Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified
memory), the box transcribes it with speaker labels, summarizes it with its
own LLM and stores everything locally. A follow-up agent turns action items
into GitHub proposals that a person approves; an Agent Command Center agent
can later write a better summary from its own context and update Drive.

```
phone (Chrome PWA, VPN)                         aibox1 (10.1.9.7)
  record, 30 s chunks,  --HTTPS, VPN only-->  Caddy :8443 (private CA, allowlist 10.200.200.0/24)
  offline buffer                                 |
                                               recorder (FastAPI): upload API, queue (1 job), web view, MCP
                                                 |  ffmpeg -> 16 kHz mono WAV
                                                 |-> engine A: NeMo-Speech.cpp (Vulkan) + Nemotron 3.5 Transcribe
                                                 |-> engine B: Qwen3-ASR + ForcedAligner (ROCm) + Nemotron 3 Diarization (Vulkan)
                                                 |  transcript.json / .md with Speaker 1..N
                                                 |-> summary v1: qwen3.8:27b on the box's Ollama
                                               data/meetings/<id>/ + index.sqlite
                                                 |
       hourly timer: follow-up agent -> /review -> approved -> GitHub sandbox issue or comment
                                                          -> Drive outbox -> Command Center agent -> Drive
       Command Center agent (MCP) -> better summary v2, v3 ... (its context and memory)
```

## What stays on the box

- Audio and transcripts never leave aibox1. ASR, diarization and the LLM all
  run on the box; there are no cloud ASR or LLM calls.
- Everything listens on the box only: Caddy binds 10.1.9.7 and admits
  `ALLOW_CIDRS` (the VPN clients, plus the box itself for the simulator). The
  recorder listens on 127.0.0.1:18000, the engines only on an internal Docker
  network. aibox1 has no VPN interface of its own; VPN clients reach its LAN
  address unNATed, which is why the allowlist works.
- What leaves the box, and only after a person approves it on `/review`: the
  text of a GitHub issue or comment (action item, owner, due date, a short
  description and a link back to the meeting), never transcript text.
- Agents that read transcripts (the MCP tools return them) must run on aibox1
  against aibox1's LLM. An indie-chat agent on another host, or one that uses
  StudioLLM on aibox0, would move transcripts off the box.
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
   GitHub and Drive, set up the Agent Command Center agent (below) and switch
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

- Meetings: list, state while processing, TL;DR.
- A meeting: current summary and its versions (v1 by the recorder, later ones
  by agents), action items with their follow-up state, speaker names, the
  transcript with click-to-play timestamps, timings and GPU use, "Transcribe
  again" with another engine, "New recorder summary".
- Speaker names replace "Speaker N" everywhere (transcript, summary, action
  items) at once, without a new LLM run.
- Review: follow-up proposals to approve or reject.
- Devices (admin): tokens for phones and agents, revoke.

Meeting folder (`data/meetings/<id>/`): `original.webm`, `audio.wav`,
`transcript.json` (words with start, end, speaker), `transcript.md`,
`speakers.json`, `summaries/v001.json`..., `summary.json`, `summary.md`,
`actions.json` (items with stable keys), `meta.json`, `engine/` (raw engine
output and logs). The SQLite index is `data/index.sqlite`.

## Follow-up agent

`python -m recorder.followup run` (hourly via `indie-recorder-followup.timer`,
and right after each meeting): for every meeting with a new summary version it
reads `actions.json` and `summary.json`, matches each action item against the
repo's existing issues, lets the LLM on the box decide per item (comment on an
existing issue, open a new one, or meeting log only), and adds one "Meeting
log" entry per summary version. Bodies link back to the meeting on aibox1 and
never quote the transcript.

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

## Setting up the Agent Command Center agent

The agent writes better summaries with its own context and memory, keeps the
box's copy of the repo's issues fresh, and carries out approved GitHub and
Drive follow-ups. It reads full transcripts, and an agent's run history keeps
what its tools returned, so run it in an indie-chat on aibox1 itself, with
aibox1's model. An indie-chat elsewhere (brainbot:brain on ai-knowledge) would
store the transcripts there.

On aibox1 (recorder side):

```bash
cd ~/indie-recorder
docker compose exec -T recorder python -m recorder.cli token add --name "command center" --role agent < /dev/null
# put the printed token into indie-chat's environment as INDIE_RECORDER_TOKEN
# in .env: hand follow-ups to the agent, and skip meetings from before go-live
#   FOLLOWUP_EXECUTOR=agent
#   AGENT_SUMMARIES_SINCE=2026-11-18
docker compose up -d recorder
```

In the indie-chat deployment (config directory, or `MANAGED_MCP_SERVERS` /
Settings for an Indie Box):

`config/mcp-servers.json` (a managed entry may use the box's loopback address;
a server added by hand in the UI may not):

```json
{
  "indie-recorder": {
    "url": "http://127.0.0.1:18000/mcp",
    "headers": { "Authorization": "Bearer ${INDIE_RECORDER_TOKEN}" },
    "enabled": true
  }
}
```

`config/agents.json` (or create the agent in the UI with the same
instructions; `backend` is the key in `agent-backends.json` that points at
aibox1's Ollama with `qwen3.8:27b-ctx65536`):

```json
[
  {
    "key": "meeting-followup",
    "name": "Meeting follow-up",
    "description": "Better meeting summaries and the approved GitHub and Drive follow-ups from the recorder.",
    "instructions": "<the agent instructions below>",
    "wiki": "read",
    "backend": "ollama",
    "refresh": true
  }
]
```

`config/routines.json`:

```json
[
  {
    "key": "meeting-followup-hourly",
    "title": "Meeting follow-up",
    "prompt": "Run the meeting follow-up: call pending_work and do what it lists, following your instructions.",
    "cron": "17 * * * *",
    "tzOffset": -60,
    "mode": "agent",
    "agent": "meeting-followup",
    "mcp": true,
    "search": false,
    "notify": true,
    "quiet": "[Nothing new]",
    "precheck": { "tool": "pending_work", "args": {} },
    "enabled": true
  }
]
```

`precheck` asks `pending_work` before each run; it answers with nothing when
there is nothing to do, so idle hours cost no model call. If the MCP tool list
shows the tool with a server prefix, use that name. In the routine's
settings, turn on "Start each run fresh": the work list comes from the box,
not from the conversation. Use the agents' restricted internet mode ("No
internet, except web search"): then the app makes the MCP calls, which is
what lets the loopback address work. Give the agent its GitHub connection
(only `brainbot-com/indie-recorder-sandbox` while testing) and its Google
Drive connection (only the folder `indie-recorder test`).

Agent instructions:

> You are the meeting follow-up agent for the indie-recorder on aibox1. You
> reach the recorded meetings through the indie-recorder tools.
>
> Start every run with pending_work. If it returns nothing, answer
> "[Nothing new]" and stop.
>
> 1. Issues first. If anything is pending, read the open and recently closed
>    issues of brainbot-com/indie-recorder-sandbox with your GitHub tools and
>    send them with sync_issues (number, title, body, state, html_url).
> 2. Better summaries. For each meeting under "summaries", read it with
>    get_meeting. Write a better summary in the meeting's language with what
>    you know about the people, projects and earlier meetings: a short TL;DR,
>    decisions, action items (owner and due date only where they were said),
>    open questions. Speakers are "Speaker 1", "Speaker 2" unless someone set
>    names: keep those labels; if you are fairly sure who a speaker is, say so
>    in the note, do not rename. Never invent decisions, owners or dates.
>    Store it with update_summary: markdown without a title line,
>    action_items as a list of {text, owner, due}, and a note on what you
>    changed and which context you used.
> 3. Approved follow-ups. For each item under "outbox", read it with
>    list_outbox and carry it out exactly: github_issue, open an issue in
>    payload.repo with payload.title and payload.body; github_comment, comment
>    payload.body on issue payload.issue_number; drive_log, append
>    payload.markdown to the Google Doc payload.doc in the Drive folder
>    payload.folder, creating the doc if it is missing. Post the bodies
>    unchanged: they carry a hidden marker that prevents duplicates. Then call
>    ack_outbox_item with the URL of what you created. If one fails, leave it
>    and say why.
>
> Work only in brainbot-com/indie-recorder-sandbox and the Drive folder
> "indie-recorder test". Never copy transcript text into GitHub, Drive or
> this chat; link to the meeting's url instead. End with one short line per
> meeting and per follow-up you handled.

Check: record a short meeting (or stream one with `sim/phone_sim.py`),
approve its proposals on `/review`, then "Run now" on the routine. The
meeting page shows summary version 2 by the agent, and the review page the
URLs of the issue, comment and doc.

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
can be transcribed again with another engine from its page. `A-iq4-diar`
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
- Backup: `data/` (meetings and index) and `secrets/`.

## Known issues

- The PWA cannot promise recording with the screen off or the app in the
  background; Chrome may stop the microphone. It keeps the screen on with a
  wake lock and warns. This is the main reason for a native app.
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
- NeMo-Speech.cpp issue #89 (Vulkan batching assert) concerns the realtime
  streaming batcher of `nemo-speech serve`; file transcription here runs one
  sequence at a time and diarization with `--no-batching`.
- The AMI mirror at Edinburgh refuses scripted bulk downloads; the benchmark
  takes AMI audio from the CC BY 4.0 mirror `diarizers-community/ami` on
  Hugging Face and the references from AMI's annotation package and BUT.

## Android app (spec for v2)

- Foreground service of type `microphone` with a persistent notification, so
  recording continues with the screen off; permissions `RECORD_AUDIO`,
  `FOREGROUND_SERVICE_MICROPHONE`, `POST_NOTIFICATIONS`; ask to be exempted
  from battery optimization.
- Capture 16 kHz mono (AudioRecord) and encode Opus in 30 s segment files in
  app storage; handle audio focus and phone calls (pause, mark the gap).
- Offline queue: WorkManager uploads segments with the same resumable API as
  the PWA (`POST /api/v1/recordings`, `PUT .../chunks/<n>` with
  `X-Chunk-SHA256`, `POST .../finalize`; the server reports which chunks it
  has), constraint "network connected", exponential backoff; delete a segment
  only after the box confirmed it; survives reboots.
- VPN: check that the box is reachable before and during upload (WireGuard
  app, or the WireGuard library in the app); show "waiting for VPN".
- Trust: bundle the box's CA in `network_security_config` for that host
  instead of relying on user CAs; pairing by the same QR code (token in the
  Android Keystore).
- UI: record and stop, timer, level, upload and processing state, link to the
  summary; encrypted local storage; no audio leaves the phone except to the box.

## Layout

`recorder/` service, worker, engines (clients), follow-up agent, MCP, web
templates · `pwa/` phone app · `sim/` phone simulator · `engines/` engine
containers · `bench/` benchmark · `deploy/` Caddy, CA, systemd, deploy and
model scripts.

Licenses of what the box downloads: Nemotron 3.5 Transcribe and Nemotron 3
Diarization OpenMDW-1.1, Qwen3-ASR and the aligner Apache-2.0, NeMo-Speech.cpp
Apache-2.0. Test data: AMI Meeting Corpus (University of Edinburgh et al.) and
Multilingual LibriSpeech, both CC BY 4.0.
