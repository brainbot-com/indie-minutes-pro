package com.brainbot.indierecorder

/** A piece of the box's summary as the meeting screen draws it. */
sealed interface SummaryPart {
    data class Lead(val text: String) : SummaryPart
    data class Heading(val text: String) : SummaryPart
    data class Item(val n: Int, val text: String, val side: String?) : SummaryPart
    data class Para(val text: String) : SummaryPart
}

/**
 * The box's summary.md in parts: the title, byline and link lines are left
 * out (the screen shows them), the first section (the TL;DR) is the lead, and
 * lists and tables of action items become numbered rows, "owner · due" at
 * the side.
 */
fun summaryParts(md: String): List<SummaryPart> {
    val lines = md.lines()
    var i = 0
    fun skipBlank() { while (i < lines.size && lines[i].isBlank()) i++ }
    skipBlank(); if (i < lines.size && lines[i].startsWith("# ")) i++
    skipBlank(); if (i < lines.size && lines[i].startsWith("_") && lines[i].trimEnd().endsWith("_")) i++
    skipBlank(); if (i < lines.size && lines[i].startsWith("http")) i++
    val out = mutableListOf<SummaryPart>()
    var headings = 0
    var lead = false
    var n = 0
    while (i < lines.size) {
        val line = lines[i].trimEnd()
        val next = lines.getOrNull(i + 1)?.trim() ?: ""
        when {
            line.startsWith("## ") -> {
                headings++
                n = 0
                if (headings == 1) lead = true else out += SummaryPart.Heading(line.removePrefix("## ").trim())
            }
            line.startsWith("|") && next.matches(Regex("^\\|[-| :]+\\|$")) -> i++      // header row and its rule
            line.startsWith("|") -> {
                val cells = line.trim('|').split(" | ").map { it.trim().replace("\\|", "|") }
                val side = listOfNotNull(cells.getOrNull(2), cells.getOrNull(3)).filter { !empty(it) }.joinToString(" · ")
                out += SummaryPart.Item(++n, plain(cells.getOrNull(1) ?: line), side.ifBlank { null })
            }
            line.startsWith("- ") || line.startsWith("* ") -> out += SummaryPart.Item(++n, plain(line.substring(2)), null)
            line.isBlank() -> {}
            else -> {
                out += if (lead) SummaryPart.Lead(plain(line)) else SummaryPart.Para(plain(line))
                lead = false
            }
        }
        i++
    }
    return out
}

private fun plain(text: String) = text.replace("**", "").replace("`", "")

/** A table cell that says "nobody" or "no date": blank, or only dashes. */
private fun empty(cell: String) = cell.trim().trim('-', '\u2013', '\u2014').isEmpty()
