package com.brainbot.indierecorder

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

class MainActivity : ComponentActivity() {
    private val link = mutableStateOf<String?>(null)
    private val autoPair = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        take(intent)
        setContent { Theme { Root(link, autoPair.value) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        take(intent)
    }

    private fun take(intent: Intent?) {
        val data = intent?.dataString
        if (data != null && data.startsWith("indierecorder://")) {
            link.value = data
            autoPair.value = BuildConfig.DEBUG && intent.getBooleanExtra("auto", false)
        }
        if (BuildConfig.DEBUG && intent != null) testHooks(intent)
    }

    /** Debug builds only, for the end-to-end test over adb: record a WAV file or the microphone, or stop. */
    private fun testHooks(intent: Intent) {
        val title = intent.getStringExtra("test_title") ?: ""
        intent.getStringExtra("test_wav")?.let { wav ->
            RecorderService.start(this, title, intent.getStringExtra("test_storage") ?: "local") {
                putExtra(RecorderService.EXTRA_TEST_WAV, wav)
                putExtra(RecorderService.EXTRA_TEST_SPEED, intent.getFloatExtra("test_speed", 1f))
            }
        }
        if (intent.getBooleanExtra("test_mic", false)) RecorderService.start(this, title, "local")
        if (intent.getBooleanExtra("test_stop", false)) RecorderService.stop(this)
    }
}

// ---- look: the same calm greys as the web view, ink for controls, red only for recording

private val Light = lightColorScheme(
    background = Color(0xFFF4F4F2), surface = Color(0xFFFFFFFF), onBackground = Color(0xFF1A1A1A),
    onSurface = Color(0xFF1A1A1A), primary = Color(0xFF1A1A1A), onPrimary = Color(0xFFFFFFFF),
    outline = Color(0xFFE2E2DF), onSurfaceVariant = Color(0xFF6F6F6A), error = Color(0xFFB3261E),
)
private val Dark = darkColorScheme(
    background = Color(0xFF161615), surface = Color(0xFF1F1F1D), onBackground = Color(0xFFECEBE6),
    onSurface = Color(0xFFECEBE6), primary = Color(0xFFECEBE6), onPrimary = Color(0xFF161615),
    outline = Color(0xFF34332F), onSurfaceVariant = Color(0xFFA3A29B), error = Color(0xFFF2847C),
)
private val RecRed = Color(0xFFC8372D)
private val Ok = Color(0xFF2F7D4F)

@Composable
fun Theme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, content = content)

@Composable
private fun muted() = MaterialTheme.colorScheme.onSurfaceVariant

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    Surface(color = MaterialTheme.colorScheme.background, modifier = Modifier.fillMaxSize()) {
        Column(
            Modifier.fillMaxSize().systemBarsPadding().imePadding().verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp), content = content,
        )
    }
}

@Composable
private fun H1(text: String) = Text(text, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)

@Composable
private fun Label(text: String) =
    Text(text.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = muted(), letterSpacing = 0.5.sp)

// ---- screens

private sealed interface Screen {
    data object Home : Screen
    data class Detail(val meetingId: String) : Screen
    data object Settings : Screen
}

@Composable
fun Root(link: MutableState<String?>, autoPair: Boolean) {
    val ctx = LocalContext.current
    var cfg by remember { mutableStateOf(Pairing.load(ctx)) }
    var screen by remember { mutableStateOf<Screen>(Screen.Home) }
    val pending = link.value
    val current = cfg
    if (pending != null || current == null) {
        PairScreen(pending, autoPair,
            onPaired = { cfg = it; link.value = null; screen = Screen.Home },
            onCancel = if (current != null) ({ link.value = null }) else null)
        return
    }
    when (val s = screen) {
        is Screen.Detail -> {
            BackHandler { screen = Screen.Home }
            DetailScreen(current, s.meetingId, onBack = { screen = Screen.Home })
        }
        Screen.Settings -> {
            BackHandler { screen = Screen.Home }
            SettingsScreen(current, onBack = { screen = Screen.Home },
                onDisconnect = { Pairing.clear(ctx); cfg = null; screen = Screen.Home })
        }
        Screen.Home -> HomeScreen(current, onOpen = { screen = Screen.Detail(it) }, onSettings = { screen = Screen.Settings })
    }
}

