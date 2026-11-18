package com.brainbot.indierecorder

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRecordingConfiguration
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.log10
import kotlin.math.sqrt

/** What the screen shows while a recording runs (one process, one recording at a time). */
object RecState {
    class Active(val clientId: String, val startedMs: Long)

    val active = MutableStateFlow<Active?>(null)
    val level = MutableStateFlow(0f)
    val muted = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
}

/**
 * Records in a foreground service of type microphone, so a meeting keeps
 * recording with the screen off. 16 kHz mono from the microphone (the box
 * resamples to 16 kHz anyway), AAC in 30 s chunk files, each sent to the box
 * as soon as it is complete.
 */
class RecorderService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    @Volatile private var running = false
    private var wakeLock: PowerManager.WakeLock? = null
    private lateinit var store: Store

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> if (running) RecState.active.value?.let { show(notification(it.startedMs, false)) } else begin(intent)
            ACTION_STOP -> if (running) running = false else stopSelf()
            else -> if (!running) stopSelf()
        }
        return START_NOT_STICKY
    }

    @SuppressLint("MissingPermission")  // the screen asks for RECORD_AUDIO before it starts the service
    private fun begin(intent: Intent) {
        val startedMs = System.currentTimeMillis()
        show(notification(startedMs, false))
        val source: PcmSource = try {
            val wav = if (BuildConfig.DEBUG) intent.getStringExtra(EXTRA_TEST_WAV) else null
            if (wav != null) WavSource(File(wav), intent.getFloatExtra(EXTRA_TEST_SPEED, 1f)) else MicSource(this)
        } catch (e: Exception) {
            RecState.error.value = e.message ?: "microphone not available"
            finish()
            return
        }
        val r = store.create(intent.getStringExtra(EXTRA_TITLE) ?: "", intent.getStringExtra(EXTRA_STORAGE) ?: "local")
        RecState.error.value = null
        RecState.active.value = RecState.Active(r.clientId, startedMs)
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "indierecorder:recording")
            .apply { acquire(MAX_RECORDING_MS) }
        running = true
        Thread({ capture(r.clientId, source, startedMs) }, "recorder").start()
    }

    private fun capture(id: String, source: PcmSource, startedMs: Long) {
        var writer: AacWriter? = null
        var failure: String? = null
        try {
            writer = AacWriter(store, id) { scope.launch { runCatching { Uploader.sync(this@RecorderService, id) } } }
            source.start()
            val buf = ShortArray(1024)
            var muted = false
            while (running) {
                val n = source.read(buf)
                if (n < 0) break                                    // a test file has ended
                if (n == 0) continue
                RecState.level.value = level(buf, n)
                writer.feed(buf, n)
                if (source.muted != muted) {
                    muted = source.muted
                    RecState.muted.value = muted
                    show(notification(startedMs, muted))
                }
                if (System.currentTimeMillis() - startedMs > MAX_RECORDING_MS) break
            }
        } catch (e: Exception) {
            failure = e.message ?: e.javaClass.simpleName
        } finally {
            runCatching { source.stop() }
            val chunks = try {
                writer?.finish() ?: 0
            } catch (e: Exception) {
                failure = failure ?: e.message
                store.recoverParts(id)
            }
            val ms = writer?.durationMs ?: 0
            store.update(id) {
                it.state = LocalRecording.STOPPED
                it.chunks = chunks
                it.durationMs = ms
                it.error = failure
            }
            RecState.error.value = failure
            RecState.active.value = null
            RecState.level.value = 0f
            RecState.muted.value = false
            UploadWorker.enqueue(this, id, now = true)
            Handler(Looper.getMainLooper()).post { finish() }
        }
    }

    private fun finish() {
        running = false
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun show(n: Notification) {
        val type = if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, n, type)
    }

    private fun notification(startedMs: Long, muted: Boolean): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 1, Intent(this, RecorderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, App.CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_rec)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(if (muted) R.string.notif_muted else R.string.channel_recording))
            .setWhen(startedMs).setUsesChronometer(true).setShowWhen(true)
            .setOngoing(true).setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
            .addAction(0, getString(R.string.notif_stop), stop)
            .build()
    }

    companion object {
        const val ACTION_START = "com.brainbot.indierecorder.START"
        const val ACTION_STOP = "com.brainbot.indierecorder.STOP"
        const val EXTRA_TITLE = "title"
        const val EXTRA_STORAGE = "storage"
        const val EXTRA_TEST_WAV = "test_wav"
        const val EXTRA_TEST_SPEED = "test_speed"
        private const val NOTIFICATION_ID = 1
        private const val MAX_RECORDING_MS = 6 * 3600 * 1000L

        fun start(ctx: Context, title: String, storage: String, extras: Intent.() -> Unit = {}) {
            ContextCompat.startForegroundService(ctx, Intent(ctx, RecorderService::class.java)
                .setAction(ACTION_START).putExtra(EXTRA_TITLE, title).putExtra(EXTRA_STORAGE, storage).apply(extras))
        }

        fun stop(ctx: Context) {
            ctx.startService(Intent(ctx, RecorderService::class.java).setAction(ACTION_STOP))
        }

        /** 0..1 for the level bar: -60 dBFS and below is empty. */
        fun level(buf: ShortArray, n: Int): Float {
            var sum = 0.0
            for (i in 0 until n) sum += buf[i].toDouble() * buf[i]
            val rms = sqrt(sum / n) / 32768.0
            val db = if (rms > 0) 20 * log10(rms) else -100.0
            return ((db + 60) / 60).coerceIn(0.0, 1.0).toFloat()
        }
    }
}

