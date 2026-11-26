# Minutes Pro

A private meeting recorder. Your phone records a meeting, and a machine you own
transcribes it, with who said what, and writes the minutes with its own
language model. Recordings, transcripts and summaries stay on that machine,
encrypted, unless you choose to send a meeting somewhere.

```
phone: Android app or web app            the recorder, on a machine you own
  records in 30 s chunks, each      --HTTPS-->  speech: Nemotron 3.5 Transcribe, NeMo-Speech.cpp on the GPU
  encrypted before it is stored                  minutes: the machine's own model (Ollama)
  or sent; uploads resume                        meetings app in the browser, encrypted storage
                                                 MCP for your own agent: better summaries, speaker names
```

## Where it runs

### On an Indie Box

The intended home. An Indie Box has the GPU, its own models and HTTPS, and
installs the recorder from its catalog and keeps it updated:

1. In the box's admin, Apps: find Minutes Pro in the catalog and install
   it. The form asks for a password and an encryption key: press Generate for
   both, note the password, and keep a copy of the key somewhere safe (without
   it the meetings cannot be read).
2. On its first start the speech engine downloads its model (about 21 GB,
   checked file by file). Until then the recorder says "Getting ready", and
   recordings wait for it.
3. Open Minutes Pro from the box's start page and sign in with the password.
4. Phones record only over HTTPS: in Admin, HTTPS, choose "Internal
   certificate" (then install the box's certificate authority on each phone
   once) or "Automatic HTTPS".
5. Settings, Connect a phone: scan the code with the phone, then record in
   the browser (add the page to the home screen) or install the Android app,
   which the connect page offers from GitHub.

Updates come from the catalog like every box app; the meetings and the model
stay. The recorder uses the box's GPU for speech and the box's default model
for the minutes (another one in the app's settings). Only a model of the box
itself gets a transcript: one the box only relays to a remote provider is
refused. The app file is [indiebox/indie-minutes-pro.compose.yml](indiebox/indie-minutes-pro.compose.yml).

### On your own computer

A Linux computer with a GPU, or a Mac with Apple silicon (the speech engine
runs natively with Metal there), with Docker and Ollama:
[standalone/README.md](standalone/README.md).

### aibox1 at brainbot

Where the recorder started: phones over the VPN, two speech engines, the
Minutes Pro app and meeting agents in brainbot:agent command, Google Drive.
See [docs/aibox1.md](docs/aibox1.md).

## Your agent

The recorder writes a first summary itself. Your own agent can write a better
one with what it knows about your work, and suggest who the unnamed speakers
are; you decide in the app.

- **Any agent with MCP**: Claude Code, Claude Desktop, OpenClaw, Hermes Agent
  and others. In the recorder, Settings, Meeting agent: make a token, then
  follow [agents/README.md](agents/README.md) (the recorder shows it at
  `/agent/` with its own address filled in). The skill the agent follows is
  [agents/meeting-follow-up/SKILL.md](agents/meeting-follow-up/SKILL.md).
- **brainbot:agent command**: the Minutes Pro app sets up the person's own
  agent with one button ([integrations/indie-chat](integrations/indie-chat/README.md)).

The agent reads whole transcripts. With a model on your own hardware they stay
with you; a cloud model's provider gets what the agent reads.

## A team

The owner records and manages the meetings: one person who records, names the
speakers and deletes what should go. Everyone else in the team reads. In the
meetings app, Settings, Team: invite people by name. Each gets a link, valid
once for seven days, to choose their own password, and then signs in with their
name. They read the meetings the owner shares with the team: every new one
unless the owner switches that off, with a Team switch on each meeting. A new
link resets someone's password; removing them ends their sessions.

## Consent and privacy

- Recording others needs their consent: the phone apps say "Tell everyone
  first" on the Record button and ask once on each phone before the first
  recording. In Germany, recording the non-public spoken word without consent
  is a crime (§ 201 StGB).
- `/privacy` on the recorder (linked from every page and app) says what is
  stored, where, who sees it, how long, and how to ask for deletion, with the
  operator's contact (`PRIVACY_CONTACT`, on an Indie Box a field of the
  install form).
- The owner deletes a meeting for good from its page (More, Delete meeting):
  recording, transcript, summaries and everything the box kept about it.
- For companies: a template for the data protection impact assessment,
  [docs/dpia-template.md](docs/dpia-template.md) (German:
  [docs/dsfa-vorlage.md](docs/dsfa-vorlage.md)). It is not legal advice.

## What stays where

- Speech recognition, speaker labels and the first summary run on the
  recorder's machine with its own models. The recorder makes no cloud calls
  for them, and refuses a model an Indie Box would relay to a remote provider
  (`LLM_REMOTE_OK=1` allows it). On a box (`LLM_LOCAL_ONLY=1` in the app file)
  the model must be one the box lists as its own: without that answer, summaries wait.
- Each person sees only their own meetings: a person's phones record into
  their account. A team member sees the meetings the owner shares.
- A browser signs in with a cookie and a key the page keeps; the APIs take
  tokens only in the Authorization header. On an Indie Box every app shares
  the host name, and cookies ignore ports: the key is what other apps there
  never see.