@Composable
private fun PairScreen(initial: String?, auto: Boolean, onPaired: (BoxConfig) -> Unit, onCancel: (() -> Unit)?) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var text by remember(initial) { mutableStateOf(initial ?: "") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val bad = stringResource(R.string.pair_bad)
    val unreachable = stringResource(R.string.box_unreachable)
    val rejected = stringResource(R.string.box_rejected)

    fun connect() {
        val cfg = BoxConfig.parse(text) ?: run { error = bad; return }
        busy = true
        error = null
        scope.launch {
            // Only a box that knows the token (and shows the CA from the link) is saved.
            val result = withContext(Dispatchers.IO) { runCatching { BoxApi(cfg).settings() } }
            busy = false
            result.onSuccess { s ->
                Pairing.save(ctx, cfg)
                rememberDefault(ctx, s)
                onPaired(cfg)
            }.onFailure {
                error = if (it is BoxApi.HttpError && it.code == 401) rejected else unreachable
            }
        }
    }
    LaunchedEffect(initial) { if (auto && initial != null) connect() }

    Page {
        Spacer(Modifier.height(24.dp))
        H1(stringResource(R.string.pair_title))
        Text(stringResource(R.string.pair_text), color = muted())
        BoxConfig.parse(text)?.let { Text(it.url + if (it.name.isNotBlank()) " · " + it.name else "") }
        OutlinedTextField(text, { text = it }, label = { Text(stringResource(R.string.pair_link)) },
            maxLines = 3, modifier = Modifier.fillMaxWidth())
        Button(onClick = { connect() }, enabled = !busy && text.isNotBlank()) { Text(stringResource(R.string.pair_button)) }
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        onCancel?.let { TextButton(onClick = it) { Text(stringResource(R.string.back)) } }
    }
}

/** The box's default for new recordings, kept for when the box is not reachable. */
private fun rememberDefault(ctx: Context, settings: JSONObject) {
    val d = settings.optString("storage_default")
    if (d == "local" || d == "drive") ctx.getSharedPreferences("ui", Context.MODE_PRIVATE).edit().putString("storage", d).apply()
}

