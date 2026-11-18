# indie-recorder in brainbot:agent command

The recorder on aibox1 in brainbot:agent command (indie-chat on ai-knowledge): the
indie-recorder app to read and steer your meetings, and a meeting agent that writes
better summaries with what it knows and stores meetings in Google Drive.

- `indie-recorder.html`: the app (named indie-recorder in the sidebar), one shared app for everyone. Each
  person sees only their own meetings: list by day, a meeting's summary,
  decisions, action items and transcript, speaker names, "Google Drive" per
  meeting. Settings: the person's name (their phones show it), the storage
  defaults, and Phones. "Connect a phone" shows one QR code and the same
  code to type, waits, and says "Connected: <phone>" when the phone has used
  it; remove a phone to lock it out (see "Connecting a phone" in the main
  README). English and German,
  light and dark as brainbot:agent command sets them; the same Swiss look as
  the phone apps. Its WebMCP tools (`list_meetings`, `open_meeting`,
  `set_storage`, `set_default_storage`, `rename_speakers`) let the app's agent
  drive it; pairing is not one of them (an agent cannot add a phone).
- `inline_assets.py`: puts `recorder/static/swiss.css` and the Inter font into
  the app (it loads nothing from the box). Run it after changing either and
  commit the result.
- `install-ai-knowledge.sh`: the change on ai-knowledge.

## What goes where

- Recording, transcription and the first summary happen on aibox1 only, with
  aibox1's own models.