- Speakers stay "Speaker 1..N" until a person names them; an agent only
  suggests names. No voiceprints are kept.
- Google Drive is optional and only where a Google sign-in is set up (aibox1):
  then a person chooses Drive per meeting.

## Encryption

- On the phone: every 30 s chunk is encrypted (AES-256-GCM) with a key agreed
  with the recorder's sealing key (ECDH P-256) before it is stored or sent.
- On the way: HTTPS on every hop; the Android app pins the recorder's
  certificate authority where the recorder has one of its own.
- On the machine: every meeting file is AES-256-GCM in 1 MiB segments
  (`recorder/vault.py`) and the index is SQLCipher. The speech engine gets
  plain audio only in RAM (a tmpfs) for the length of a job.
- The key: one secret, `DATA_KEY` (an Indie Box's install form, or `.env`),
  from which the phones' sealing key is derived; or the two files
  `deploy/make-keys.sh` makes (aibox1). The recorder refuses to start without
  it, or with another key than the data was written with.

## The apps

Every app shows a person's meetings the same way, from one set of views
(`recorder/static/meetings.js` and `meetings.css`): the meetings by day, a
meeting with its summary, speaker names (play a voice, name it) and the
transcript.

- **In a browser**: the meetings app at the recorder's address, after signing
  in with the password (`OWNER_PASSWORD`). Settings there connects phones and
  gives agents their tokens.
- **Phone web app** (`/app/`): any phone with a current browser; records,
  keeps chunks until the recorder has them, lists the meetings.
- **Android app** (`android/`): also records with the screen off; see
  [docs/aibox1.md](docs/aibox1.md#android-app).
- **Minutes Pro in brainbot:agent command**: the same app, through its
  connector.

## Settings

The ones that differ between an Indie Box, your own computer and aibox1 (all
in `recorder/config.py`):

| Variable | What it does |
|---|---|
| `DATA_KEY` / `DATA_KEY_FILE` | the encryption key (base64url or hex), or the file holding it |
| `OWNER_PASSWORD`, `OWNER_NAME` | signing in with a password as the recorder's owner; without it, tokens only |
| `PUBLIC_URL` | the recorder's address for links; `auto` follows the address a request came in on (behind a proxy) |
| `FORWARDED_ALLOW_IPS` | proxies whose `X-Forwarded-Proto` counts |
| `BOX_NAME`, `PHONE_REACH` | what the apps call the machine, and whether phones reach it over the `vpn` or the `lan` |
| `OLLAMA_URL`, `LLM_MODEL`, `LLM_CONTEXT` | the model for the minutes, and how much it reads at once (on a box: what the box says) |
| `LLM_LOCAL_ONLY`, `LLM_REMOTE_OK` | on an Indie Box: only a model the box lists as local; or allow a remote one after all |
| `NEMO_URL`, `QWEN_URL` | the speech engines; `off` for one that is not there |
| `DRIVE`, `GOOGLE_OAUTH_FILE` | Google Drive per meeting: `DRIVE=0` hides it where no one carries it out |
| `GPU_LOCK` | one GPU job at a time: the lock the engine and the recorder share |

The speech engine: `FETCH_MODELS=iq4` downloads its model on start,
`NEMO_DEVICE` picks the GPU (`vulkan:0`, `metal`, `cuda:0`, `cpu`), and
`PATH_MAP` tells an engine outside the containers where the recorder's files
are (a Mac).

## Development

- Tests: `docker build -t minutes-pro . && docker run --rm minutes-pro
  python -m pytest -q tests`.
- Images: `.github/workflows/publish-images.yml` tests, then publishes
  `ghcr.io/brainbot-com/indie-minutes-pro` (amd64 and arm64) and
  `indie-minutes-pro-engine` (amd64) with `sha-<commit>`, and `latest` from main.
- A release for the Indie Box catalog: Actions, Release (dry-run first). It
  builds and tests, tags both images with the version, pins it in the app file
  (`indiebox/pin-images.sh`) and proposes it to the catalog in
  brainbot-com/indie-box. Before the first one: the repository secret
  `INDIE_BOX_TOKEN`, read access for the catalog's sync to this repository,
  the catalog scope (`INDIEBOX_REGISTRY_SCOPE`, `INDIEBOX_REGISTRY_AUDIENCE`
  for a private app), and the two GHCR packages public (boxes pull without
  credentials).

## Layout

`recorder/` the service: upload API, worker, the meetings app, MCP ·
`engines/` the speech engine containers · `pwa/` phone web app ·
`android/` Android app · `indiebox/` the Indie Box app file ·
`standalone/` your own computer · `agents/` setup for any agent and its skill ·
`integrations/` brainbot:agent command · `deploy/` aibox1's Caddy, CA, keys
and scripts · `sim/` phone simulator · `bench/` benchmark ([BENCHMARK.md](BENCHMARK.md)).

## License

Apache License 2.0 ([LICENSE](LICENSE)). What Minutes Pro downloads and
includes, under its own licenses, is listed in [NOTICE](NOTICE): Nemotron 3.5
Transcribe (OpenMDW-1.1), NeMo-Speech.cpp (Apache-2.0), the Inter font (SIL
OFL 1.1).
