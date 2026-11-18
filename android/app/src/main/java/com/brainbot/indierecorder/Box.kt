package com.brainbot.indierecorder

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.IOException
import java.net.URLDecoder
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.util.Base64
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/** The box this phone is paired with: its address, the phone's token and the box's own CA. */
class BoxConfig(val url: String, val token: String, val caDer: ByteArray, val name: String) {
    companion object {
        /**
         * The older pairing link with the phone's token in it (connect codes replaced it, PairLink):
         * indierecorder://pair?u=<https url>&t=<token>&ca=<CA certificate, base64url DER>&n=<name>.
         * The CA comes with the link, so the phone trusts that box and nothing else.
         */
        fun parse(link: String): BoxConfig? {
            val text = link.trim()
            if (!text.startsWith("indierecorder://pair?")) return null
            val q = text.substringAfter('?').split('&').mapNotNull {
                val i = it.indexOf('=')
                if (i <= 0) null else it.substring(0, i) to URLDecoder.decode(it.substring(i + 1), "UTF-8")
            }.toMap()
            val url = q["u"]?.trimEnd('/') ?: return null
            val token = q["t"] ?: return null
            val ca = q["ca"] ?: return null
            if (!url.startsWith("https://") || token.length < 16) return null
            val der = try { Base64.getUrlDecoder().decode(ca.trimEnd('=')) } catch (e: IllegalArgumentException) { return null }
            return BoxConfig(url, token, der, q["n"] ?: "")
        }
    }

    val host: String get() = url.removePrefix("https://").substringBefore('/')
}

/** Whom the phone records for, as the box last said (GET /api/v1/me): the person and this phone's name. */
class Me(val person: String?, val device: String)

/** The pairing as stored on the phone; the token is encrypted with an Android Keystore key. */
object Pairing {
    private const val PREFS = "box"
    private const val KEY_ALIAS = "indie-recorder-token"

    fun load(ctx: Context): BoxConfig? {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val url = p.getString("url", null) ?: return null
        val token = try { decrypt(p.getString("token", null) ?: return null) } catch (e: Exception) { return null }
        val ca = Base64.getDecoder().decode(p.getString("ca", null) ?: return null)
        return BoxConfig(url, token, ca, p.getString("name", "") ?: "")
    }

    fun save(ctx: Context, cfg: BoxConfig) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("url", cfg.url)
            .putString("token", encrypt(cfg.token))
            .putString("ca", Base64.getEncoder().encodeToString(cfg.caDer))
            .putString("name", cfg.name)
            .remove("me_device").remove("me_person")       // a new pairing may record for someone else
            .remove("box_name").remove("seal_kid").remove("seal_key")
            .apply()
    }

    /** The box's sealing key as its health answer last gave it (public); null from a box without one. */
    fun sealKey(ctx: Context): Seal.BoxKey? {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val kid = p.getString("seal_kid", null) ?: return null
        val key = p.getString("seal_key", null) ?: return null
        return runCatching { Seal.BoxKey(kid, Seal.unb64u(key)) }.getOrNull()
    }

    /** From GET /api/v1/health: {"seal": {"kid", "alg", "key"}}; kept so a recording started offline is sealed too. */
    fun saveSealKey(ctx: Context, health: JSONObject) {
        val seal = health.optJSONObject("seal") ?: return
        if (seal.optString("alg") != "ECDH-P256+HKDF-SHA256+A256GCM") return
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("seal_kid", seal.getString("kid")).putString("seal_key", seal.getString("key")).apply()
    }

    /** The box's own name from its health answer ("aibox1"), for the screens' top bars. */
    fun boxName(ctx: Context): String? = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("box_name", null)

    fun saveBoxName(ctx: Context, name: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("box_name", name).apply()
    }

    fun me(ctx: Context): Me? {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return Me(p.getString("me_person", null), p.getString("me_device", null) ?: return null)
    }

    fun saveMe(ctx: Context, me: JSONObject) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString("me_device", me.optString("device"))
            .putString("me_person", me.optString("person").takeIf { it.isNotBlank() && it != "null" })
            .apply()
    }

    fun clear(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    private fun encrypt(plain: String): String = Keystore.encrypt(KEY_ALIAS, plain.toByteArray())

    private fun decrypt(stored: String): String = String(Keystore.decrypt(KEY_ALIAS, stored))
}

