# Meetings in brainbot:agent command

The recorder on aibox1 in brainbot:agent command (indie-chat on ai-knowledge): the
Meetings app to read and steer your meetings, and a meeting agent that writes
better summaries with what it knows and stores meetings in Google Drive.

- `indie-recorder.html`: the Meetings app (an app template). List by day, a
  meeting's summary, action items and transcript, speaker names, "Store in
  Google Drive" per meeting and the default for new recordings. English and
  German, follows brainbot:agent command's light and dark theme. Its WebMCP tools
  (`list_meetings`, `open_meeting`, `set_storage`, `set_default_storage`,
  `rename_speakers`) let the app's agent drive it.
- `install-ai-knowledge.sh`: the one-time change on ai-knowledge.

## What goes where

- Recording, transcription and the first summary happen on aibox1 only, with
  aibox1's own models.
- brainbot:agent command reaches the recorder only through
  `https://ai-knowledge.mz.brainbot.com/indie-recorder/mcp` (needs your
  recorder token) and `/indie-recorder/api/v1/files/...` (signed links that
  expire after two hours). Nothing else of the recorder is reachable there.
- Each meeting is either local or in Drive (your choice per meeting; the
  default for new recordings is a setting). A local meeting stays on aibox1:
  the Meetings app shows it through brainbot:agent command's server to your browser,
  nothing is stored on the way. A Drive meeting is uploaded by your meeting
  agent: `Summary.md`, `Action items.md`, `Transcript.md` (and the recording,
  if you switch that on) into a folder per meeting, plus an entry in the
  running "Meeting log" doc.
- The meeting agent reads full transcripts, and an agent's run history keeps
  what its tools returned. It is your private agent, so that history is
  yours, on ai-knowledge. Give it aibox1's model (below), never StudioLLM:
  then no transcript goes to another model host.

## Install (once, an admin of ai-knowledge)

On aibox1 this is already set (`~/indie-recorder/.env`): `ALLOW_CIDRS`
admits ai-knowledge (10.1.1.26), `FILES_BASE_URL` points at the route below,
`FOLLOWUP_EXECUTOR=agent`, `FOLLOWUP_TARGETS=drive`, `STORAGE_DEFAULT=drive`.

From a checkout of this repo:

```bash
scp -r integrations/indie-chat root@ai-knowledge.mz.brainbot.com:/root/indie-recorder-kit
ssh root@ai-knowledge.mz.brainbot.com bash /root/indie-recorder-kit/install-ai-knowledge.sh
```

It fetches the recorder's CA certificate from aibox1 and refuses it unless its
SHA-256 fingerprint is the recorder's (`E6:89:A7:83:...:07:F1:2D:9F`), then:

1. adds the route above to `/opt/kb/brain-tls/Caddyfile` (validated, then a
   graceful reload; on a validation error the file is put back),
2. adds `ai-knowledge.mz.brainbot.com` to `INDIE_PRIVATE_HOSTS` in
   `/opt/kb/indiechat-src/.env`, so a person's own MCP server may point there
   (the name resolves to the private 10.1.1.26),
3. puts the Meetings template into `/opt/kb/indiechat-config` (no `seed`:
   it waits in the template picker, nobody gets it unasked),
4. recreates the indie-chat app container (a few seconds) and checks that
   brainbot:agent command answers again; if not, it puts `.env` and the templates back.

Each changed file is backed up once as `<file>.pre-indie-recorder`. Running it
again changes nothing that is already in place, except a newer
`indie-recorder.html`, which it copies and loads (apps made from the template
follow it at the next start, `refresh: true`).

To remove it: copy the three `.pre-indie-recorder` files back (the Caddyfile
in place, `cat Caddyfile.pre-indie-recorder > Caddyfile`, then `docker exec
brain-tls caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`),
delete `/opt/kb/indiechat-config/app-templates/indie-recorder.html` and run
`docker compose -p indiechat -f /opt/kb/indiechat-src/docker-compose.ai-knowledge.yml up -d --force-recreate app`.

## For each person

