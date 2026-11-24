package com.brainbot.indierecorder

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToLong

/*
 * The meeting views every Minutes Pro app shares (recorder/static/meetings.js in the web apps):
 * a person's meetings by day, and one meeting with its summary, speakers and transcript. What the
 * screens draw is worked out here, without Android, so the tests can hold it to meetings.js.
 */

/** One meeting of the list, as the box's list_meetings sends it. */
class MeetingRow(val id: String, val title: String, val startedAt: Instant?, val durationS: Double,
                 val state: String, val storage: String) {
    companion object {
        fun fromJson(o: JSONObject) = MeetingRow(
            o.getString("id"), text(o, "title") ?: o.getString("id"),
            instant(text(o, "started_at")) ?: instant(text(o, "updated_at")),
            o.optDouble("duration_s", 0.0).takeUnless { it.isNaN() } ?: 0.0,
            o.optString("state"), text(o, "storage") ?: "local")

        fun list(a: JSONArray) = (0 until a.length()).map { fromJson(a.getJSONObject(it)) }
    }
}

/** "2026-11-19T08:39:12Z" (or with an offset) as an instant; null when it is none. */
fun instant(iso: String?): Instant? = iso?.let {
    runCatching { Instant.parse(it) }.recoverCatching { _ -> OffsetDateTime.parse(it).toInstant() }.getOrNull()
}

/** The rows by the phone's own day, in the order the box sends them (newest first): a group per day. */
fun meetingDays(rows: List<MeetingRow>, zone: ZoneId): List<Pair<LocalDate?, List<MeetingRow>>> {
    val out = mutableListOf<Pair<LocalDate?, MutableList<MeetingRow>>>()
    for (r in rows) {
        val day = r.startedAt?.atZone(zone)?.toLocalDate()
        if (out.isEmpty() || out.last().first != day) out += day to mutableListOf()
        out.last().second += r
    }
    return out
}

/** "Today", "Yesterday", else "Thursday, November 19" / "Donnerstag, 19. November". */
fun dayLabel(day: LocalDate?, today: LocalDate, locale: Locale, todayWord: String, yesterdayWord: String): String = when (day) {
    null -> ""
    today -> todayWord
    today.minusDays(1) -> yesterdayWord
    else -> DateTimeFormatter.ofPattern(if (locale.language == "de") "EEEE, d. MMMM" else "EEEE, MMMM d", locale).format(day)
}

/** "08:39 AM" / "08:39", as the web apps write a meeting's time. */
fun meetingTime(t: Instant, zone: ZoneId, locale: Locale): String =
    DateTimeFormatter.ofPattern(if (locale.language == "de") "HH:mm" else "hh:mm a", locale).format(t.atZone(zone))

/** "Nov 19, 2026" / "19. Nov. 2026", over a meeting's title. */
fun meetingDate(t: Instant, zone: ZoneId, locale: Locale): String =
    DateTimeFormatter.ofPattern(if (locale.language == "de") "d. MMM yyyy" else "MMM d, yyyy", locale).format(t.atZone(zone))

/** A meeting's length: 00:35, 10:11, 1:46:43 (whole seconds, rounded). */
fun meetingLength(seconds: Double): String {
    val s = maxOf(0L, seconds.roundToLong())
    val h = s / 3600
    return if (h > 0) "%d:%02d:%02d".format(h, s / 60 % 60, s % 60) else "%02d:%02d".format(s / 60, s % 60)
}

/** What a row says about a meeting: Uploading, Failed and Processing in red, else where it is kept. */
enum class RowState(val busy: Boolean) { UPLOADING(true), FAILED(true), PROCESSING(true), DRIVE(false), LOCAL(false) }

fun rowState(state: String, storage: String): RowState = when (state) {
    "uploading" -> RowState.UPLOADING
    "failed" -> RowState.FAILED
    "done" -> if (storage == "drive") RowState.DRIVE else RowState.LOCAL
    else -> RowState.PROCESSING
}

/** A piece of a transcript (render.py's Markdown) as the meeting screen draws it. */
sealed interface TranscriptPart {
    /** The line under the title (date, length, speakers, engine), and notes. */
    data class Meta(val text: String) : TranscriptPart
    /** Who spoke and when, then what they said. */
    data class Turn(val who: String, val at: String, val said: List<String>) : TranscriptPart
    /** Words before the first turn. */
    data class Said(val text: String) : TranscriptPart
}

private val TURN = Regex("^\\*\\*(.+?)\\*\\*\\s*·\\s*([\\d:]+)$")

/** The transcript as turns, like meetings.js transcript(): the title line goes, the meta line stays, quiet. */
fun transcriptParts(md: String): List<TranscriptPart> {
    val out = mutableListOf<TranscriptPart>()
    var who: String? = null
    var at = ""
    var said = mutableListOf<String>()
    var meta = true
    fun close() {
        who?.let { out += TranscriptPart.Turn(it, at, said) }
        who = null
        said = mutableListOf()
    }
    for (raw in md.split('\n')) {
        val line = raw.trim()
        if (line.isEmpty() || line.startsWith("# ")) continue
        val m = TURN.find(line)
        when {
            m != null -> {
                close()
                who = m.groupValues[1]
                at = m.groupValues[2]
                meta = false
            }
            meta || line.startsWith("> ") -> {
                close()
                out += TranscriptPart.Meta(line.removePrefix("> "))
                meta = false
            }
            who != null -> said += line
            else -> out += TranscriptPart.Said(line)
        }
    }
    close()
    return out
}

/** A name someone suggested for a speaker, and why (by: recorder, agent or person). */
class Suggestion(val name: String, val why: String, val by: String)

/** A speaker of a meeting with what helps to name them, as get_meeting sends it. */
class SpeakerCard(val label: String, val name: String, val share: Int, val turns: Int, val first: Double,
                  val lines: List<String>, val suggestions: List<Suggestion>, val clipUrl: String?)

/** The speaker cards of a meeting, the most talkative first; an older box sent only the names. */
fun speakerCards(m: JSONObject): List<SpeakerCard> {
    val a = m.optJSONArray("speakers")
    if (a == null) {
        val names = m.optJSONObject("speaker_names") ?: return emptyList()
        return names.keys().asSequence().map { SpeakerCard(it, names.optString(it), 0, 0, 0.0, emptyList(), emptyList(), null) }.toList()
    }
    return (0 until a.length()).map { i ->
        val c = a.getJSONObject(i)
        val lines = c.optJSONArray("lines")
        val sugg = c.optJSONArray("suggestions")
        SpeakerCard(c.getString("label"), text(c, "name") ?: "", c.optInt("share"), c.optInt("turns"), c.optDouble("first", 0.0),
            (0 until (lines?.length() ?: 0)).map { lines!!.getString(it) },
            (0 until (sugg?.length() ?: 0)).map { j ->
                val s = sugg!!.getJSONObject(j)
                Suggestion(s.getString("name"), text(s, "why") ?: "", s.optString("by"))
            },
            text(c, "clip_url"))
    }
}

/**
 * Where the phone fetches a speaker's clip: the signed link's path at the box itself. The link names
 * the brain-tls address, whose certificate the phone may not trust; the box answers the same path.
 */
fun clipPath(url: String?): String? = url?.substringAfter("/api/v1/files/", "")?.takeIf { it.isNotEmpty() }?.let { "/api/v1/files/$it" }
