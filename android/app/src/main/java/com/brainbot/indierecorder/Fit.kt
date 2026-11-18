package com.brainbot.indierecorder

/**
 * The heights of the main screen's parts that grow (the block, the timer, the waveform), from the
 * room the other parts and the gaps leave them. Each gets its least; what is left goes to the first
 * ones (the block) until they are full, then to the others, the parts of a turn each the same share
 * of what they can still grow; the rest stays free. Short of even the least, each gets the same
 * share of its least. A part that does not grow has 0 for both and gets 0.
 */
fun growHeights(room: Int, least: List<Int>, full: List<Int>, first: List<Boolean>): List<Int> {
    val all = least.sum()
    if (room < all) return least.map { if (all == 0) 0 else (it.toLong() * room.coerceAtLeast(0) / all).toInt() }
    val out = least.toMutableList()
    var left = room - all
    for (turn in listOf(true, false)) {
        val parts = out.indices.filter { first[it] == turn }
        val more = parts.sumOf { (full[it] - least[it]).coerceAtLeast(0) }
        if (more == 0) continue
        val give = minOf(left, more)
        for (i in parts) {
            val add = ((full[i] - least[i]).coerceAtLeast(0).toLong() * give / more).toInt()
            out[i] += add
            left -= add
        }
    }
    return out
}
