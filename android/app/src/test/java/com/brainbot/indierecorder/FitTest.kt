package com.brainbot.indierecorder

import org.junit.Assert.assertEquals
import org.junit.Test

class FitTest {
    // the Recording screen's parts: bar, timer, waveform, cells, block (first), button
    private val least = listOf(0, 61, 56, 0, 100, 0)
    private val full = listOf(0, 96, 88, 0, 163, 0)
    private val first = listOf(false, false, false, false, true, false)

    @Test
    fun fullSizesWhereThereIsRoom() {
        assertEquals(listOf(0, 96, 88, 0, 163, 0), growHeights(500, least, full, first))
    }

    @Test
    fun theBlockGrowsFirst() {
        assertEquals(listOf(0, 61, 56, 0, 140, 0), growHeights(61 + 56 + 140, least, full, first))
        assertEquals(listOf(0, 61, 56, 0, 163, 0), growHeights(61 + 56 + 163, least, full, first))
    }

    @Test
    fun thenTheOthersByTheSameShare() {
        // 35 + 32 can grow; 33 is about half of that
        val h = growHeights(61 + 56 + 163 + 33, least, full, first)
        assertEquals(163, h[4])
        assertEquals(61 + 17, h[1])
        assertEquals(56 + 15, h[2])
    }

    @Test
    fun shortOfTheLeastEachGivesTheSameShare() {
        assertEquals(listOf(0, 30, 27, 0, 49, 0), growHeights(108, least, full, first))     // half of 217
        assertEquals(listOf(0, 0, 0, 0, 0, 0), growHeights(-20, least, full, first))
        assertEquals(listOf(0, 0), growHeights(-20, listOf(0, 0), listOf(0, 0), listOf(false, false)))
    }

    @Test
    fun aPartAtItsFullSizeAlreadyTakesNothing() {
        assertEquals(listOf(96, 186), growHeights(400, listOf(96, 112), listOf(96, 186), listOf(false, true)))
        assertEquals(listOf(96, 150), growHeights(96 + 150, listOf(96, 112), listOf(96, 186), listOf(false, true)))
    }
}
