package com.brainbot.indierecorder

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, getString(R.string.channel_recording), NotificationManager.IMPORTANCE_LOW))
        resume()
    }

    /**
     * A new process never has a recording running, so one still marked as
     * recording lost its process (killed, crashed, rebooted): keep its whole
     * chunks and send them. Anything not yet with the box is queued again.
     */
    private fun resume() {
        val store = Store(this)
        for (r in store.list()) {
            when (r.state) {
                LocalRecording.RECORDING -> {
                    val chunks = store.recoverParts(r.clientId)
                    store.update(r.clientId) {
                        it.state = LocalRecording.STOPPED
                        it.chunks = chunks
                        it.durationMs = chunks * 30_000L
                        it.interrupted = true
                    }
                    UploadWorker.enqueue(this, r.clientId)
                }
                LocalRecording.STOPPED -> UploadWorker.enqueue(this, r.clientId)
            }
        }
    }

    companion object {
        const val CHANNEL = "recording"
    }
}