- brainbot:agent command reaches the recorder only through these paths on
  `https://ai-knowledge.mz.brainbot.com/indie-recorder/`: `api/v1/indiechat`
  (the "indie-recorder" connector: the deployment's service token, and
  brainbot:agent command names the signed-in person in `X-Indie-User`),
  `mcp` (a person's own recorder token, for their meeting agent),
  `api/v1/files/...` (signed links that expire after two hours), and for
  phones: `connect` (the page a connect code opens), `api/v1/pair` and
  `api/v1/pair/{code}` (a code, once, within 15 minutes),
  `indie-recorder.apk` and `static/...` (the page's stylesheet). Nothing else
  of the recorder is reachable there; recordings go from the phone to aibox1
  directly.
- The recorder knows people by their brainbot:agent command id (it gets no
  name or email); a person's phones and meetings belong to that id. Only a
  person, not an agent, can connect or remove phones or set their name.
- Each meeting is either local or in Drive (your choice per meeting; the
  default for new recordings is a setting). A local meeting stays on aibox1:
  the indie-recorder app shows it through brainbot:agent command's server to your browser,
  nothing is stored on the way. A Drive meeting is uploaded by your meeting
  agent: `Summary.md`, `Action items.md`, `Transcript.md` (and the recording,
  if you switch that on) into a folder per meeting, plus an entry in the
  running "Meeting log" doc. Without a meeting agent of your own, a Drive
  meeting waits ("Waiting for the Drive upload") and stays on aibox1.
- The meeting agent reads full transcripts, and an agent's run history keeps
  what its tools returned. It is your private agent, so that history is
  yours, on ai-knowledge. Give it aibox1's model (below), never StudioLLM:
  then no transcript goes to another model host.

## Install (an admin of ai-knowledge)

On aibox1 this is already set (`~/indie-recorder/.env`): `ALLOW_CIDRS`
admits ai-knowledge (10.1.1.26), `FILES_BASE_URL` points at the route below,
`FOLLOWUP_EXECUTOR=agent`, `FOLLOWUP_TARGETS=drive`, `STORAGE_DEFAULT=drive`,
`CONNECT_URL` and `APK_URL` point at the brain-tls address. The connector's
service token is made once on aibox1 with
`python -m recorder.cli token add --name "brainbot:agent command" --role agent
--trusts-identity` and kept in `secrets/indiechat_service_token`.

From a checkout of this repo:

```bash
scp -r integrations/indie-chat root@ai-knowledge.mz.brainbot.com:/root/indie-recorder-kit
ssh aibox1 cat indie-recorder/secrets/indiechat_service_token \
  | ssh root@ai-knowledge.mz.brainbot.com bash /root/indie-recorder-kit/install-ai-knowledge.sh --token-stdin
```

The token travels on stdin, never on a command line; without `--token-stdin`
the script keeps the one already set. It fetches the recorder's CA
certificate from aibox1 and refuses it unless its SHA-256 fingerprint is the
recorder's (`E6:89:A7:83:...:07:F1:2D:9F`), then:

1. routes the paths above in `/opt/kb/brain-tls/Caddyfile` to aibox1,
   trusting only the recorder's CA (validated, then a graceful reload; on a
   validation error the file is put back),
2. adds `ai-knowledge.mz.brainbot.com` to `INDIE_PRIVATE_HOSTS` and sets
   `INDIE_RECORDER_TOKEN` in `/opt/kb/indiechat-src/.env`,
3. adds the "indie-recorder" connector (with `identity: true`) to
   `connectors.json`,
4. puts the Meetings template into `/opt/kb/indiechat-config`, seeded as one
   shared app for everyone and refreshed from the template at every start,
5. recreates the indie-chat app container (a few seconds) and checks that
   brainbot:agent command answers again; if not, it puts `.env`, the
   connectors and the templates back.

Each changed file is backed up once as `<file>.pre-indie-recorder`. Running it
again changes nothing that is already in place, except a newer
`indie-recorder.html`, which it copies and loads.

To remove it: copy the `.pre-indie-recorder` files back (the Caddyfile in
place, `cat Caddyfile.pre-indie-recorder > Caddyfile`, then `docker exec
brain-tls caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`),
delete `/opt/kb/indiechat-config/app-templates/indie-recorder.html` and run
`docker compose -p indiechat -f /opt/kb/indiechat-src/docker-compose.ai-knowledge.yml up -d --force-recreate app`.

## For each person

The indie-recorder app is in everyone's sidebar. To record:

1. **Your name.** indie-recorder, Settings, Your name: your phones show it, so you
   can see whose account they record into.
2. **A phone.** indie-recorder, Connect a phone: scan the code with the phone's
   camera (VPN on) and follow the page it opens ("Open the app", "Install the
   app" or "Record in the browser"), or type the code in the app. Remove a
   phone under Settings, Phones to lock it out.

For Drive and the better summaries, each person sets up their own meeting
agent from the app (Settings, Meeting agent, or "Set it up" on the main
screen when meetings wait for Drive):

1. In indie-recorder, on the right: New agent for this app (or link one of
   theirs).
2. In that agent's settings: engine **aibox1 Ollama (64k)**, model
   **qwen3.8:27b-ctx65536** (that exact tag: the box keeps it loaded for other
   work). Each run reads whole transcripts, and they stay on aibox1.
3. "Set up my meeting agent". The button asks the agent to read the skill
   and do its set-up part: it asks for Google Drive (with changes) and
   creates the hourly routine "Meeting follow-up"; the person confirms both.

The skill is [recorder/skills/meeting-follow-up/SKILL.md](../../recorder/skills/meeting-follow-up/SKILL.md),
served by the recorder as the connector's `agent_setup`, so it ships with the
recorder. The agent reaches the recorder through the deployment's connector
as that person (`data_connector`, connector `indie-recorder`): no recorder
token and no MCP server of its own. It downloads transcripts through signed
links (`meeting_files`), since a connector answer is cut at 12,000
characters. An admin can also add the SKILL.md as a skill for everyone
(Settings, Skills), so any agent knows it.

Limits in brainbot:agent command today: an agent cannot pick its own engine
(step 2 is the person's) and cannot give its routine a precheck, so each
hourly run asks the model once even when there is nothing to do; it answers
"[Nothing new]" and sends no notification.

Without the connector (another deployment), a person's agent can use the
recorder as an MCP server instead: a token from `recorder.cli token add
--role agent --user <id>`, added from the browser console with
`IndieLib.McpServersAPI.create({name: "indie-recorder", scope: "user", url:
"https://ai-knowledge.mz.brainbot.com/indie-recorder/mcp", headers:
{Authorization: "Bearer <token>"}})`; the routine's precheck can then be
`pending_work` (`IndieLib.RoutinesAPI.update(id, {precheck: {tool:
"pending_work", args: {}}, quiet: "[Nothing new]"})`). The MCP tools are the
connector's ops.

### Check

Record a short meeting with the phone (or `sim/phone_sim.py`), with "Save to
Google Drive" on. When the recorder is done, "Run now" on the routine: the
meeting's folder appears in Drive, the meeting log gets an entry, and
indie-recorder shows summary version 2 and "Open folder".
