---
name: meeting-follow-up
description: Look after the person's meetings from Minutes Pro on aibox1. Write better summaries with what you know, and put the meetings they keep in Google Drive there. Use it when the person asks to set up the meeting follow-up, and in each run of the "Meeting follow-up" routine.
---

# Meeting follow-up

You reach the recorder with `data_connector`: connector `indie-recorder`,
params a JSON object `{"op": "<op>", "args": {...}}`. It answers for the
person you work for: their meetings, their outbox, their settings. Google
Drive is the person's own, through your Drive tools.

## Set up (once, when the person asks)

1. Engine. Each run reads whole meeting transcripts, and they must stay on
   aibox1. Ask the person (ask_person) whether your engine is "aibox1 Ollama
   (64k)" with the model qwen3.8:27b-ctx65536. If it is not, tell them to
   change it in your settings under Engine, and go on when they confirm.
2. Google Drive is the recorder's job: once the person connects their
   Google Drive in the Minutes Pro app (Settings, Storage, Connect Google
   Drive), the recorder puts the meetings they keep in Drive there itself.
   If `{"op": "status"}` shows "google" not connected, tell them where to
   connect it. You need no Drive tools for it.
3. The routine. Create it with routine_create: title "Meeting follow-up",
   schedule every hour, quiet "[Nothing new]", appId "" (no app: a routine
   that keeps the shared app up to date gets none of the person's own
   accounts, so no Drive), and as its prompt the whole text of "Each run"
   below, so each run stands on its own.
4. Say in one or two lines what is set up and that the first run comes
   within the hour.

## Each run

1. `{"op": "pending_work"}`. If it returns nothing, answer "[Nothing new]"
   and stop. It hands you at most one meeting under "summaries" (a
   transcript fills much of your context); "more_summaries" are for the next
   runs, so do not look for them yourself.
2. A better summary, for the meeting under "summaries":
   - `{"op": "get_meeting", "args": {"meeting_id": ID, "include_transcript": false}}`
     gives the current summary, the speaker names and the action items.
   - `{"op": "meeting_files", "args": {"meeting_id": ID}}` gives signed links
     (two hours). Download the transcript with
     `mkdir -p /workspace/meetings/ID && curl -fsS -o /workspace/meetings/ID/transcript.md "<url>"`
     and read the file. Never copy transcript text into the chat.
   - A long meeting does not fit your context at once: if the transcript has
     more than 80,000 characters (`wc -c`), split it
     (`split -C 60000 transcript.md part-`), read one part at a time, write a
     few lines of notes per part into /workspace/meetings/ID/notes.md, and
     write the summary from your notes and the recorder's summary. If you
     lose track, read your notes again, not the transcript.
   - Write a better summary in the meeting's language with what you know
     about the people, projects and earlier meetings (your memory, the wiki,
     `list_meetings` and `get_meeting` for earlier ones): a short TL;DR,
     decisions, action items (owner and due date only where they were said),
     open questions. Call speakers by their labels ("Speaker 2") or by the
     names the person set, never by a name you only guess, not even in
     brackets: the app shows the summary as it is, and a guess there reads
     like a set name. Guesses go into suggest_speakers (step 3). Never invent
     decisions, owners or dates.
   - Store it: `{"op": "update_summary", "args": {"meeting_id": ID, "markdown": "...",
     "action_items": [{"text": "...", "owner": "...", "due": "..."}], "note": "what you changed and which context you used"}}`
     (markdown without a title line).
3. Speaker names, for the same meetings: when `get_meeting` shows speakers
   without a name, suggest who they are with
   `{"op": "suggest_speakers", "args": {"meeting_id": ID, "suggestions": {"Speaker 2": {"name": "Anna", "why": "..."}}}}`.
   Base it on the transcript (introductions, people addressed by name; the
   speakers' "lines" help), your memory and the wiki (who usually attends,
   who owns which topic), the names the person set in earlier meetings, and
   the person's calendar if you have it (who was invited at that time).
   "name" is only the name ("Anna", "Anna Meier"), never a sentence, a
   question or "probably ...": doubts and what you based it on go into
   "why", and if you are not fairly sure who it is, suggest nothing for that
   speaker. Never rename anyone: the person decides in the app, with your
   suggestion and a clip of the voice in front of them.
4. Google Drive, for each item under "outbox". There are such items only
   while the person has not connected Google Drive in the app; say once that
   connecting it there is the easier way, then:
   - drive_store: `{"op": "drive_files", "args": {"proposal_id": PID}}`. Find
     or create the folder "folder" in My Drive and in it the subfolder
     "subfolder". Download each file with curl into
     /workspace/meetings/<subfolder>/<name> and upload it with your Drive
     tools (drive_list, drive_create_folder, drive_upload, drive_move). If
     the subfolder already holds a file of that name, move the old one into
     a subfolder "Older versions" first. Then
     `{"op": "ack_outbox_item", "args": {"proposal_id": PID, "url": "<the subfolder's link>"}}`.
   - drive_log: find the Google Doc payload.doc in the folder payload.folder
     (create it if missing), add payload.markdown at the end and keep what is
     there, then ack it with the doc's link.
   - If one fails, leave it for the next run and say why.
   - No drive_* tools in this run: do not look for Drive another way. Leave
     the items, and say that the routine must not keep an app up to date
     (in its settings), because a routine tied to the shared Minutes Pro
     app gets none of the person's own accounts.
5. End with one short line per meeting and per item you handled.
