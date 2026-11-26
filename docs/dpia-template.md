# Template: data protection impact assessment for Minutes Pro

This template helps companies that use Minutes Pro with the data protection
impact assessment (DPIA) under Art. 35 GDPR. The technical parts describe how
Minutes Pro works; the fields in square brackets are for the controller to
fill in. It is not legal advice: check the result with your data protection
officer and, where there is one, the works council. German version:
[dsfa-vorlage.md](dsfa-vorlage.md), with the German rules spelled out.

## 1. Scope

| | |
|---|---|
| Controller | [Company, address] |
| Data protection officer | [Name, contact] |
| Processing | Recording, transcribing and summarizing meetings with Minutes Pro |
| Version, operation | Minutes Pro [version] on [Indie Box / own server], in the network [location] |
| Prepared by, on | [Name], [date] |

## 2. Description of the processing

**Purpose.** [Example: minutes of project meetings, so that decisions and
action items are on record.]

**How it works.**
1. A person designated for it (the owner) starts a recording on a phone in a
   meeting. Minutes Pro records only when someone taps Record; it never
   listens on its own.
2. The phone encrypts each piece of the recording (AES-256-GCM, with a key
   agreed by ECDH P-256 with the box's key) and sends it over HTTPS to the box
   in the company's own network.
3. On the box, a speech model (NVIDIA Nemotron 3.5 Transcribe) writes down the
   text and tells the speakers apart ("Speaker 1, 2 ..."); a language model on
   the box writes a summary with decisions and action items.
4. The owner reads the result and can give the speakers names. Members of
   their team read the meetings the owner shares with the team.

**Data subjects.** Everyone who speaks in a recorded meeting (employees,
customers, partners, guests); the people in the team.

**Data.**

| Category | Examples | Where |
|---|---|---|
| Audio | the recording of the meeting | box, encrypted |
| Content | transcript, summary, action items | box, encrypted |
| Attribution | speaker labels, names given, short voice samples (made on demand, not stored) | box, encrypted |
| Accounts | names and password hashes of team members, last sign-in; the owner's phones | box, encrypted |
| Special categories (Art. 9) | may occur, for example when health is discussed in a meeting | [define how they are handled] |

Minutes Pro keeps no voice profiles: speakers are told apart within each
meeting, never recognized across meetings.

**Recipients.** The owner; team members for shared meetings. Only if the
owner turns it on: Google Drive for single meetings, and an AI agent of their
own that improves summaries. [Both excluded / handled as follows: ...] The
maker (brainbot) has no access.

**Retention.** Until the owner deletes a meeting. [Set a period, for example:
recordings deleted after 90 days, summaries at the end of the project.]
Backups of the box keep their copy until they expire [backup retention].

## 3. Necessity and proportionality

**Legal basis.** [Check and decide, for example consent under Art. 6(1)(a)
GDPR, the national rules for employees, or legitimate interest under
Art. 6(1)(f).] Regardless of the legal basis: in Germany, recording the
non-public spoken word without consent is a crime (§ 201 StGB), and similar
rules apply elsewhere. Everyone taking part must know about the recording and
agree before it starts.

**Co-determination.** Software that records conversations can be suitable for
monitoring behaviour or performance; in Germany the works council has a say
(§ 87(1) no. 6 BetrVG). [Works agreement: yes / no / not needed, because ...]

**Data minimisation.**
- Recording only on a tap, no listening in the background.
- Processing on the company's own box; no cloud service for speech
  recognition or summaries.
- Deleting a meeting removes the recording, the transcript and the summaries.
- Team members read only what is shared; recording, naming and deleting stay
  with the owner.

**Transparency.** The app reminds at every recording ("Tell everyone first")
and asks once on every phone. The box's `/privacy` page describes the
processing, with a contact for privacy questions. [Own information for
employees and guests: ...]

**Data subjects' rights.** Access, copy, rectification and erasure (Art. 15 to
17) are handled by the owner in the app; deleting removes the meeting from the
box. [Process for requests: ...]

## 4. Risks

| Risk | Consequence for data subjects | Likelihood | Severity |
|---|---|---|---|
| Recording without knowledge or consent | breach of the confidentiality of the spoken word, criminal liability | [ ] | high |
| Unauthorised access to recordings | confidential content becomes known | [ ] | high |
| Use to monitor performance or behaviour | pressure, disadvantage | [ ] | [ ] |
| Wrong transcription or speaker attribution | statements attributed to the wrong person | [ ] | medium |
| Disclosure through Drive or a cloud agent | content leaves the company | [ ] | [ ] |
| Keeping data too long | more data than needed, for longer than needed | [ ] | medium |

## 5. Measures

**Technical (how Minutes Pro works):**
- Encryption on the phone before storing and sending; HTTPS in transit; on
  the box every file with AES-256-GCM, the index with SQLCipher; the key sits
  in the app's settings on the box.
- Audio in memory only (tmpfs) while it is processed.
- Sign-in with a password; members' passwords as scrypt hashes; limits on
  wrong attempts; sessions end after 30 days.
- Roles: the owner records and manages; members read only what the owner
  shares; invitations work once and for seven days.
- No voice profiles, no recognition of people across meetings.
- Language models run on the box; a model the box would only pass on to an
  external provider gets no transcripts.

**Organisational (for the controller to fill in):**
- [Who may record; notice to everyone taking part before each recording]
- [Retention periods and who carries them out]
- [Whether Google Drive or an AI agent may be used]
- [Access to the box and its backups]
- [Handling of meetings in which special categories come up]

## 6. Result

| | |
|---|---|
| Residual risk after the measures | [low / medium / high, reasons] |
| Opinion of the data protection officer | [ ] |
| Prior consultation of the supervisory authority needed (Art. 36) | [yes / no] |
| Approval | [Name, role, date] |
| Next review | [date, or on significant changes] |
