---
name: meeting-follow-up
description: Look after the person's meetings in their Minutes Pro through its MCP server. Write better summaries with what you know about their work, and suggest who unnamed speakers are. Use it in each run of the hourly "Meeting follow-up" task, and when the person asks to set that up.
---

# Meeting follow-up

Minutes Pro records the person's meetings with their phone, transcribes
them on their own machine (an Indie Box, or a Mac or Linux computer) and writes
a first summary. You reach it through its MCP server (named `minutes` in the
setup guide; its tools are named below). It answers for the person whose token
you use: their meetings and nothing else.

## Set up (once, when the person asks)

1. Call `status`. It names the box the recorder runs on and how many meetings
   there are. If the call fails, the MCP server or its token is not set up
   yet: say so, and stop.
2. Tell the person in one line which model you run on, because you will read
   whole meeting transcripts. With a model on their own hardware (Ollama, LM
   Studio, the box's own) the transcripts stay with them; a cloud model's
   provider receives what you read. Go on only when they agree.
3. Schedule "Each run" below once an hour with your own scheduler, as a task
   of its own whose prompt is the whole "Each run" part, so each run stands on
   its own. Say when the first run comes.

## Each run

1. Call `pending_work`. If it returns nothing, there is nothing to do: stop
   (answer "[Nothing new]" if your scheduler expects an answer). It hands you
   at most one meeting under "summaries"; "more_summaries" are for the next
   runs, so do not look for them yourself.
2. A better summary, for the meeting under "summaries":
   - `get_meeting` with `{"meeting_id": ID, "include_transcript": false}`
     gives the current summary, the speaker names and the action items.
   - The transcript: if you can run commands, `meeting_files` gives signed
     links (valid two hours); download Transcript.md with
     `curl -fsS -o <a scratch folder>/ID/transcript.md "<url>"` and read the
     file. Otherwise call `get_meeting` with `"include_transcript": true`.
     Never copy transcript text into a chat or a message.
   - A transcript of more than 80,000 characters does not fit at once: read
     it in parts, write a few lines of notes per part, and write the summary
     from your notes and the recorder's summary.
   - Write it in the meeting's language, with what you know about the people,
     projects and earlier meetings (your memory, `list_meetings` and
     `get_meeting` for earlier ones): a short TL;DR, decisions, action items
     (owner and due date only where they were said), open questions. Call
     speakers by their labels ("Speaker 2") or by the names the person set,
     never by a name you only guess, not even in brackets: the app shows the
     summary as it is. Never invent decisions, owners or dates.
   - Store it with `update_summary`: `{"meeting_id": ID, "markdown": "...",
     "action_items": [{"text": "...", "owner": "...", "due": "..."}],
     "note": "what you changed and which context you used"}` (markdown
     without a title line).
3. Speaker names, for the same meeting: when `get_meeting` shows speakers
   without a name, suggest who they are with `suggest_speakers`:
   `{"meeting_id": ID, "suggestions": {"Speaker 2": {"name": "Anna", "why": "..."}}}`.
   Base it on the transcript (introductions, people addressed by name; each
   speaker's "lines" help), your memory, and the names the person set in
   earlier meetings. "name" is only the name ("Anna", "Anna Meier"), never a
   sentence or "probably ...": doubts and reasons go into "why", and if you
   are not fairly sure, suggest nothing for that speaker. Never rename anyone:
   the person decides in the app, with your suggestion and a clip of the
   voice in front of them.
4. Items under "outbox" are Google Drive uploads that wait for an agent with
   Drive tools. If you have such tools, `list_outbox` and `drive_files` say
   what goes where; upload, then `ack_outbox_item` with the link. If you have
   none, leave them and say once that the person can connect Google Drive in
   the recorder's app, where the recorder offers it.
5. End with one short line per meeting and item you handled.