@Composable
private fun HomeScreen(cfg: BoxConfig, onOpen: (String) -> Unit, onSettings: () -> Unit) {
    val ctx = LocalContext.current
    val store = remember { Store(ctx) }
    val api = remember(cfg) { BoxApi(cfg) }
    val prefs = remember { ctx.getSharedPreferences("ui", Context.MODE_PRIVATE) }
    val active by RecState.active.collectAsState()
    val level by RecState.level.collectAsState()
    val micMuted by RecState.muted.collectAsState()
    val recError by RecState.error.collectAsState()
    var title by rememberSaveable { mutableStateOf("") }
    var drive by remember { mutableStateOf(prefs.getString("storage", "local") == "drive") }
    var touched by remember { mutableStateOf(false) }
    var box by remember { mutableStateOf<String?>(null) }
    var items by remember { mutableStateOf(store.list()) }
    val states = remember { mutableStateMapOf<String, String>() }
    var denied by remember { mutableStateOf(false) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }

    // While the screen is visible: the phone's list every 2 s, the box every 15 s.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(cfg) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var tick = 0
            while (true) {
                items = withContext(Dispatchers.IO) { store.list() }
                if (tick % 7 == 0 || box != "ok") {
                    box = withContext(Dispatchers.IO) { runCatching { api.settings() } }.fold(
                        onSuccess = { s ->
                            rememberDefault(ctx, s)
                            if (!touched) drive = prefs.getString("storage", "local") == "drive"
                            "ok"
                        },
                        onFailure = { if (it is BoxApi.HttpError && it.code == 401) "rejected" else "unreachable" })
                }
                if (tick % 3 == 0) {
                    for (r in items) {
                        val mid = r.meetingId ?: continue
                        if (states[mid] == "done" || states[mid] == "failed") continue
                        withContext(Dispatchers.IO) { runCatching { api.meeting(mid).optString("state") } }
                            .onSuccess { states[mid] = it }
                    }
                }
                tick++
                delay(2000)
            }
        }
    }
    LaunchedEffect(active) {
        while (active != null) {
            now = System.currentTimeMillis()
            delay(500)
        }
    }

    val needed = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
    }.toTypedArray()
    fun start() {
        RecorderService.start(ctx, title, if (drive) "drive" else "local")
        title = ""
        denied = false
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { res ->
        if (res[Manifest.permission.RECORD_AUDIO] == true) start() else denied = true
    }
    fun record() {
        if (needed.all { ContextCompat.checkSelfPermission(ctx, it) == PackageManager.PERMISSION_GRANTED }) start()
        else ask.launch(needed)
    }

    Page {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(9.dp).clip(CircleShape).background(RecRed))
            Spacer(Modifier.width(8.dp))
            Text(stringResource(R.string.app_name), fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onSettings) { Text(stringResource(R.string.settings), color = muted()) }
        }
        when (box) {
            "ok" -> Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(Ok))
                Spacer(Modifier.width(6.dp))
                Text(stringResource(R.string.box_ready) + " · " + cfg.host, fontSize = 13.sp, color = muted())
            }
            "unreachable" -> Text(stringResource(R.string.box_unreachable), fontSize = 13.sp, color = muted())
            "rejected" -> Text(stringResource(R.string.box_rejected), fontSize = 13.sp, color = MaterialTheme.colorScheme.error)
        }

        val a = active
        if (a == null) {
            OutlinedTextField(title, { title = it }, label = { Text(stringResource(R.string.title_hint)) },
                singleLine = true, modifier = Modifier.fillMaxWidth())
            Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(drive, { drive = it; touched = true })
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(stringResource(R.string.drive))
                    Text(stringResource(if (drive) R.string.drive_on else R.string.drive_off), fontSize = 13.sp, color = muted())
                }
            }
            val recordLabel = stringResource(R.string.record)
            Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.size(88.dp).clip(CircleShape).background(RecRed).clickable { record() }
                    .semantics { contentDescription = recordLabel })
                Spacer(Modifier.height(8.dp))
                Text(recordLabel, color = muted())
            }
        } else {
            val stopLabel = stringResource(R.string.stop)
            Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(clock(now - a.startedMs), fontSize = 44.sp, fontWeight = FontWeight.Light)
                Spacer(Modifier.height(12.dp))
                LinearProgressIndicator(progress = { level }, modifier = Modifier.fillMaxWidth().height(6.dp),
                    color = RecRed, trackColor = MaterialTheme.colorScheme.outline, gapSize = 0.dp, drawStopIndicator = {})
                if (micMuted) {
                    Spacer(Modifier.height(8.dp))
                    Text(stringResource(R.string.notif_muted), color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.height(20.dp))
                Box(Modifier.size(88.dp).clip(CircleShape).border(2.dp, MaterialTheme.colorScheme.outline, CircleShape)
                    .clickable { RecorderService.stop(ctx) }.semantics { contentDescription = stopLabel },
                    contentAlignment = Alignment.Center) {
                    Box(Modifier.size(30.dp).clip(RoundedCornerShape(4.dp)).background(RecRed))
                }
                Spacer(Modifier.height(8.dp))
                Text(stopLabel, color = muted())
            }
        }
        recError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        if (denied) Text(stringResource(R.string.mic_denied), color = MaterialTheme.colorScheme.error)

        Spacer(Modifier.height(4.dp))
        Label(stringResource(R.string.recordings))
        if (items.isEmpty()) Text(stringResource(R.string.none_yet), color = muted())
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(2.dp)).background(MaterialTheme.colorScheme.surface)) {
            items.forEachIndexed { i, r ->
                if (i > 0) HorizontalDivider(color = MaterialTheme.colorScheme.outline)
                RecordingRow(r, r.meetingId?.let { states[it] }, onClick = {
                    r.meetingId?.let(onOpen) ?: UploadWorker.enqueue(ctx, r.clientId, now = true)
                })
            }
        }
    }
}

