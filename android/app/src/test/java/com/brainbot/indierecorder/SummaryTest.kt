package com.brainbot.indierecorder

import org.junit.Assert.assertEquals
import org.junit.Test

class SummaryTest {
    @Test
    fun leadSectionsAndNumberedRows() {
        val md = """
            # Weekly team sync

            _18 Nov 2026, 47 min, 4 speakers_

            https://10.1.9.7:8443/meetings/abc

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
            SummaryPart.Lead("The team agreed to give the phone app to five testers."),
            SummaryPart.Heading("Decisions"),
            SummaryPart.Item(1, "Five testers get the app on Monday.", null),
            SummaryPart.Item(2, "Drive stays a choice per meeting.", null),
            SummaryPart.Heading("Action items"),
            SummaryPart.Item(1, "Prepare the tester list", "Speaker 2 · Fri"),
            SummaryPart.Item(2, "Write the install guide", "Speaker 1"),
        ), summaryParts(md))
    }
}
