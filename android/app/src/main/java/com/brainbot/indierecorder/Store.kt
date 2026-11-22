package com.brainbot.indierecorder

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
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
    /** The seal (Seal.kt): the box's key id, this recording's public half, its chunk key wrapped by the Keystore. */
    var sealKid: String? = null,
    var sealEpk: String? = null,
    var sealKey: String? = null,
    /** AAC frames in the complete chunks, counted as they are sealed (and again by recoverParts): the length after a crash. */
    var frames: Long = 0,
) {
    val sealed get() = sealEpk != null

    fun toJson(): JSONObject = JSONObject()
        .put("clientId", clientId).put("title", title).put("startedAt", startedAt).put("storage", storage)
        .put("state", state).put("chunks", chunks).put("durationMs", durationMs)
        .put("recordingId", recordingId ?: JSONObject.NULL).put("meetingId", meetingId ?: JSONObject.NULL)
        .put("uploaded", uploaded).put("error", error ?: JSONObject.NULL).put("interrupted", interrupted)
        .put("sealKid", sealKid ?: JSONObject.NULL).put("sealEpk", sealEpk ?: JSONObject.NULL)
        .put("sealKey", sealKey ?: JSONObject.NULL).put("frames", frames)

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
            o.optString("sealKid").takeIf { it.isNotBlank() && it != "null" },
            o.optString("sealEpk").takeIf { it.isNotBlank() && it != "null" },
            o.optString("sealKey").takeIf { it.isNotBlank() && it != "null" },
            o.optLong("frames"),
        )
    }
}

/**
 * Recordings in the app's private storage: files/recordings/<clientId>/ with
 * meta.json and the chunks 000000.aac, 000001.aac, ... A chunk is written as
 * .part; when it is complete it is sealed for the box (Seal.kt) into its chunk
 * file and the .part goes, so a complete chunk never stays on the phone in the
 * clear. A crash leaves whole chunks plus at most one partial one, which
 * recoverParts() cuts back to its whole frames and seals.
 */
class Store(ctx: Context) {
    private val root = File(ctx.filesDir, "recordings").apply { mkdirs() }

    fun dir(id: String) = File(root, id)
    fun chunkFile(id: String, idx: Int) = File(dir(id), "%06d.aac".format(idx))
    fun partFile(id: String, idx: Int) = File(dir(id), "%06d.aac.part".format(idx))

    fun create(title: String, storage: String, clientId: String = UUID.randomUUID().toString(),
               seal: Seal.Start? = null): LocalRecording {
        val r = LocalRecording(clientId, title.trim(), Instant.now().toString(), storage,
            sealKid = seal?.kid, sealEpk = seal?.epk, sealKey = seal?.let { Keystore.encrypt(CHUNK_KEYS, it.key) })
        dir(r.clientId).mkdirs()
        write(r)
        return r
    }

    /** The recording's chunk key; null when it is not sealed, or when the Keystore lost its key. */
    fun chunkKey(r: LocalRecording): ByteArray? =
        r.sealKey?.let { runCatching { Keystore.decrypt(CHUNK_KEYS, it) }.getOrNull() }

    /**
     * A chunk is complete: its frames become the chunk file, sealed when the recording is
     * (written whole and synced before the .part goes). Returns the number of AAC frames.
     */
    fun completeChunk(id: String, idx: Int, plain: ByteArray, key: ByteArray?): Int {
        val frames = Adts.frameCount(plain)
        val tmp = File(dir(id), "%06d.aac.tmp".format(idx))
        FileOutputStream(tmp).use { out ->
            out.write(if (key != null) Seal.seal(key, id, idx, plain) else plain)
            out.fd.sync()
        }
        tmp.renameTo(chunkFile(id, idx))
        partFile(id, idx).delete()
        update(id) { it.frames += frames }
        return frames
    }

    /** A sealed recording whose seal went before anything was sent: the same chunks, sealed for another key. */
    fun reseal(id: String, old: ByteArray, seal: Seal.Start) {
        for (idx in completeChunks(id)) {
            val plain = Seal.open(old, id, idx, chunkFile(id, idx).readBytes())
            val tmp = File(dir(id), "%06d.aac.tmp".format(idx))
            FileOutputStream(tmp).use { out -> out.write(Seal.seal(seal.key, id, idx, plain)); out.fd.sync() }
            tmp.renameTo(chunkFile(id, idx))
        }
        update(id) {
            it.sealKid = seal.kid
            it.sealEpk = seal.epk
            it.sealKey = Keystore.encrypt(CHUNK_KEYS, seal.key)
        }
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

    /**
     * Turns a chunk left half-written by a crash into a complete one, sealed like the others;
     * returns the number of complete chunks. A sealed recording whose key the Keystore lost
     * drops that last part rather than keep it in the clear.
     */
    fun recoverParts(id: String): Int {
        val r = get(id)
        val key = r?.let { chunkKey(it) }
        (dir(id).listFiles { f -> f.name.endsWith(".aac.part") } ?: emptyArray()).forEach { part ->
            val idx = part.name.substringBefore('.').toIntOrNull()
            val data = part.readBytes()
            val keep = Adts.completeLength(data)
            when {
                idx == null || chunkFile(id, idx).exists() -> part.delete()      // sealed already: the crash came after
                keep > 0 && (key != null || r?.sealed == false) -> completeChunk(id, idx, data.copyOf(keep), key)
                else -> part.delete()
            }
        }
        (dir(id).listFiles { f -> f.name.endsWith(".aac.tmp") } ?: emptyArray()).forEach { it.delete() }
        // counted again from the chunks: a crash between sealing a chunk and counting it loses nothing
        if (r != null && (key != null || !r.sealed)) countFrames(id, key)?.let { n -> update(id) { it.frames = n } }
        return completeChunks(id).size
    }

    private fun countFrames(id: String, key: ByteArray?): Long? = runCatching {
        completeChunks(id).sumOf { idx ->
            val data = chunkFile(id, idx).readBytes()
            Adts.frameCount(if (key != null) Seal.open(key, id, idx, data) else data).toLong()
        }
    }.getOrNull()

    /** The length of what the complete chunks hold, from their frames (counted by recoverParts). */
    fun audioMs(id: String): Long = (get(id)?.frames ?: 0) * Adts.FRAME_MS

    fun deleteAudio(id: String) {
        (dir(id).listFiles { f -> f.name.contains(".aac") } ?: emptyArray()).forEach { it.delete() }
    }

    fun remove(id: String) {
        dir(id).deleteRecursively()
    }

    companion object {
        private val LOCK = Any()
        private const val CHUNK_KEYS = "indie-recorder-chunks"
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
