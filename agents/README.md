# Your own agent for the recorder

The recorder writes a first summary of every meeting itself. Your own agent can
write a better one, because it knows your projects and the people you work
with, and it can suggest who the unnamed speakers are. Any agent that speaks
MCP will do. This page sets up Claude Code, Claude Desktop, OpenClaw and Hermes
Agent; the commands were checked against their documentation on 2026-11-23.

In brainbot:agent command none of this is needed: the indie-recorder app sets
up your agent there with one button.

## What every agent needs

1. **A token.** In the recorder, open Settings, then Meeting agent: give the
   agent a name and press Make a token. Copy the token right away, it is shown
   only once. Give each agent a token of its own; Remove there ends one.
2. **The MCP server**: `https://recorder.example:9207/mcp`, streamable HTTP,
   with the header `Authorization: Bearer <token>`.
3. **The skill**: [`/agent/SKILL.md`](https://recorder.example:9207/agent/SKILL.md),
   saved as `meeting-follow-up/SKILL.md` in the agent's skills folder. It tells
   the agent what to do in each run.
4. **A run every hour** that uses the skill.

**Where the transcripts go.** The agent reads whole transcripts. With a model
on your own hardware (Ollama, LM Studio, the Indie Box's own models) they stay
with you. With a cloud model (Claude, GPT, Gemini) its provider receives the
transcripts the agent reads. The recorder itself never sends them anywhere.

**HTTPS with the box's own certificate authority.** If the recorder runs on an
Indie Box with an internal certificate, the agent's computer has to trust the
box's certificate authority: for Claude Code, Claude Desktop and OpenClaw set
`NODE_EXTRA_CA_CERTS=/path/to/box-ca.pem`; Hermes takes `ssl_verify` in its
config (below).

In the commands below, put in your token for `<token>`.

## Claude Code

```bash
claude mcp add --transport http --scope user recorder \
  https://recorder.example:9207/mcp --header "Authorization: Bearer <token>"
mkdir -p ~/.claude/skills/meeting-follow-up
curl -fsS -o ~/.claude/skills/meeting-follow-up/SKILL.md \
  https://recorder.example:9207/agent/SKILL.md
```

Every hour, either in the Claude desktop app (Scheduled, New task, Local,
Hourly, with the prompt "Use the meeting-follow-up skill: do one run"; it runs
while the app is open and the computer awake), or with cron:

```
0 * * * * /usr/local/bin/claude -p "Use the meeting-follow-up skill: do one run." --allowedTools "mcp__recorder__*" >> ~/meeting-follow-up.log 2>&1
```

A `-p` run refuses tools that would ask first, so the recorder's tools are
allowed by name. For cron, sign in with `claude setup-token` and set
`CLAUDE_CODE_OAUTH_TOKEN` in the crontab. Claude Code runs on Claude, a cloud
model: see "Where the transcripts go".

## Claude Desktop

Claude's custom connectors connect from Anthropic's cloud, which cannot reach a
recorder on your own network. Use the local bridge
[mcp-remote](https://github.com/geelen/mcp-remote) instead (it needs Node.js).
In Settings, Developer, Edit Config, add to `claude_desktop_config.json`:

```json
{"mcpServers": {"recorder": {
  "command": "npx",
  "args": ["mcp-remote", "https://recorder.example:9207/mcp",
           "--header", "Authorization: Bearer ${AUTH_TOKEN}"],
  "env": {"AUTH_TOKEN": "<token>"}}}}
```

Then ask Claude about your meetings in a chat. The hourly follow-up is Claude
Code's job (above).

## OpenClaw

```bash
openclaw mcp add recorder --url https://recorder.example:9207/mcp \
  --transport streamable-http --header "Authorization=Bearer <token>"
openclaw mcp doctor recorder --probe
mkdir -p ~/.openclaw/skills/meeting-follow-up
curl -fsS -o ~/.openclaw/skills/meeting-follow-up/SKILL.md \
  https://recorder.example:9207/agent/SKILL.md
openclaw automations add --name "Meeting follow-up" --every 1h --session isolated \
  --message "Use the meeting-follow-up skill: do one run." --no-deliver
```

With a local model the transcripts stay with you: run `openclaw onboard` and
choose Ollama, or set the provider in `~/.openclaw/openclaw.json` with
Ollama's own address (`http://<host>:11434`, without `/v1`, which breaks tool
calls there).

## Hermes Agent

```bash
hermes mcp add recorder --url https://recorder.example:9207/mcp --auth header
mkdir -p ~/.hermes/skills/meetings/meeting-follow-up
curl -fsS -o ~/.hermes/skills/meetings/meeting-follow-up/SKILL.md \
  https://recorder.example:9207/agent/SKILL.md
hermes cron create "every 1h" "Do one run of the meeting follow-up." \
  --skill meeting-follow-up --name "Meeting follow-up"
hermes gateway install
```

`hermes mcp add` asks for the token. By hand, in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  recorder:
    url: "https://recorder.example:9207/mcp"
    headers:
      Authorization: "Bearer ${MCP_RECORDER_API_KEY}"
    # ssl_verify: "/path/to/box-ca.pem"
```

with the token in `~/.hermes/.env` as `MCP_RECORDER_API_KEY`, then `/reload-mcp`
in a chat. Cron jobs run inside the gateway, hence `hermes gateway install`.
For a local model, point Hermes at Ollama's OpenAI address
(`http://<host>:11434/v1`) as a custom provider; it needs a context of at
least 64k tokens, more than Ollama's default.

## Any other agent

The server speaks MCP over streamable HTTP with plain JSON answers and no
sessions, and takes the token as `Authorization: Bearer <token>`. An agent that
only starts local (stdio) servers can use mcp-remote as above. The skill is
plain Markdown: use it as the instructions of the agent's hourly task. Its main
tools are `pending_work`, `get_meeting`, `meeting_files`, `update_summary` and
`suggest_speakers`; `tools/list` describes them all.
