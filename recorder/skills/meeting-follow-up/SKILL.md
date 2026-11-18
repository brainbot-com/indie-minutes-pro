---
name: meeting-follow-up
description: Look after the person's meetings from the indie recorder on aibox1. Write better summaries with what you know, and put the meetings they keep in Google Drive there. Use it when the person asks to set up the meeting follow-up, and in each run of the "Meeting follow-up" routine.
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
2. Google Drive. If you have no Drive tools that create files, ask for them
   with connection_request (Google Drive, with changes) and go on once it is
   connected.
3. The routine. Create it with routine_create: title "Meeting follow-up",
   schedule every hour, quiet "[Nothing new]", and as its prompt the whole
   text of "Each run" below, so each run stands on its own.
4. Say in one or two lines what is set up and that the first run comes
   within the hour.

## Each run

1. `{"op": "pending_work"}`. If it returns nothing, answer "[Nothing new]"
   and stop.
2. Better summaries, for each meeting under "summaries":
   - `{"op": "get_meeting", "args": {"meeting_id": ID, "include_transcript": false}}`
     gives the current summary, the speaker names and the action items.
   - `{"op": "meeting_files", "args": {"meeting_id": ID}}` gives signed links
     (two hours). Download the transcript with
     `mkdir -p /workspace/meetings/ID && curl -fsS -o /workspace/meetings/ID/transcript.md "<url>"`
     and read the file. Never copy transcript text into the chat.
   - Write a better summary in the meeting's language with what you know
     about the people, projects and earlier meetings (your memory, the wiki,
     `list_meetings` and `get_meeting` for earlier ones): a short TL;DR,
     decisions, action items (owner and due date only where they were said),
     open questions. Keep the labels "Speaker 1", "Speaker 2" unless names
     are set; if you are fairly sure who a speaker is, say so in the note, do
     not rename. Never invent decisions, owners or dates.
   - Store it: `{"op": "update_summary", "args": {"meeting_id": ID, "markdown": "...",
     "action_items": [{"text": "...", "owner": "...", "due": "..."}], "note": "what you changed and which context you used"}}`
     (markdown without a title line).
3. Google Drive, for each item under "outbox":
   - drive_store: `{"op": "drive_files", "args": {"proposal_id": PID}}`. Find
     or create the folder "folder" in My Drive and in it the subfolder
     "subfolder". Download each file with curl into
     /workspace/meetings/<subfolder>/<name> and upload it with your Drive
     tools. If the subfolder already holds a file of that name, move the old
     one into a subfolder "Older versions" first. Then
     `{"op": "ack_outbox_item", "args": {"proposal_id": PID, "url": "<the subfolder's link>"}}`.
   - drive_log: find the Google Doc payload.doc in the folder payload.folder
     (create it if missing), add payload.markdown at the end and keep what is
     there, then ack it with the doc's link.
   - If one fails, leave it for the next run and say why.
4. End with one short line per meeting and per item you handled.