@Composable
private fun RecordingRow(r: LocalRecording, meetingState: String?, onClick: () -> Unit) {
    val status = when {
        r.state == LocalRecording.RECORDING -> stringResource(R.string.st_recording)
        r.state == LocalRecording.STOPPED && r.error == "rejected" -> stringResource(R.string.box_rejected)
        r.state == LocalRecording.STOPPED && r.uploaded < r.chunks && r.error == "unreachable" ->
            stringResource(R.string.st_waiting) + " · ${r.uploaded}/${r.chunks}"
        r.state == LocalRecording.STOPPED && r.uploaded < r.chunks ->
            stringResource(R.string.st_uploading, r.uploaded, r.chunks)
        r.state == LocalRecording.STOPPED -> stringResource(R.string.st_waiting)
        meetingState == "done" -> stringResource(R.string.st_done)
        meetingState == "failed" -> stringResource(R.string.st_failed)
        else -> stringResource(R.string.st_processing)
    }
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(r.title.ifBlank { stringResource(R.string.untitled) }, maxLines = 1)
            val length = if (r.durationMs > 0) " · " + clock(r.durationMs) else ""
            Text(when_(r.startedAt) + length + (if (r.storage == "drive") " · Drive" else ""), fontSize = 13.sp, color = muted())
            if (r.interrupted) Text(stringResource(R.string.interrupted), fontSize = 12.sp, color = muted())
        }
        Spacer(Modifier.width(12.dp))
        Text(status, fontSize = 12.sp,
            color = if (meetingState == "failed") MaterialTheme.colorScheme.error else muted())
    }
}

@Composable
private fun DetailScreen(cfg: BoxConfig, mid: String, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val api = remember(cfg) { BoxApi(cfg) }
    val scope = rememberCoroutineScope()
    var m by remember { mutableStateOf<JSONObject?>(null) }
    var unreachable by remember { mutableStateOf(false) }
    var drive by remember { mutableStateOf(false) }

    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(mid) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                withContext(Dispatchers.IO) { runCatching { api.meeting(mid) } }
                    .onSuccess { m = it; drive = it.optString("storage") == "drive"; unreachable = false }
                    .onFailure { unreachable = true }
                val state = m?.optString("state")
                if (state == "done" || state == "failed") break
                delay(5000)
            }
        }
    }

    Page {
        TextButton(onClick = onBack) { Text("‹ " + stringResource(R.string.back), color = muted()) }
        val mm = m
        if (mm == null) {
            if (unreachable) Text(stringResource(R.string.box_unreachable), color = muted())
            return@Page
        }
        H1(mm.optString("title").ifBlank { mid })
        val speakers = mm.optInt("speakers")
        Text(when_(mm.optString("started_at")) + " · " + clock((mm.optDouble("duration_s", 0.0) * 1000).toLong()) +
            if (speakers > 0) " · $speakers" else "", fontSize = 14.sp, color = muted())
        Row(verticalAlignment = Alignment.CenterVertically) {
            Switch(drive, { v ->
                drive = v
                scope.launch(Dispatchers.IO) { runCatching { api.setStorage(mid, if (v) "drive" else "local") } }
            })
            Spacer(Modifier.width(12.dp))
            Text(stringResource(R.string.store_drive))
            mm.optString("drive_url").takeIf { it.startsWith("https://") }?.let { url ->
                Spacer(Modifier.weight(1f))
                TextButton(onClick = { open(ctx, url) }) { Text("Drive", color = muted()) }
            }
        }
        if (mm.optString("state") != "done") Text(stringResource(R.string.not_ready), color = muted())
        else Summary(mm.optString("summary_md"))
        mm.optString("url").takeIf { it.startsWith("https://") }?.let { url ->
            TextButton(onClick = { open(ctx, url) }) { Text(stringResource(R.string.open_box), color = muted()) }
        }
    }
}