interface PcmSource {
    val muted: Boolean
    fun start()
    /** Samples read into buf; -1 at the end of a test file. */
    fun read(buf: ShortArray): Int
    fun stop()
}

/** The microphone, tuned for speech recognition, with a note when a call takes it over. */
@SuppressLint("MissingPermission")
class MicSource(ctx: Context) : PcmSource {
    private val rec = AudioRecord(
        MediaRecorder.AudioSource.VOICE_RECOGNITION, Adts.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
        AudioFormat.ENCODING_PCM_16BIT,
        maxOf(AudioRecord.getMinBufferSize(Adts.SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 32768))
    private val am = ctx.getSystemService(AudioManager::class.java)
    @Volatile override var muted = false
    private val callback = if (Build.VERSION.SDK_INT >= 29) object : AudioManager.AudioRecordingCallback() {
        override fun onRecordingConfigChanged(configs: MutableList<AudioRecordingConfiguration>) {
            muted = configs.any { it.clientAudioSessionId == rec.audioSessionId && it.isClientSilenced }
        }
    } else null

    init {
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            throw IOException("microphone not available")
        }
    }

    override fun start() {
        callback?.let { am.registerAudioRecordingCallback(it, Handler(Looper.getMainLooper())) }
        rec.startRecording()
    }

    override fun read(buf: ShortArray): Int {
        val n = rec.read(buf, 0, buf.size)
        if (n < 0) throw IOException("microphone read error $n")
        return n
    }

    override fun stop() {
        callback?.let { am.unregisterAudioRecordingCallback(it) }
        try { rec.stop() } finally { rec.release() }
    }
}

/** Debug builds only: a 16 kHz mono 16-bit WAV file in place of the microphone, at real-time pace times speed. */
class WavSource(file: File, private val speed: Float) : PcmSource {
    private val input = DataInputStream(BufferedInputStream(FileInputStream(file)))
    override val muted = false
    private var t0 = 0L
    private var samples = 0L

    override fun start() {
        val head = ByteArray(12)
        input.readFully(head)
        require(String(head, 0, 4) == "RIFF" && String(head, 8, 4) == "WAVE") { "not a WAV file" }
        while (true) {                                    // skip chunks up to "data"
            val id = ByteArray(4).also { input.readFully(it) }
            val size = Integer.reverseBytes(input.readInt())
            if (String(id) == "data") break
            input.skipBytes(size + (size and 1))
        }
        t0 = SystemClock.elapsedRealtime()
    }

