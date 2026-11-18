package com.brainbot.indierecorder

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

/** One recording on the phone, from the first chunk until the box has it all. */
class LocalRecording(
    val clientId: String,
    var title: String,
    val startedAt: String,
    var storage: String,
    var state: String = RECORDING,
    var chunks: Int = 0,
    var durationMs: Long = 0,
    var recordingId: String? = null,
    var meetingId: String? = null,
    var uploaded: Int = 0,
    var error: String? = null,
    var interrupted: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("clientId", clientId).put("title", title).put("startedAt", startedAt).put("storage", storage)
        .put("state", state).put("chunks", chunks).put("durationMs", durationMs)
        .put("recordingId", recordingId ?: JSONObject.NULL).put("meetingId", meetingId ?: JSONObject.NULL)
        .put("uploaded", uploaded).put("error", error ?: JSONObject.NULL).put("interrupted", interrupted)

    companion object {
        const val RECORDING = "recording"     // the service writes chunks
        const val STOPPED = "stopped"         // complete, waiting for the box to have it all
        const val FINALIZED = "finalized"     // the box has it; the audio is gone from the phone

        fun fromJson(o: JSONObject) = LocalRecording(
            o.getString("clientId"), o.optString("title"), o.getString("startedAt"), o.optString("storage", "local"),
            o.optString("state", RECORDING), o.optInt("chunks"), o.optLong("durationMs"),
            o.optString("recordingId").takeIf { it.isNotBlank() && it != "null" },
            o.optString("meetingId").takeIf { it.isNotBlank() && it != "null" },
            o.optInt("uploaded"), o.optString("error").takeIf { it.isNotBlank() && it != "null" },
            o.optBoolean("interrupted"),
        )
    }
}

/**
 * Recordings in the app's private storage: files/recordings/<clientId>/ with
 * meta.json and the chunks 000000.aac, 000001.aac, ... A chunk is written as
 * .part and renamed when it is complete, so a crash leaves whole chunks plus at
 * most one partial one, which recoverParts() cuts back to its whole frames.
 */
class Store(ctx: Context) {
    private val root = File(ctx.filesDir, "recordings").apply { mkdirs() }

    fun dir(id: String) = File(root, id)
    fun chunkFile(id: String, idx: Int) = File(dir(id), "%06d.aac".format(idx))
    fun partFile(id: String, idx: Int) = File(dir(id), "%06d.aac.part".format(idx))

    fun create(title: String, storage: String): LocalRecording {
        val r = LocalRecording(UUID.randomUUID().toString(), title.trim(), Instant.now().toString(), storage)
        dir(r.clientId).mkdirs()
        write(r)
        return r
    }

    fun get(id: String): LocalRecording? = synchronized(LOCK) {
        val f = File(dir(id), "meta.json")
        if (!f.exists()) null else try { LocalRecording.fromJson(JSONObject(f.readText())) } catch (e: Exception) { null }
    }

    fun list(): List<LocalRecording> =
        (root.listFiles() ?: emptyArray()).mapNotNull { get(it.name) }.sortedByDescending { it.startedAt }

    /** Reads, changes and writes one recording under a lock, so the recorder and the uploader never undo each other. */
    fun update(id: String, change: (LocalRecording) -> Unit): LocalRecording? = synchronized(LOCK) {
        val r = get(id) ?: return null
        change(r)
        write(r)
        r
    }

    private fun write(r: LocalRecording) = synchronized(LOCK) {
        val tmp = File(dir(r.clientId), "meta.json.tmp")
        tmp.writeText(r.toJson().toString())
        tmp.renameTo(File(dir(r.clientId), "meta.json"))
    }

    /** Indices of the complete chunks, 0..n-1 without gaps. */
    fun completeChunks(id: String): List<Int> {
        val out = mutableListOf<Int>()
        while (chunkFile(id, out.size).exists()) out.add(out.size)
        return out
    }

    /** Turns a chunk left half-written by a crash into a complete one; returns the number of complete chunks. */
    fun recoverParts(id: String): Int {
        (dir(id).listFiles { f -> f.name.endsWith(".aac.part") } ?: emptyArray()).forEach { part ->
            val data = part.readBytes()
            val keep = Adts.completeLength(data)
            if (keep > 0) {
                File(dir(id), part.name.removeSuffix(".part")).writeBytes(data.copyOf(keep))
            }
            part.delete()
        }
        return completeChunks(id).size
    }

    /** The length of what the complete chunks hold, from their frames. */
    fun audioMs(id: String): Long =
        completeChunks(id).sumOf { Adts.frameCount(chunkFile(id, it).readBytes()).toLong() } * Adts.FRAME_MS

    fun deleteAudio(id: String) {
        (dir(id).listFiles { f -> f.name.contains(".aac") } ?: emptyArray()).forEach { it.delete() }
    }

    fun remove(id: String) {
        dir(id).deleteRecursively()
    }

    companion object {
        private val LOCK = Any()
    }
}

/** 0:05, 12:34, 1:02:03 */
fun clock(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

/** The big timer: 00:05, 12:34, 1:02:03 */
fun timer(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) clock(ms) else "%02d:%02d".format(s / 60, s % 60)
}
