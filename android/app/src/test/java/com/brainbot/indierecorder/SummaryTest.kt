package com.brainbot.indierecorder

import org.junit.Assert.assertEquals
import org.junit.Test

/** The same cases as the web apps' summary() (recorder/static/meetings.js). */
class SummaryTest {
    @Test
    fun leadSectionsAndNumberedRows() {
        val md = """
            # Weekly team sync

            _18 Nov 2026, 47 min, 4 speakers_

            https://192.168.1.20:8443/meetings/abc

            ## TL;DR

            The team agreed to give the **phone app** to five testers.

            ## Decisions

            - Five testers get the app on Monday.
            - Drive stays a choice per meeting.

            ## Action items

            | # | Task | Owner | Due |
            |---|------|-------|-----|
            | 1 | Prepare the tester list | Speaker 2 | Fri |
            | 2 | Write the install guide | Speaker 1 | - |
        """.trimIndent()
        assertEquals(listOf(
            SummaryPart.Lead("The team agreed to give the **phone app** to five testers."),
            SummaryPart.Heading("Decisions", "decisions"),
            SummaryPart.Item(1, "Five testers get the app on Monday.", null),
            SummaryPart.Item(2, "Drive stays a choice per meeting.", null),
            SummaryPart.Heading("Action items", "actions"),
            SummaryPart.Item(1, "Prepare the tester list", "Speaker 2 · Fri"),
            SummaryPart.Item(2, "Write the install guide", "Speaker 1"),
        ), summaryParts(md))
    }

    @Test
    fun germanHeadingsAreKnownSectionsAndOthersStayAsWritten() {
        val md = """
            ## Kurzfassung

            Erster Satz.
            Zweiter Satz.

            ## Offene Fragen

            * Wer testet?

            ### Nächste Schritte

            Ein Absatz.
        """.trimIndent()
        assertEquals(listOf(
            SummaryPart.Lead("Erster Satz."),
            SummaryPart.Lead("Zweiter Satz."),
            SummaryPart.Heading("Offene Fragen", "questions"),
            SummaryPart.Item(1, "Wer testet?", null),
            SummaryPart.Heading("Nächste Schritte", null),
            SummaryPart.Para("Ein Absatz."),
        ), summaryParts(md))
    }

    @Test
    fun aFirstParagraphWithoutHeadingsIsTheLeadAndNumbersGoOnAfterAParagraph() {
        val md = "An agent's summary without a TL;DR heading.\n\nMore context.\n\n## Decisions\n\n- One\n\nA note.\n\n- Two"
        assertEquals(listOf(
            SummaryPart.Lead("An agent's summary without a TL;DR heading."),
            SummaryPart.Para("More context."),
            SummaryPart.Heading("Decisions", "decisions"),
            SummaryPart.Item(1, "One", null),
            SummaryPart.Para("A note."),
            SummaryPart.Item(2, "Two", null),
        ), summaryParts(md))
    }
}