/**
 * The box's summary.md, drawn like the web view: the title, byline and link
 * lines are left out (the screen shows them), the TL;DR is the lead, tables
 * of action items become "text" with "owner · due" under it.
 */
@Composable
private fun Summary(md: String) {
    val lines = md.lines()
    var i = 0
    fun skipBlank() { while (i < lines.size && lines[i].isBlank()) i++ }
    skipBlank(); if (i < lines.size && lines[i].startsWith("# ")) i++
    skipBlank(); if (i < lines.size && lines[i].startsWith("_") && lines[i].endsWith("_")) i++
    skipBlank(); if (i < lines.size && lines[i].startsWith("http")) i++
    var headings = 0
    var lead = false
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        while (i < lines.size) {
            val line = lines[i].trimEnd()
            val next = lines.getOrNull(i + 1)?.trim() ?: ""
            when {
                line.startsWith("## ") -> {
                    headings++
                    if (headings == 1) lead = true else {
                        Spacer(Modifier.height(8.dp))
                        Label(line.removePrefix("## "))
                    }
                }
                line.startsWith("|") && next.matches(Regex("^\\|[-| :]+\\|$")) -> i++      // header row and its rule
                line.startsWith("|") -> {
                    val cells = line.trim('|').split(" | ").map { it.trim().replace("\\|", "|") }
                    val text = cells.getOrNull(1) ?: line
                    val who = listOfNotNull(cells.getOrNull(2), cells.getOrNull(3)).filter { it.isNotBlank() }.joinToString(" · ")
                    Column {
                        Text("•  " + plain(text))
                        if (who.isNotBlank()) Text("    $who", fontSize = 13.sp, color = muted())
                    }
                }
                line.startsWith("- ") || line.startsWith("* ") -> Text("•  " + plain(line.substring(2)))
                line.isBlank() -> {}
                else -> {
                    Text(plain(line), fontSize = if (lead) 17.sp else 15.sp)
                    lead = false
                }
            }
            i++
        }
    }
}

@Composable
private fun SettingsScreen(cfg: BoxConfig, onBack: () -> Unit, onDisconnect: () -> Unit) {
    val ctx = LocalContext.current
    val pm = remember { ctx.getSystemService(PowerManager::class.java) }
    var ignoring by remember { mutableStateOf(pm.isIgnoringBatteryOptimizations(ctx.packageName)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(Unit) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {      // back from the system dialog
            ignoring = pm.isIgnoringBatteryOptimizations(ctx.packageName)
        }
    }
    Page {
        TextButton(onClick = onBack) { Text("‹ " + stringResource(R.string.back), color = muted()) }
        H1(stringResource(R.string.settings))
        Text(stringResource(R.string.paired_with, cfg.url) + if (cfg.name.isNotBlank()) " (${cfg.name})" else "")
        if (!ignoring) {
            Spacer(Modifier.height(8.dp))
            Text(stringResource(R.string.battery), fontWeight = FontWeight.SemiBold)
            Text(stringResource(R.string.battery_text), color = muted())
            Button(onClick = {
                ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + ctx.packageName)))
            }) { Text(stringResource(R.string.battery)) }
        }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(onClick = onDisconnect) { Text(stringResource(R.string.disconnect)) }
        Text("indie recorder " + BuildConfig.VERSION_NAME, fontSize = 12.sp, color = muted())
    }
}

// ---- small helpers

private fun open(ctx: Context, url: String) =
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }

private fun plain(text: String) = text.replace("**", "").replace("`", "")

private fun when_(iso: String): String = runCatching {
    DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT).format(Instant.parse(iso).atZone(ZoneId.systemDefault()))
}.getOrDefault(iso)