/** Secrets kept on the phone, encrypted with keys that never leave the Android Keystore: "iv:ciphertext". */
object Keystore {
    private fun key(alias: String): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        kg.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return kg.generateKey()
    }

    fun encrypt(alias: String, plain: ByteArray): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key(alias))
        val b64 = Base64.getEncoder()
        return b64.encodeToString(c.iv) + ":" + b64.encodeToString(c.doFinal(plain))
    }

    fun decrypt(alias: String, stored: String): ByteArray {
        val (iv, ct) = stored.split(":").map { Base64.getDecoder().decode(it) }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(alias), GCMParameterSpec(128, iv))
        return c.doFinal(ct)
    }
}

/** The recorder's upload and meetings API, over HTTPS that trusts only the box's CA. */
class BoxApi(val cfg: BoxConfig) {
    class HttpError(val code: Int, val body: String) : IOException("HTTP $code: ${body.take(200)}")

    private val http: OkHttpClient = client(cfg.caDer)
    private fun send(method: String, path: String, body: ByteArray? = null, type: String = "application/json",
                     headers: Map<String, String> = emptyMap()): String {
        val b = Request.Builder().url(cfg.url + path).header("Authorization", "Bearer " + cfg.token)
        headers.forEach { (k, v) -> b.header(k, v) }
        b.method(method, body?.toRequestBody(type.toMediaType()))
        http.newCall(b.build()).execute().use { r ->
            val text = r.body?.string() ?: ""
            if (!r.isSuccessful) throw HttpError(r.code, text)
            return text
        }
    }

    private fun post(path: String, o: JSONObject) = JSONObject(send("POST", path, o.toString().toByteArray()))

    fun health(): JSONObject = JSONObject(send("GET", "/api/v1/health"))
    fun settings(): JSONObject = JSONObject(send("GET", "/api/v1/settings"))
    fun recording(rid: String): JSONObject = JSONObject(send("GET", "/api/v1/recordings/$rid"))
    fun meeting(mid: String): JSONObject = JSONObject(send("GET", "/api/v1/meetings/$mid"))

    /** Whom this phone records for; null from a box that does not tell yet (404). */
    fun me(): JSONObject? = try {
        JSONObject(send("GET", "/api/v1/me"))
    } catch (e: HttpError) {
        if (e.code == 404) null else throw e
    }

    fun createRecording(r: LocalRecording): JSONObject = post("/api/v1/recordings", JSONObject()
        .put("client_id", r.clientId).put("mime", "audio/aac").put("title", r.title.ifBlank { JSONObject.NULL })
        .put("started_at", r.startedAt).put("storage", r.storage)
        .apply { if (r.sealed) put("seal", JSONObject().put("kid", r.sealKid).put("epk", r.sealEpk)) })

    fun putChunk(rid: String, idx: Int, data: ByteArray): JSONObject = JSONObject(send(
        "PUT", "/api/v1/recordings/$rid/chunks/$idx", data, "application/octet-stream",
        mapOf("X-Chunk-SHA256" to sha256(data))))

    fun finalize(rid: String, total: Int, title: String): JSONObject = post("/api/v1/recordings/$rid/finalize",
        JSONObject().put("total_chunks", total).put("title", title.ifBlank { JSONObject.NULL }))

    fun setStorage(mid: String, storage: String) {
        send("PUT", "/api/v1/meetings/$mid/storage", JSONObject().put("storage", storage).toString().toByteArray())
    }

    companion object {
        /** HTTPS that trusts only the box's own CA; a box that sent none is checked against the phone's trust store. */
        fun client(caDer: ByteArray): OkHttpClient {
            val b = OkHttpClient.Builder()
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(90, TimeUnit.SECONDS)
                .writeTimeout(120, TimeUnit.SECONDS)
            if (caDer.isEmpty()) return b.build()
            val ca = CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(caDer))
            val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null); setCertificateEntry("box", ca) }
            val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(ks) }
            val tm = tmf.trustManagers.first { it is X509TrustManager } as X509TrustManager
            val ssl = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
            return b.sslSocketFactory(ssl.socketFactory, tm).build()
        }

        fun sha256(data: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

        fun ints(a: JSONArray?): Set<Int> = (0 until (a?.length() ?: 0)).map { a!!.getInt(it) }.toSet()
    }
}
