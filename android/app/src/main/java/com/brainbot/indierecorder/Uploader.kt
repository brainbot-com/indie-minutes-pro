package com.brainbot.indierecorder

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Brings a recording to the box with the same resumable API as the PWA:
 * create (idempotent by client id), PUT the chunks the box does not have yet,
 * finalize once the recording is stopped. A chunk leaves the phone only
 * after the box has confirmed the whole recording.
 */
object Uploader {
    private val locks = HashMap<String, Mutex>()
    private fun lock(id: String) = synchronized(locks) { locks.getOrPut(id) { Mutex() } }

    /** True when nothing is left to do for this recording. */
    suspend fun sync(ctx: Context, id: String): Boolean = withContext(Dispatchers.IO) {
        lock(id).withLock { syncLocked(ctx, id) }
    }

    private fun syncLocked(ctx: Context, id: String): Boolean {
        val store = Store(ctx)
        var r = store.get(id) ?: return true
        if (r.state == LocalRecording.FINALIZED) return true
        if (r.state == LocalRecording.STOPPED && r.chunks == 0) {
            store.remove(id)
            return true
        }
        val api = BoxApi(Pairing.load(ctx) ?: return false)
        try {
            val rid = r.recordingId ?: try {
                api.createRecording(r).getString("recording_id")
            } catch (e: BoxApi.HttpError) {
                // The box's sealing key is another one now, and nothing of this recording was sent yet.
                if (e.code == 409 && r.sealed) return if (resealed(ctx, store, api, r)) syncLocked(ctx, id) else false
                throw e
            }.also { new -> store.update(id) { it.recordingId = new } }
            val status = try {
                api.recording(rid)
            } catch (e: BoxApi.HttpError) {
                // Paired again with a new token: the box knows this recording under the old one.
                if (e.code == 404) {
                    store.update(id) { it.recordingId = null }
                    return false
                }
                throw e
            }
            if (status.getString("state") == "finalized") return done(store, id, status)
            val have = BoxApi.ints(status.optJSONArray("received")).toMutableSet()
            for (idx in store.completeChunks(id)) {
                if (idx in have) continue
                api.putChunk(rid, idx, store.chunkFile(id, idx).readBytes())
                have.add(idx)
                store.update(id) { it.uploaded = have.size; it.error = null }
            }
            r = store.update(id) { it.uploaded = have.size; it.error = null } ?: return true
            if (r.state != LocalRecording.STOPPED) return false
            val result = try {
                api.finalize(rid, r.chunks, r.title)
            } catch (e: BoxApi.HttpError) {
                if (e.code == 409) return false   // a chunk is still missing, or another finalize runs
                throw e
            }
            return done(store, id, result)
        } catch (e: IOException) {
            store.update(id) { it.error = describe(e) }
            throw e
        }
    }

    /**
     * Seals a stopped recording's chunks again for the box's current key (fetched anew), so it never
     * goes in the clear; one still recording waits until it stops, a box without a key until it has one.
     */
    private fun resealed(ctx: Context, store: Store, api: BoxApi, r: LocalRecording): Boolean {
        Pairing.saveSealKey(ctx, api.health())
        val box = Pairing.sealKey(ctx) ?: return false
        if (box.kid == r.sealKid || r.state != LocalRecording.STOPPED) return false
        val old = store.chunkKey(r) ?: return false
        store.reseal(r.clientId, old, Seal.start(box, r.clientId))
        return true
    }

    private fun done(store: Store, id: String, status: JSONObject): Boolean {
        store.update(id) {
            it.state = LocalRecording.FINALIZED
            it.meetingId = status.optString("meeting_id").takeIf { m -> m.isNotBlank() && m != "null" }
            it.uploaded = it.chunks
            it.error = null
            it.sealKey = null                        // the chunk key goes with the audio
        }
        store.deleteAudio(id)
        return true
    }

    fun describe(e: Exception): String = when {
        e is BoxApi.HttpError && e.code == 401 -> "rejected"
        e is BoxApi.HttpError -> "HTTP ${e.code}"
        else -> "unreachable"
    }
}

/** Uploads after the recording, in the background, across restarts and reboots. */
class UploadWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getString(KEY) ?: return Result.failure()
        return try {
            if (Uploader.sync(applicationContext, id)) Result.success() else Result.retry()
        } catch (e: Exception) {
            Result.retry()
        }
    }

    companion object {
        private const val KEY = "clientId"

        fun enqueue(ctx: Context, id: String, now: Boolean = false) {
            val req = OneTimeWorkRequestBuilder<UploadWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                // Linear: a VPN that comes back after an hour is found within minutes, not hours.
                .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
                .setInputData(workDataOf(KEY to id))
                .build()
            WorkManager.getInstance(ctx).enqueueUniqueWork(
                "upload-$id", if (now) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP, req)
        }
    }
}
