package com.brainbot.indierecorder

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class AdtsTest {
    @Test
    fun headerCarriesFormatAndLength() {
        val expected = intArrayOf(0xFF, 0xF1, 0x60, 0x40, 0x0D, 0x7F, 0xFC).map { it.toByte() }.toByteArray()
        assertArrayEquals(expected, Adts.header(100))     // AAC LC, 16 kHz, mono, 107 bytes
    }

    @Test
    fun completeLengthKeepsWholeFramesOnly() {
        val f1 = Adts.header(10) + ByteArray(10)
        val f2 = Adts.header(20) + ByteArray(20)
        val cut = Adts.header(30) + ByteArray(5)
        assertEquals(f1.size + f2.size, Adts.completeLength(f1 + f2 + cut))
        assertEquals(f1.size, Adts.completeLength(f1))
        assertEquals(0, Adts.completeLength(byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8)))
        assertEquals(2, Adts.frameCount(f1 + f2 + cut))
        assertEquals(64, Adts.FRAME_MS)
    }

    @Test
    fun clockFormats() {
        assertEquals("0:05", clock(5_000))
        assertEquals("12:34", clock(754_000))
        assertEquals("1:02:03", clock(3_723_000))
        assertEquals("00:05", timer(5_000))
        assertEquals("12:34", timer(754_000))
        assertEquals("1:02:03", timer(3_723_000))
    }
}
