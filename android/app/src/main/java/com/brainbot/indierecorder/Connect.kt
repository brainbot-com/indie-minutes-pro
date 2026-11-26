package com.brainbot.indierecorder

import android.content.Context
import android.os.Build
import android.provider.Settings
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.URLDecoder
import java.util.Base64
import java.util.concurrent.TimeUnit

/**
 * A connect code from the Meetings app (Settings, Connect a phone): 8
 * characters, shown as XXXX-XXXX, valid once within 15 minutes. The phone
 * spends it for a token of its own; the code itself carries no token.
 */
object ConnectCode {
    const val ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"      // no 0 and O, no 1, I and L
    const val LENGTH = 8

    /** Typed or pasted: case, spaces and dashes do not matter, and a connect link gives the part after '#'. */
    fun normalize(text: String): String {
        val t = text.trim()
        val tail = if ('#' in t) t.substringAfterLast('#') else t
        return tail.uppercase().filter { it in ALPHABET }.take(LENGTH)
    }

    fun shown(code: String): String = if (code.length > 4) code.take(4) + "-" + code.drop(4) else code
}

/**
 * The box's address, typed on the connect screen when the build has no connect address of its
 * own (the build on GitHub, for any Indie Box): "box.local:9207", or the connect page's link.
 */
object BoxAddress {
    private const val PREFS = "connect"     // not "box": Disconnect clears that one

    /** Its base URL ("https://box.local:9207"), or null when it names no host. */
    fun base(text: String): String? {
        var t = text.trim().substringBefore('#').substringBefore('?').trimEnd('/')
        if (t.isEmpty() || t.any { it.isWhitespace() }) return null
        if (!t.startsWith("https://", ignoreCase = true) && !t.startsWith("http://", ignoreCase = true)) t = "https://$t"
        t = t.removeSuffix("/connect").removeSuffix("/app").trimEnd('/')
        val host = try { java.net.URI(t).host } catch (e: Exception) { null }
        return if (host.isNullOrBlank()) null else t
    }

    /** The one typed last, so a phone connected again finds it filled in. */
    fun last(ctx: Context): String = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("address", "") ?: ""

    fun remember(ctx: Context, text: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("address", text.trim()).apply()
    }
}

/** What a link opens the app with. */
sealed interface PairLink {
    /**
     * From the connect page: indierecorder://pair?c=<code>&u=<the box's https address>[&ca=<its CA, base64url DER>].
     * Without ca (an Indie Box: a public certificate, or its own CA installed on the phone) the
     * phone's trust store decides; a ca that does not decode is refused.
     */
    class Code(val code: String, val url: String, val caDer: ByteArray) : PairLink

    /** The older link with the phone's token in it (Devices page before connect codes). */
    class Token(val cfg: BoxConfig) : PairLink

    companion object {
        fun parse(link: String): PairLink? {
            val text = link.trim()
            if (!text.startsWith("indierecorder://pair?")) return null
            val q = text.substringAfter('?').split('&').mapNotNull {
                val i = it.indexOf('=')
                if (i <= 0) null else it.substring(0, i) to URLDecoder.decode(it.substring(i + 1), "UTF-8")
            }.toMap()
            val c = q["c"] ?: return BoxConfig.parse(text)?.let(::Token)
            val code = ConnectCode.normalize(c)
            val url = q["u"]?.trimEnd('/') ?: return null
            val der = q["ca"]?.let { decodeCa(it) ?: return null } ?: ByteArray(0)
            if (code.length != ConnectCode.LENGTH || !url.startsWith("https://")) return null
            return Code(code, url, der)
        }

        fun decodeCa(text: String): ByteArray? =
            try { Base64.getUrlDecoder().decode(text.trimEnd('=')) } catch (e: IllegalArgumentException) { null }
    }
}

/** What a code would connect to, before the phone spends it (GET /api/v1/pair/<code>). */
class Offer(val person: String?, val box: String, val boxUrl: String, val caDer: ByteArray?) {
    fun toJson(): String = JSONObject().put("person", person ?: JSONObject.NULL).put("box", box).put("box_url", boxUrl)
        .put("ca", caDer?.let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) } ?: JSONObject.NULL).toString()

    companion object {
        fun from(o: JSONObject): Offer? {
            val url = o.optString("box_url").trimEnd('/')
            if (!url.startsWith("https://")) return null
            val ca = o.optString("ca").takeIf { it.isNotBlank() && it != "null" }
            return Offer(text(o, "person"), text(o, "box") ?: url.removePrefix("https://").substringBefore('/'), url,
                ca?.let { PairLink.decodeCa(it) ?: return null })
        }

        fun parse(json: String?): Offer? = json?.let { try { from(JSONObject(it)) } catch (e: JSONException) { null } }
    }
}

