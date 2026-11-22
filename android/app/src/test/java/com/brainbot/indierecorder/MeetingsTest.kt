package com.brainbot.indierecorder

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale

/** The meeting views worked out as the web apps do them (recorder/static/meetings.js). */
class MeetingsTest {
    private val berlin = ZoneId.of("Europe/Berlin")

    private fun row(id: String, started: String, state: String = "done", storage: String = "local") =
        JSONObject().put("id", id).put("title", "Meeting $id").put("started_at", started).put("duration_s", 34.9)
            .put("state", state).put("storage", storage)

    @Test
    fun meetingsGoByThePhonesOwnDay() {
        // newest first, as list_meetings sends them; 23:30 UTC on the 18th is 00:30 on the 19th in Berlin
        val rows = MeetingRow.list(JSONArray(listOf(row("a", "2026-11-19T08:39:12Z"), row("b", "2026-11-19T06:00:00Z"),
            row("c", "2026-11-18T23:30:00Z"), row("d", "2026-11-18T22:30:00Z"))))
        val days = meetingDays(rows, berlin)
        assertEquals(listOf(LocalDate.of(2026, 11, 19), LocalDate.of(2026, 11, 18)), days.map { it.first })
        assertEquals(listOf(listOf("a", "b", "c"), listOf("d")), days.map { d -> d.second.map { it.id } })
        assertEquals(listOf(2, 2), meetingDays(rows, ZoneId.of("UTC")).map { it.second.size })
    }

    @Test
    fun dayLabelsTimesAndDates() {
        val today = LocalDate.of(2026, 11, 22)
        assertEquals("Today", dayLabel(today, today, Locale.ENGLISH, "Today", "Yesterday"))
        assertEquals("Gestern", dayLabel(today.minusDays(1), today, Locale.GERMAN, "Heute", "Gestern"))
        assertEquals("Thursday, November 19", dayLabel(LocalDate.of(2026, 11, 19), today, Locale.ENGLISH, "Today", "Yesterday"))
        assertEquals("Donnerstag, 19. November", dayLabel(LocalDate.of(2026, 11, 19), today, Locale.GERMAN, "Heute", "Gestern"))
        val t = Instant.parse("2026-11-19T07:39:12Z")
        assertEquals("08:39 AM", meetingTime(t, berlin, Locale.ENGLISH))
        assertEquals("08:39", meetingTime(t, berlin, Locale.GERMAN))
        assertEquals("Nov 19, 2026", meetingDate(t, berlin, Locale.ENGLISH))
        assertEquals("19. Nov. 2026", meetingDate(t, berlin, Locale.GERMAN))
        assertEquals(Instant.parse("2026-11-19T07:39:12Z"), instant("2026-11-19T08:39:12+01:00"))
        assertNull(instant("null"))
    }

    @Test
    fun lengthsAndStates() {
        assertEquals("00:35", meetingLength(34.9))
        assertEquals("10:11", meetingLength(611.0))
        assertEquals("1:46:43", meetingLength(6403.0))
        assertEquals("00:00", meetingLength(-3.0))
        assertEquals(RowState.DRIVE, rowState("done", "drive"))
        assertEquals(RowState.LOCAL, rowState("done", "local"))
        assertEquals(RowState.PROCESSING, rowState("transcribing", "drive"))
        assertEquals(RowState.FAILED, rowState("failed", "local"))
        assertEquals(true, rowState("summarizing", "local").busy)
    }

    @Test
    fun theTranscriptAsTurns() {
        val md = """
            # Transcript: Weekly team sync

            2026-11-19 09:39 · 10 min · 2 speakers · en · Nemotron 3.5

            > Speaker labels are guesses.

            **Speaker 1** · 00:00:05

            Good morning.

            Shall we start?

            **Luis** · 00:00:12

            Yes.
        """.trimIndent()
        assertEquals(listOf(
            TranscriptPart.Meta("2026-11-19 09:39 · 10 min · 2 speakers · en · Nemotron 3.5"),
            TranscriptPart.Meta("Speaker labels are guesses."),
            TranscriptPart.Turn("Speaker 1", "00:00:05", listOf("Good morning.", "Shall we start?")),
            TranscriptPart.Turn("Luis", "00:00:12", listOf("Yes.")),
        ), transcriptParts(md))
    }

    @Test
    fun speakerCardsAndWhereTheirClipsAre() {
        val m = JSONObject().put("speakers", JSONArray().put(JSONObject().put("label", "Speaker 2").put("name", JSONObject.NULL)
            .put("share", 35).put("turns", 12).put("first", 5.2).put("lines", JSONArray().put("We ship on Monday."))
            .put("suggestions", JSONArray().put(JSONObject().put("name", "Anna").put("why", "addressed as Anna").put("by", "agent")))
            .put("clip_url", "https://ai-knowledge.mz.brainbot.com/indie-recorder/api/v1/files/20261119-0839-b89d/1795340902/abcdefghijklmnopqrstuv/clip%3ASpeaker%202")))
        val c = speakerCards(m).single()
        assertEquals("Speaker 2", c.label)
        assertEquals("", c.name)
        assertEquals(listOf("Anna"), c.suggestions.map { it.name })
        assertEquals("/api/v1/files/20261119-0839-b89d/1795340902/abcdefghijklmnopqrstuv/clip%3ASpeaker%202", clipPath(c.clipUrl))
        assertNull(clipPath(null))
        // an older box sends only the names
        val old = speakerCards(JSONObject().put("speakers", 2).put("speaker_names", JSONObject().put("Speaker 1", "Luis")))
        assertEquals(listOf("Speaker 1" to "Luis"), old.map { it.label to it.name })
    }
}