1. **A recorder token.** On aibox1, `cd ~/indie-recorder && docker compose
   exec -T recorder python -m recorder.cli token add --name "agent command
   <name>" --role agent < /dev/null` prints one (or an admin makes one on the
   recorder's Devices page). Luis's is in `~/indie-recorder/secrets/command_center_token`.
2. **Your own MCP server.** brainbot:agent command's form for MCP servers has no field
   for a header yet, so add it once from the browser console: open
   https://ai-knowledge.mz.brainbot.com, press F12, Console, and run

   ```js
   await IndieLib.McpServersAPI.create({
     name: "indie-recorder", scope: "user",
     url: "https://ai-knowledge.mz.brainbot.com/indie-recorder/mcp",
     headers: { Authorization: "Bearer PASTE-THE-TOKEN-HERE" },
   })
   ```

   Reload the page. Settings, Integrations lists `indie-recorder` as yours:
   only your own agents and apps get it.
3. **The Meetings app.** New, From a template, Meetings. It finds the server
   by its tools; the dot at the top turns green when it reaches aibox1.
4. **The meeting agent** (needed for Drive and for the better summaries):
   - New agent, name it "Meeting follow-up". Engine **aibox1 Ollama (64k)**,
     model **qwen3.8:27b-ctx65536** (that exact tag: the box keeps it loaded
     for other work, another tag loads a second copy).
   - Connections: Google Drive, with changes ("write"), for the account the
     meetings should go to.
   - Instructions: the text below.
   - Schedule: hourly (for example minute 17), "Start each run fresh" on,
     notifications as you like. Prompt: `Run the meeting follow-up.`
   - The pre-check (skip the hour without a model call when there is nothing
     to do) has no field in the editor yet. In the console:

     ```js
     const r = (await IndieLib.RoutinesAPI.list()).routines.find(x => x.title === "Meeting follow-up");
     await IndieLib.RoutinesAPI.update(r.id, { precheck: { tool: "pending_work", args: {} }, quiet: "[Nothing new]" });
     ```

     (Use the routine's title as you named it.)

### Agent instructions

> You look after the meetings recorded with the indie recorder on aibox1. You
> reach them with the indie-recorder tools and Google Drive with the drive
> tools.
>
> Start every run with pending_work. If it returns nothing, answer
> "[Nothing new]" and stop.
>
> 1. Better summaries. For each meeting under "summaries", read it with
>    get_meeting. Write a better summary in the meeting's language with what
>    you know about the people, projects and earlier meetings (your memory,
>    the wiki, list_meetings and get_meeting for earlier ones): a short TL;DR,
>    decisions, action items (owner and due date only where they were said),
>    open questions. Speakers are "Speaker 1", "Speaker 2" unless someone set
>    names: keep those labels; if you are fairly sure who a speaker is, say so
>    in the note, do not rename. Never invent decisions, owners or dates.
>    Store it with update_summary: markdown without a title line,
>    action_items as a list of {text, owner, due}, and a note on what you
>    changed and which context you used.
> 2. Drive. For each item under "outbox", carry it out:
>    - drive_store: call drive_files with its proposal_id. Find or create the
>      folder named "folder" in My Drive and in it the subfolder named
>      "subfolder". For each file, download it with
>      `curl -fsS -o "/workspace/meetings/<subfolder>/<name>" "<url>"`
>      (make the directory first), then put it into the subfolder with
>      drive_upload. If the subfolder already holds a file of that name, move
>      the old one into a subfolder "Older versions" first (create it if
>      missing). Then ack_outbox_item with the subfolder's link.
>    - drive_log: find the Google Doc named payload.doc in the folder
>      payload.folder (create it with drive_create if missing), add
>      payload.markdown at the end and keep what is there, then
>      ack_outbox_item with the doc's link.
>    If one fails, leave it for the next run and say why.
>
> Never copy transcript text into this chat or anywhere but the files you
> were given. End with one short line per meeting and per item you handled.

### Check

Record a short meeting with the phone (or `sim/phone_sim.py`), with "Save to
Google Drive" on. When the recorder is done, "Run now" on the routine: the
meeting's folder appears in Drive, the meeting log gets an entry, and the
Meetings app shows summary version 2 and "Open folder".