/** The phone's own token and what to show about it, from spending a code (POST /api/v1/pair). */
class Joined(val cfg: BoxConfig, val person: String?, val box: String)

/** A string field, or null when it is missing, empty or JSON null. */
fun text(o: JSONObject, key: String): String? = o.optString(key).takeIf { it.isNotBlank() && it != "null" }

/**
 * A name for this phone in the person's list of phones: the name its owner gave it ("Luis's S24",
 * Settings, About phone), else its Bluetooth name, else maker and model ("Pixel 8", "Samsung SM-S911B").
 */
fun deviceName(ctx: Context): String {
    val given = sequenceOf(
        { Settings.Global.getString(ctx.contentResolver, Settings.Global.DEVICE_NAME) },
        { Settings.Secure.getString(ctx.contentResolver, "bluetooth_name") },    // Android 12 and later may refuse it
    ).mapNotNull { read -> runCatching(read).getOrNull()?.trim() }.firstOrNull { it.isNotEmpty() }
    return given?.take(60) ?: readableDevice(Build.MANUFACTURER ?: "", Build.MODEL ?: "")
}

/** Maker and model, readable: "Pixel 8", "Samsung SM-S911B". */
fun readableDevice(maker: String, model: String): String {
    val m = maker.trim().takeUnless { it.equals("unknown", ignoreCase = true) } ?: ""   // emulators and some builds
    val d = model.trim()
    val name = when {
        m.isEmpty() || d.startsWith(m, ignoreCase = true) -> d
        m.equals("Google", ignoreCase = true) -> d                     // Pixel phones say "Pixel 8" themselves
        else -> m.replaceFirstChar { it.uppercase() } + " " + d
    }
    return name.ifBlank { "Phone" }.take(60)
}

/**
 * Connecting with a code: look at what it connects to, then spend it. Talks
 * to the box with its pinned CA when the link brought one, else with the
 * phone's own trust store (the connect address has a public certificate).
 */
class ConnectApi(private val base: String, caDer: ByteArray?) {
    enum class Kind { USED, TOO_MANY, UNREACHABLE, OTHER }

    class Failure(val kind: Kind, val detail: String) : IOException(detail)

    private val http: OkHttpClient = if (caDer != null && caDer.isNotEmpty()) BoxApi.client(caDer) else
        OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()

    fun peek(code: String): Offer {
        val o = call(Request.Builder().url("$base/api/v1/pair/$code").get().build())
        return Offer.from(o) ?: throw Failure(Kind.OTHER, "the box sent no https address")
    }

    fun redeem(code: String, device: String): Joined {
        val body = JSONObject().put("code", code).put("device", device).toString().toRequestBody("application/json".toMediaType())
        val o = call(Request.Builder().url("$base/api/v1/pair").post(body).build())
        val url = o.optString("box_url").trimEnd('/')
        val token = o.optString("token")
        if (!url.startsWith("https://") || token.length < 16) throw Failure(Kind.OTHER, "the box sent no token")
        val ca = text(o, "ca")?.let { PairLink.decodeCa(it) } ?: ByteArray(0)
        return Joined(BoxConfig(url, token, ca, text(o, "device") ?: device), text(o, "person"), text(o, "box") ?: "")
    }

    private fun call(request: Request): JSONObject {
        val (code, text) = try {
            http.newCall(request).execute().use { r -> r.code to (r.body?.string() ?: "") }
        } catch (e: IOException) {
            throw Failure(Kind.UNREACHABLE, e.message ?: "no answer")
        }
        when (code) {
            in 200..299 -> return try { JSONObject(text) } catch (e: JSONException) { throw Failure(Kind.OTHER, "no JSON") }
            404 -> throw Failure(Kind.USED, "HTTP 404")
            429 -> throw Failure(Kind.TOO_MANY, "HTTP 429")
            else -> throw Failure(Kind.OTHER, "HTTP $code")
        }
    }

    companion object {
        /** Where a typed code is looked up; debug builds can point the end-to-end test at the box itself. */
        var base: String = BuildConfig.CONNECT_BASE
        var baseCa: ByteArray? = null
    }
}
