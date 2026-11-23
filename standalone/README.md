# Indie Recorder on your own computer

For anyone without an Indie Box: the recorder on a Linux computer with a GPU,
or on a Mac with Apple silicon. It is the same recorder an Indie Box installs
from its catalog; the box does the steps below by itself, which is why it is
the easier home for it ([README](../README.md#on-an-indie-box)).

What you need:

- **Linux**: Docker with Compose, an AMD or Intel GPU with Vulkan (tested on a
  Radeon 8060S), about 30 GB free disk and 32 GB of memory or more. Without a
  usable GPU it runs on the CPU, at about half real time.
- **Mac**: Apple silicon with 32 GB of memory or more (64 GB is comfortable),
  Docker Desktop (or OrbStack), about 30 GB free disk. The speech engine runs
  natively there, with Metal; the rest runs in Docker.
- **A model for the summaries**: [Ollama](https://ollama.com) on the same
  computer (or another one you own), with a model that writes in the
  languages of your meetings, for example `ollama pull qwen3:14b`.

## Set it up

1. Get the recorder and fill in two values:

   ```bash
   git clone https://github.com/brainbot-com/indie-recorder.git
   cd indie-recorder/standalone
   cp .env.example .env
   ```

   In `.env`, `RECORDER_PASSWORD` is what you sign in with, and
   `RECORDER_DATA_KEY` encrypts every meeting; make one with
   `openssl rand -base64 32 | tr '+/' '-_' | tr -d '='` and keep a copy
   somewhere safe. On a Mac, switch to the three Mac lines in `.env`.

2. HTTPS for the phones (browsers let a page use the microphone only over
   HTTPS). This makes your own certificate authority and a certificate for this
   computer's address and names:

   ```bash
   BIND_IP=192.168.1.20 TLS_DNS_NAMES="mycomputer mycomputer.local" ../deploy/make-ca.sh secrets/tls
   ```

   It prints the authority's fingerprint. On a Mac the script uses the
   system's LibreSSL; if that refuses an option, run it with Homebrew's
   OpenSSL first in the `PATH`.

3. Ollama has to be reachable from Docker. On a Mac it is. On Linux it listens
   on 127.0.0.1 only: let it listen on all addresses (`OLLAMA_HOST=0.0.0.0` in
   its service, with port 11434 closed to the network in your firewall), or
   set `OLLAMA_URL` in `.env` to where it runs.

4. Start it:

   - **Linux**: `docker compose up -d`. The engine downloads its model on the
     first start, about 21 GB, and checks each file; `docker compose logs -f
     engine` shows how far it is.
   - **Mac**: in one terminal `mac/engine.sh` (it downloads the engine and the
     model on the first start, then keeps running), then `docker compose up -d`.

5. Open `https://<this computer>:8443` in a browser, accept or trust the
   certificate, and sign in with your password. Until the model is there, the
   status says "Getting ready" and recordings wait for it.

## Phones

1. Put the phone on the same network as the computer.
2. Install your certificate authority on the phone once: open
   `http://<this computer>:8480/ca.crt` on it, then on Android Settings,
   Security and privacy, More security settings, Install a certificate, CA
   certificate; on an iPhone, install the profile and then turn on full trust
   for it under General, About, Certificate Trust Settings. Compare the
   fingerprint with the one the script printed.
3. In the recorder, Settings, Connect a phone. Scan the code with the phone and
   choose Record in the browser; add the page to the home screen.

## Your own agent

The recorder writes a first summary itself. Your own agent (Claude Code,
OpenClaw, Hermes or any other with MCP) can write better ones and suggest who
the speakers are: Settings, Meeting agent, then [agents/README.md](../agents/README.md),
which the recorder also shows at `https://<this computer>:8443/agent/` with
its address filled in.

## Updating

`docker compose pull && docker compose up -d`. `RECORDER_TAG` in `.env` pins a
release instead of the latest build. The data in `data/` and the model in
`models/` stay; keep `RECORDER_DATA_KEY` the same.

## Where things are

- `data/`: the meetings, encrypted with `RECORDER_DATA_KEY`, and the index.
- `models/`: the speech model (Nemotron 3.5 Transcribe 30B-A4B, AD-IQ4_NL).
- `secrets/tls/`: your certificate authority (keep `ca.key` private) and the
  certificate Caddy serves.
- On Linux the audio of a running job is in RAM only (a tmpfs). On a Mac it
  passes through `scratch/`, which the native engine reads, and is deleted
  when the job ends; FileVault keeps it encrypted on the disk.
