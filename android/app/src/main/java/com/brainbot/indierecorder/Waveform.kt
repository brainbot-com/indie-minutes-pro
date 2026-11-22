package com.brainbot.indierecorder

import kotlin.math.abs
import kotlin.math.log10

/**
 * The live waveform of the recording screen, drawn the same way as in the
 * PWA: one bar per 100 ms of recorded audio, its height from the loudest
 * sample of those 100 ms on a 50 dB scale. A pause adds no bars; when the
 * recording resumes, a gap of three bars shows where it was.
 */
class Waveform(private val capacity: Int = 200, private val samplesPerBar: Int = Adts.SAMPLE_RATE / 10) {
    private val bars = ArrayDeque<Float>()
    private var peak = 0
    private var count = 0

    /** Adds recorded samples; true when at least one bar was completed. */
    fun feed(pcm: ShortArray, n: Int): Boolean {
        var added = false
        for (i in 0 until n) {
            val a = abs(pcm[i].toInt())
            if (a > peak) peak = a
            if (++count == samplesPerBar) {
                push(value(peak / 32768f))
                peak = 0
                count = 0
                added = true
            }
        }
        return added
    }

    /** The bar in progress is dropped: it would mix audio from before and after the pause. */
    fun pause() {
        peak = 0
        count = 0
    }

    fun resume() = repeat(GAP_STEPS) { push(GAP) }

    /** Oldest first; GAP where a pause was. */
    fun bars(): List<Float> = bars.toList()

    private fun push(v: Float) {
        bars.addLast(v)
        while (bars.size > capacity) bars.removeFirst()
    }

    companion object {
        const val GAP = -1f
        const val GAP_STEPS = 3
        private const val FLOOR_DB = -50.0

        /** 0..1 for a bar from a peak at full scale 1.0: -50 dBFS and below is the floor. */
        fun value(peak: Float): Float {
            val db = 20 * log10(maxOf(peak.toDouble(), 1e-5))
            return ((db - FLOOR_DB) / -FLOOR_DB).coerceIn(0.0, 1.0).toFloat()
        }
    }
}