    override fun read(buf: ShortArray): Int {
        val bytes = ByteArray(buf.size * 2)
        var got = 0
        while (got < bytes.size) {
            val n = input.read(bytes, got, bytes.size - got)
            if (n < 0) break
            got += n
        }
        if (got < 2) return -1
        ByteBuffer.wrap(bytes, 0, got).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(buf, 0, got / 2)
        samples += got / 2
        val wait = t0 + (samples * 1000 / Adts.SAMPLE_RATE / speed).toLong() - SystemClock.elapsedRealtime()
        if (wait > 0) Thread.sleep(wait)
        return got / 2
    }

    override fun stop() = input.close()
}

/**
 * AAC LC encoder writing 30 s chunk files: ADTS frames, so the box can join
 * them into one stream. Chunks are cut by audio time, not wall time.
 */
class AacWriter(private val store: Store, private val id: String, private val onChunk: (Int) -> Unit) {
    private val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC).apply {
        configure(MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, Adts.SAMPLE_RATE, 1).apply {
            setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
            setInteger(MediaFormat.KEY_BIT_RATE, 48000)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 16384)
        }, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        start()
    }
    private val info = MediaCodec.BufferInfo()
    private var chunk = 0
    private var out = FileOutputStream(store.partFile(id, 0))
    private var written = 0L
    private var samples = 0L
    val durationMs get() = samples * 1000 / Adts.SAMPLE_RATE

    fun feed(pcm: ShortArray, n: Int) {
        var off = 0
        while (off < n) {
            val idx = codec.dequeueInputBuffer(10_000)
            if (idx < 0) {
                drain(false)
                continue
            }
            val inBuf = codec.getInputBuffer(idx)!!.apply { clear(); order(ByteOrder.nativeOrder()) }
            val count = minOf(n - off, inBuf.remaining() / 2)
            inBuf.asShortBuffer().put(pcm, off, count)
            codec.queueInputBuffer(idx, 0, count * 2, samples * 1_000_000L / Adts.SAMPLE_RATE, 0)
            samples += count
            off += count
            drain(false)
        }
    }

    /** Ends the stream; returns the number of complete chunks. */
    fun finish(): Int {
        var idx = -1
        for (i in 0 until 100) {
            idx = codec.dequeueInputBuffer(10_000)
            if (idx >= 0) break
            drain(false)
        }
        if (idx >= 0) {
            codec.queueInputBuffer(idx, 0, 0, samples * 1_000_000L / Adts.SAMPLE_RATE, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
            drain(true)
        }
        codec.stop()
        codec.release()
        closeChunk()
        return chunk
    }

    private fun drain(endOfStream: Boolean) {
        var idle = 0
        while (true) {
            val idx = codec.dequeueOutputBuffer(info, if (endOfStream) 10_000 else 0)
            if (idx == MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (!endOfStream || ++idle > 300) return
                continue
            }
            if (idx < 0) continue                                    // format changed: nothing to do with ADTS
            val buf = codec.getOutputBuffer(idx)!!
            if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0 && info.size > 0) {
                if (maxOf(info.presentationTimeUs, 0) / CHUNK_US > chunk && written > 0) {
                    closeChunk()
                    out = FileOutputStream(store.partFile(id, chunk))
                }
                val frame = ByteArray(info.size)
                buf.position(info.offset)
                buf.get(frame)
                out.write(Adts.header(info.size))
                out.write(frame)
                written += frame.size + 7
            }
            codec.releaseOutputBuffer(idx, false)
            if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) return
        }
    }

    private fun closeChunk() {
        out.flush()
        out.fd.sync()
        out.close()
        val part = store.partFile(id, chunk)
        if (written > 0) {
            part.renameTo(store.chunkFile(id, chunk))
            onChunk(chunk)
            chunk += 1
        } else {
            part.delete()
        }
        written = 0
    }

    companion object {
        private const val CHUNK_US = 30_000_000L
    }
}
