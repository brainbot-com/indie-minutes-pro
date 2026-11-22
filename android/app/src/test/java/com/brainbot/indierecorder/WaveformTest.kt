package com.brainbot.indierecorder

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WaveformTest {
    private fun samples(n: Int, amplitude: Int) = ShortArray(n) { if (it % 2 == 0) amplitude.toShort() else (-amplitude).toShort() }

    @Test
    fun barValueIsFiftyDecibelsOfPeak() {
        assertEquals(1f, Waveform.value(1f), 1e-6f)
        assertEquals(0f, Waveform.value(0.00316f), 1e-3f)        // -50 dBFS
        assertEquals(0.4f, Waveform.value(0.0316f), 1e-3f)       // -30 dBFS
        assertEquals(0.88f, Waveform.value(0.5f), 1e-2f)         // -6 dBFS
        assertEquals(0f, Waveform.value(0f), 0f)                 // silence is the floor, not NaN
        assertEquals(1f, Waveform.value(2f), 0f)                 // clipped
    }

    @Test
    fun oneBarPer100msOfAudio() {
        val w = Waveform()
        assertFalse(w.feed(samples(1599, 16384), 1599))          // 16 kHz: 1600 samples make a bar
        assertTrue(w.feed(samples(1, 0), 1))
        assertEquals(1, w.bars().size)
        assertEquals(Waveform.value(0.5f), w.bars()[0], 1e-6f)
        w.feed(samples(3200, 32767), 3200)
        assertEquals(3, w.bars().size)
        assertEquals(1f, w.bars()[2], 1e-3f)
    }

    @Test
    fun pauseDropsTheBarInProgressAndLeavesAGap() {
        val w = Waveform()
        w.feed(samples(1600, 1000), 1600)
        w.feed(samples(800, 30000), 800)                         // half a bar before the pause
        w.pause()
        w.resume()
        w.feed(samples(1600, 1000), 1600)
        val bars = w.bars()
        assertEquals(listOf(Waveform.value(1000 / 32768f), Waveform.GAP, Waveform.GAP, Waveform.GAP,
            Waveform.value(1000 / 32768f)), bars)
    }

    @Test
    fun keepsTheNewestBars() {
        val w = Waveform(capacity = 5)
        for (i in 1..8) w.feed(samples(1600, i * 1000), 1600)
        assertEquals(5, w.bars().size)
        assertEquals(Waveform.value(8000 / 32768f), w.bars().last(), 1e-6f)
        assertEquals(Waveform.value(4000 / 32768f), w.bars().first(), 1e-6f)
    }
}
