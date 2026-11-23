package com.brainbot.indierecorder

/** A piece of the box's summary as the meeting screen draws it. */
sealed interface SummaryPart {
    /** The short version: the TL;DR section, or a first paragraph before any heading. */
    data class Lead(val text: String) : SummaryPart
    /** A section's heading; key is one every summary has (decisions, actions, questions), said in the app's language. */
    data class Heading(val text: String, val key: String? = null) : SummaryPart
    data class Item(val n: Int, val text: String, val side: String?) : SummaryPart
    data class Para(val text: String) : SummaryPart
}

private val SECTIONS = mapOf("tl;dr" to "tldr", "kurzfassung" to "tldr", "decisions" to "decisions",
    "entscheidungen" to "decisions", "action items" to "actions", "aufgaben" to "actions",
    "open questions" to "questions", "offene fragen" to "questions")
private val SKIP = Regex("^# |^_.*_$|^https?://\\S+$")
private val HEADING = Regex("^#{2,4}\\s+(.*)")
private val TABLE_RULE = Regex("^\\|[\\s:|-]+\\|$")
private val TABLE_HEAD = Regex("^\\|\\s*#\\s*\\|")
private val TABLE_ROW = Regex("^\\|(.*)\\|$")
private val BULLET = Regex("^[-*]\\s+(.*)")

/**
 * The box's summary.md in parts, as the web apps draw it (recorder/static/meetings.js, summary()):
 * the title, byline and link lines left out (the screen shows them), the TL;DR or a first paragraph
 * as the lead, then a section per heading with numbered rows; tables of action items become rows
 * with "owner · due" at the side (a cell with only a dash says nobody or no date: left out).
 * **Bold** stays marked for the screen.
 */
fun summaryParts(md: String): List<SummaryPart> {
    val out = mutableListOf<SummaryPart>()
    var n = 0
    var tldr = false
    var first = true
    for (raw in md.split('\n')) {
        val line = raw.trim()
        val heading = HEADING.find(line)
        val row = TABLE_ROW.find(line)
        val bullet = BULLET.find(line)
        when {
            SKIP.containsMatchIn(line) -> {}
            heading != null -> {
                val text = heading.groupValues[1]
                val key = SECTIONS[text.lowercase()]
                tldr = key == "tldr"
                n = 0
                if (!tldr) out += SummaryPart.Heading(text, key)
            }
            TABLE_RULE.matches(line) || TABLE_HEAD.containsMatchIn(line) -> {}
            row != null -> {
                val c = row.groupValues[1].split('|').map { it.trim() }
                val side = listOfNotNull(c.getOrNull(2), c.getOrNull(3)).filter { !empty(it) }.joinToString(" · ")
                out += SummaryPart.Item(++n, c.getOrNull(1)?.ifEmpty { null } ?: c[0], side.ifEmpty { null })
            }
            bullet != null -> out += SummaryPart.Item(++n, bullet.groupValues[1], null)
            line.isNotEmpty() -> {
                out += if (tldr || first) SummaryPart.Lead(line) else SummaryPart.Para(line)
                first = false
            }
        }
    }
    return out
}

/** A table cell that says "nobody" or "no date": blank, or only dashes. */
private fun empty(cell: String) = cell.trim().trim('-', '\u2013', '\u2014').isEmpty()
