package com.brainbot.indierecorder

/**
 * ADTS framing for the encoder's raw AAC frames. Every frame carries its own
 * header, so a recording can be cut into chunk files between any two frames
 * and the box gets a valid stream back by joining them (audio/aac).
 */
object Adts {
    const val SAMPLE_RATE = 16000
    private const val FREQ_INDEX = 8      // 16 kHz
    private const val PROFILE_LC = 2      // AAC LC
    private const val CHANNELS = 1

    fun header(frameLength: Int): ByteArray {
        val len = frameLength + 7
        return byteArrayOf(
            0xFF.toByte(),
            0xF1.toByte(),                // MPEG-4, no CRC
            (((PROFILE_LC - 1) shl 6) or (FREQ_INDEX shl 2) or (CHANNELS shr 2)).toByte(),
            (((CHANNELS and 3) shl 6) or (len shr 11)).toByte(),
            ((len shr 3) and 0xFF).toByte(),
            (((len and 7) shl 5) or 0x1F).toByte(),
            0xFC.toByte(),
        )
    }

    /** Bytes up to the end of the last whole frame: a file cut off mid-write keeps its complete frames. */
    fun completeLength(data: ByteArray): Int = scan(data).first

    /** Whole frames in the data; each holds 1024 samples, 64 ms at 16 kHz. */
    fun frameCount(data: ByteArray): Int = scan(data).second

    const val FRAME_MS = 1024 * 1000 / SAMPLE_RATE

    private fun scan(data: ByteArray): Pair<Int, Int> {
        var pos = 0
        var frames = 0
        while (pos + 7 <= data.size) {
            if (data[pos] != 0xFF.toByte() || (data[pos + 1].toInt() and 0xF6) != 0xF0) break
            val len = ((data[pos + 3].toInt() and 3) shl 11) or
                ((data[pos + 4].toInt() and 0xFF) shl 3) or
                ((data[pos + 5].toInt() and 0xE0) shr 5)
            if (len < 7 || pos + len > data.size) break
            pos += len
            frames++
        }
        return pos to frames
    }
}
