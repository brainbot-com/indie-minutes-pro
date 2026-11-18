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
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private val link = mutableStateOf<String?>(null)
    private val autoPair = mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        // The paper runs behind both system bars; no grey scrim behind the navigation buttons in dark mode.
        val clear = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT)
        enableEdgeToEdge(statusBarStyle = clear, navigationBarStyle = clear)
        if (Build.VERSION.SDK_INT >= 29) window.isNavigationBarContrastEnforced = false
        super.onCreate(savedInstanceState)
        // A rotation or a switch to dark mode recreates the activity with the same intent: its link
        // (or test hook) was handled already, and the screens keep their own state.
        if (savedInstanceState == null) take(intent) else link.value = savedInstanceState.getString("link")
        setContent { Theme { Root(link, autoPair.value) } }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("link", link.value)
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

    /**
     * Debug builds only, for the end-to-end test over adb: where typed connect codes are looked up
     * (the emulator has no VPN to ai-knowledge, so the box itself with its CA), and record a WAV
     * file or the microphone, pause, resume, stop.
     */
    private fun testHooks(intent: Intent) {
        intent.getStringExtra("test_connect_base")?.let { ConnectApi.base = it.trimEnd('/') }
        intent.getStringExtra("test_connect_ca")?.let { ConnectApi.baseCa = PairLink.decodeCa(it) }
        val title = intent.getStringExtra("test_title") ?: ""
        intent.getStringExtra("test_wav")?.let { wav ->
            RecorderService.start(this, title, intent.getStringExtra("test_storage") ?: "local") {
                putExtra(RecorderService.EXTRA_TEST_WAV, wav)
                putExtra(RecorderService.EXTRA_TEST_SPEED, intent.getFloatExtra("test_speed", 1f))
            }
        }
        if (intent.getBooleanExtra("test_mic", false)) RecorderService.start(this, title, "local")
        if (intent.getBooleanExtra("test_pause", false)) RecorderService.pause(this)
        if (intent.getBooleanExtra("test_resume", false)) RecorderService.resume(this)
        if (intent.getBooleanExtra("test_stop", false)) RecorderService.stop(this)
    }
}

// ---- look: Swiss (direction 5 of the design canvas), the same as the PWA, the web view and the
// Meetings app: Inter, ink on paper, one red for recording and the main action, 3dp rules over
// sections, 1dp rules between rows, square corners.

class Palette(
    val paper: Color, val ink: Color, val muted: Color, val ghost: Color, val faint: Color,
    val dim: Color, val red: Color, val redDark: Color, val onRed: Color,
)

private val LightPalette = Palette(
    paper = Color(0xFFFFFFFF), ink = Color(0xFF111111), muted = Color(0xFF555555), ghost = Color(0xFFD9D9D9),
    faint = Color(0xFFF2F2F2), dim = Color(0xFF767676), red = Color(0xFFE1251B), redDark = Color(0xFFB81D14),
    onRed = Color(0xFFFFFFFF),
)
private val DarkPalette = Palette(
    paper = Color(0xFF111111), ink = Color(0xFFF2F2F2), muted = Color(0xFFA3A3A3), ghost = Color(0xFF3A3A3A),
    faint = Color(0xFF1D1D1D), dim = Color(0xFF767676), red = Color(0xFFE1251B), redDark = Color(0xFFB81D14),
    onRed = Color(0xFFFFFFFF),
)
private val LocalPalette = staticCompositionLocalOf { LightPalette }

private val Inter = FontFamily(
    Font(R.font.inter_regular, FontWeight.W400), Font(R.font.inter_medium, FontWeight.W500),
    Font(R.font.inter_bold, FontWeight.W700), Font(R.font.inter_black, FontWeight.W900),
)

@Composable
fun Theme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val p = if (dark) DarkPalette else LightPalette
    // Material only for what the framework draws itself: text selection and the cursor handle.
    val scheme = if (dark) darkColorScheme(primary = p.ink, background = p.paper, surface = p.paper)
                 else lightColorScheme(primary = p.ink, background = p.paper, surface = p.paper)
    MaterialTheme(colorScheme = scheme) {
        CompositionLocalProvider(
            LocalPalette provides p,
            LocalTextStyle provides TextStyle(fontFamily = Inter, fontSize = 16.sp, lineHeight = 23.sp, color = p.ink),
            content = content,
        )
    }
}

@Composable
private fun pal() = LocalPalette.current

/** Figures of equal width, so a running timer does not wobble. */
@Composable
private fun tnum() = LocalTextStyle.current.merge(TextStyle(fontFeatureSettings = "tnum"))

/** Small capitals over a screen's title or the timer. */
@Composable
private fun Kicker(text: String, color: Color = pal().muted) =
    Text(text.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.W700, letterSpacing = 0.5.sp, color = color)

/** A cell's or a field's label. */
@Composable
private fun Label(text: String, color: Color = pal().muted) =
    Text(text.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.W700, letterSpacing = 0.55.sp, color = color)

@Composable
private fun H1(text: String, size: TextUnit = 42.sp) =
    Text(text, fontSize = size, fontWeight = FontWeight.W900, letterSpacing = (size.value * -0.035f).sp,
        lineHeight = size * 1.05f)

@Composable
private fun Value(text: String, color: Color = pal().ink) =
    Text(text, fontSize = 17.sp, fontWeight = FontWeight.W500, color = color)

@Composable
private fun Rule(thick: Boolean = false) =
    Box(Modifier.fillMaxWidth().height(if (thick) 3.dp else 1.dp).background(pal().ink))

@Composable
private fun Page(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxSize().background(pal().paper).systemBarsPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(start = 24.dp, end = 24.dp, bottom = 22.dp),
        verticalArrangement = Arrangement.spacedBy(22.dp), content = content,
    )
}

/**
 * The main screen (Ready, Recording, Paused) never scrolls: on a short screen the timer, the
 * waveform and the gaps get smaller and the blocks give up height (BlockAndList); only the
 * recordings list scrolls. Where that still leaves the block and the list less than their least
 * (a small phone with large text), the page turns tight: the gaps close up and the screen leaves
 * out what it can do without. The content gets the page's height, whether it is tight, and what
 * to call when it finds it is short of room. key: the screen (tight starts over for another one).
 */
@Composable
private fun FitPage(key: Any?, content: @Composable ColumnScope.(Dp, Boolean, () -> Unit) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxSize().background(pal().paper).systemBarsPadding().imePadding()) {
        val h = maxHeight
        var tight by remember(key, h, LocalDensity.current.fontScale) { mutableStateOf(false) }
        Column(Modifier.fillMaxSize().padding(start = 24.dp, end = 24.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(pageGap(h, tight))) {
            content(h, tight) { tight = true }
        }
    }
}

/** The main screen's gaps, as in the web app: 22dp, on short screens 2.2% of the height (at least 10dp); 8dp when tight. */
private fun pageGap(h: Dp, tight: Boolean): Dp = when {
    tight -> 8.dp
    h >= 760.dp -> 22.dp
    else -> (h * 0.022f).coerceAtLeast(10.dp)
}

/** The timer's height, as in the web app: 12% of the page's, from 56 to 104dp. */
private fun timerSize(h: Dp): Dp = (h * 0.12f).coerceIn(56.dp, 104.dp)

/** The waveform's height, as in the web app: 10% of the page's, from 56 to 88dp; 40dp when tight. */
private fun waveHeight(h: Dp, tight: Boolean): Dp = if (tight) 40.dp else (h * 0.1f).coerceIn(56.dp, 88.dp)

/**
 * The bottom of the main screen: the block (Record, or Pause and Stop) takes what is left between
 * minBlock and maxBlock, the notes under it what they need, the list the rest, scrolling on its own.
 * onShort: it got less than the least block, the gap and the least list (listMin) need.
 */
@Composable
private fun BlockAndList(modifier: Modifier, minBlock: Dp, maxBlock: Dp, gap: Dp, onShort: () -> Unit,
                         block: @Composable () -> Unit, notes: @Composable () -> Unit, list: @Composable () -> Unit,
                         listMin: Dp = 72.dp) {
    val least = with(LocalDensity.current) { (minBlock + gap + listMin).roundToPx() }
    Layout(contents = listOf(block, notes, list), modifier = modifier.onSizeChanged { if (it.height < least) onShort() }) { (b, n, l), c ->
        val width = c.maxWidth
        val gapPx = gap.roundToPx()
        val noteParts = n.map { it.measure(Constraints(maxWidth = width)) }
        val notesH = noteParts.sumOf { it.height }
        val room = (c.maxHeight - notesH - gapPx * (if (notesH > 0) 2 else 1)).coerceAtLeast(0)
        val blockH = (room - listMin.roundToPx()).coerceIn(minBlock.roundToPx(), maxBlock.roundToPx()).coerceAtMost(room)
        val listH = (room - blockH).coerceAtLeast(0)
        val blockParts = b.map { it.measure(Constraints.fixed(width, blockH)) }
        val listParts = l.map { it.measure(Constraints.fixed(width, listH)) }
        layout(width, c.maxHeight) {
            var y = 0
            blockParts.forEach { it.place(0, y) }
            y += blockH + gapPx
            noteParts.forEach { it.place(0, y); y += it.height }
            if (notesH > 0) y += gapPx
            listParts.forEach { it.place(0, y) }
        }
    }
}

/**
 * One line that always fits: the largest size from max down (1sp steps, not below min) at which it
 * fits the width and the height it gets, so a label never clips or breaks, whatever the phone's
 * width or font size. fitKey is what its width depends on: a running timer refits only when its
 * shape changes (tabular figures).
 */
@Composable
private fun FitText(text: String, max: TextUnit, color: Color, modifier: Modifier = Modifier, min: TextUnit = 12.sp,
                    tracking: Float = -0.025f, lineFactor: Float = 1f, tabular: Boolean = false, fitKey: String = text) {
    val measurer = rememberTextMeasurer()
    val base = LocalTextStyle.current
    BoxWithConstraints(modifier) {
        val c = constraints
        fun style(size: Float) = base.merge(TextStyle(fontSize = size.sp, fontWeight = FontWeight.W900,
            letterSpacing = (size * tracking).sp, lineHeight = (size * lineFactor).sp,
            // a line tighter than the font (the timer's) is as tall as it says, the figures in its middle
            lineHeightStyle = if (lineFactor < 1f) LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None) else null,
            fontFeatureSettings = if (tabular) "tnum" else null))
        val size = remember(fitKey, c.maxWidth, c.maxHeight, max, min, base) {
            var sz = max.value
            while (sz > min.value) {
                val m = measurer.measure(fitKey, style(sz), maxLines = 1, softWrap = false).size
                if (m.width <= c.maxWidth && (!c.hasBoundedHeight || m.height <= c.maxHeight)) break
                sz -= 1f
            }
            sz
        }
        Text(text, color = color, style = style(size), maxLines = 1, softWrap = false)
    }
}

/**
 * The top of every screen: the brand (after the red record dot) or Back on the left, Settings or
 * the box's state on the right.
 */
@Composable
private fun Bar(left: String, right: String?, onLeft: (() -> Unit)? = null, onRight: (() -> Unit)? = null,
                rightColor: Color = pal().ink, mark: Boolean = false) {
    Column {
        Row(Modifier.fillMaxWidth().height(40.dp)) {
            BarItem(left, onLeft, pal().ink, Modifier.weight(1f), mark)
            right?.let {
                Spacer(Modifier.width(12.dp))
                BarItem(it, onRight, rightColor)
            }
        }
        Spacer(Modifier.height(10.dp))
        Rule(thick = true)
    }
}

@Composable
private fun BarItem(text: String, onClick: (() -> Unit)?, color: Color, modifier: Modifier = Modifier, mark: Boolean = false) {
    Box(modifier.fillMaxHeight().let { if (onClick != null) it.clickable(onClick = onClick) else it },
        contentAlignment = Alignment.BottomStart) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            // the mark of all the recorder's faces (the PWA's and web view's .brand::before): 0.8em, 0.45em gap
            if (mark) Box(Modifier.padding(end = 5.dp).size(10.dp).clip(CircleShape).background(pal().red))
            Text(text.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.W700, letterSpacing = 0.5.sp, color = color,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** A section: small capitals over a 3dp rule; its rows follow with 1dp rules. */
@Composable
private fun Section(title: String) = Column {
    Text(title.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.W700, letterSpacing = 0.55.sp)
    Spacer(Modifier.height(8.dp))
    Rule(thick = true)
}

@Composable
private fun NumberedRow(n: Int, text: String, side: String? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("%02d".format(n), Modifier.width(34.dp).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W700)
        Text(text, Modifier.weight(1f).alignByBaseline(), fontSize = 15.sp, lineHeight = 21.sp)
        side?.let { Text(it, Modifier.alignByBaseline(), fontSize = 13.sp, color = pal().muted) }
    }
    Rule()
}

class CellSpec(val label: String, val content: @Composable () -> Unit)

/** Label over value, in a grid under a 1dp rule; compact (closer) on a short main screen, as in the web app. */
@Composable
private fun Cells(columns: Int, cells: List<CellSpec>, compact: Boolean = false) {
    Column {
        Rule()
        Spacer(Modifier.height(if (compact) 8.dp else 12.dp))
        cells.chunked(columns).forEachIndexed { i, row ->
            if (i > 0) Spacer(Modifier.height(if (compact) 8.dp else 14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                row.forEach { c ->
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Label(c.label)
                        c.content()
                    }
                }
                repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

/** A square switch: the knob moves right and the track fills with ink when it is on. */
@Composable
private fun SwitchBox(on: Boolean) {
    val p = pal()
    Box(Modifier.size(44.dp, 24.dp).background(if (on) p.ink else Color.Transparent).border(2.dp, p.ink).padding(3.dp),
        contentAlignment = if (on) Alignment.CenterEnd else Alignment.CenterStart) {
        Box(Modifier.size(18.dp).background(if (on) p.paper else p.ink))
    }
}

@Composable
private fun Toggle(on: Boolean, onChange: (Boolean) -> Unit, text: String?, description: String) {
    Row(Modifier.heightIn(min = 32.dp).toggleable(on, role = Role.Switch, onValueChange = onChange)
        .semantics { contentDescription = description },
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        SwitchBox(on)
        text?.let { Value(it) }
    }
}

/** A big flat button: a sign at the top, the word at the bottom (Record, Pause, Resume, Stop). */
@Composable
private fun Block(label: String, color: Color, pressedColor: Color, textColor: Color, fontSize: TextUnit,
                  modifier: Modifier = Modifier, onClick: () -> Unit, sign: @Composable () -> Unit) {
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    Column(modifier.background(if (pressed) pressedColor else color)
        .clickable(source, indication = null, role = Role.Button, onClick = onClick).padding(18.dp),
        verticalArrangement = Arrangement.SpaceBetween) {
        sign()
        FitText(label, fontSize, textColor, min = 14.sp)
    }
}

/** The one red action of a screen that is not a block: the word left, a white square right. */
@Composable
private fun WideButton(label: String, enabled: Boolean, onClick: () -> Unit) {
    val p = pal()
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    Row(Modifier.fillMaxWidth().height(72.dp).background(if (!enabled) p.ghost else if (pressed) p.redDark else p.red)
        .clickable(source, indication = null, enabled = enabled, role = Role.Button, onClick = onClick)
        .padding(horizontal = 18.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), color = p.onRed, fontSize = 26.sp, fontWeight = FontWeight.W900, letterSpacing = (-0.5).sp)
        Box(Modifier.size(18.dp).background(p.onRed))
    }
}

@Composable
private fun OutlineButton(label: String, onClick: () -> Unit) {
    val p = pal()
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    Box(Modifier.heightIn(min = 44.dp).background(if (pressed) p.ink else Color.Transparent).border(2.dp, p.ink)
        .clickable(source, indication = null, role = Role.Button, onClick = onClick)
        .padding(horizontal = 14.dp, vertical = 10.dp), contentAlignment = Alignment.CenterStart) {
        Text(label, color = if (pressed) p.paper else p.ink, fontSize = 15.sp, fontWeight = FontWeight.W700)
    }
}

@Composable
private fun LinkText(label: String, onClick: () -> Unit) =
    Text(label, Modifier.clickable(onClick = onClick).padding(vertical = 6.dp), fontSize = 14.sp,
        fontWeight = FontWeight.W700, textDecoration = TextDecoration.Underline)

@Composable
private fun PauseSign(color: Color) = Canvas(Modifier.size(26.dp)) {
    val u = size.width / 26f
    drawRect(color, Offset(3 * u, 2 * u), Size(7 * u, 22 * u))
    drawRect(color, Offset(16 * u, 2 * u), Size(7 * u, 22 * u))
}

@Composable
private fun PlaySign(color: Color) = Canvas(Modifier.size(26.dp)) {
    val u = size.width / 26f
    drawPath(Path().apply { moveTo(4 * u, 2 * u); lineTo(24 * u, 13 * u); lineTo(4 * u, 24 * u); close() }, color)
}

/**
 * The recorded time, big: as tall as the page allows (timerSize) and never wider than the screen.
 * Sized in dp, not sp: a picture of the time, it does not grow with the phone's font size.
 */
@Composable
private fun Timer(ms: Long, color: Color, max: Dp) {
    val text = timer(ms)
    FitText(text, with(LocalDensity.current) { max.toSp() }, color, Modifier.fillMaxWidth(), min = 24.sp,
        tracking = -0.05f, lineFactor = 0.92f, tabular = true, fitKey = text.replace(Regex("[0-9]"), "0"))
}

/**
 * The live waveform, the same as the PWA's: one bar per 100 ms (Waveform),
 * 3dp wide on a 5dp step, the newest at the right edge, mirrored around the
 * middle, at least 2dp tall; dim while paused.
 */
@Composable
private fun Wave(bars: List<Float>, paused: Boolean, height: Dp) {
    val color = if (paused) pal().dim else pal().ink
    val description = stringResource(R.string.input_level)
    Canvas(Modifier.fillMaxWidth().height(height).semantics { contentDescription = description }) {
        val step = 5.dp.toPx()
        val w = 3.dp.toPx()
        val min = 2.dp.toPx()
        val shown = bars.takeLast((size.width / step).toInt())
        shown.forEachIndexed { i, v ->
            if (v < 0f) return@forEachIndexed                      // the gap of a pause
            val h = maxOf(min, v * size.height)
            drawRect(color, Offset(size.width - (shown.size - i) * step + (step - w), (size.height - h) / 2), Size(w, h))
        }
    }
}

// ---- the box as the screens last saw it

/** "ok", "unreachable" or "rejected"; null before the first answer. */
private val boxState = MutableStateFlow<String?>(null)

/** The box's name ("aibox1") from its health answer; null until it has said. */
private val boxName = MutableStateFlow<String?>(null)

@Composable
private fun boxText(box: String?): Pair<String, Color> = when (box) {
    "ok" -> stringResource(R.string.box_ready) to pal().ink
    "rejected" -> stringResource(R.string.box_rejected_short) to pal().red
    "unreachable" -> stringResource(R.string.box_offline) to pal().red
    else -> "…" to pal().muted
}

/** "aibox1 · ready" for a top bar. */
@Composable
private fun boxLine(box: String?): Pair<String, Color> {
    val name by boxName.collectAsState()
    val (state, color) = boxText(box)
    return ((name ?: stringResource(R.string.box_label)) + " · " + state) to color
}

/** The box's name and its sealing key, from its health answer (both public). */
private suspend fun learnName(ctx: Context, api: BoxApi) {
    val health = withContext(Dispatchers.IO) { runCatching { api.health() } }.getOrNull() ?: return
    Pairing.saveSealKey(ctx, health)
    health.optString("box").takeIf { it.isNotBlank() }?.let {
        boxName.value = it
        Pairing.saveBoxName(ctx, it)
    }
}

// ---- screens

private sealed interface Screen {
    data object Home : Screen
    data class Detail(val meetingId: String) : Screen
    data object Settings : Screen
}

/** The open screen survives a rotation or a switch to dark mode (both recreate the activity). */
private val ScreenSaver = Saver<Screen, String>(
    save = { s -> when (s) { Screen.Home -> "home"; Screen.Settings -> "settings"; is Screen.Detail -> "detail:" + s.meetingId } },
    restore = { v -> when { v == "settings" -> Screen.Settings; v.startsWith("detail:") -> Screen.Detail(v.removePrefix("detail:")); else -> Screen.Home } },
)

@Composable
fun Root(link: MutableState<String?>, autoPair: Boolean) {
    val ctx = LocalContext.current
    var cfg by remember { mutableStateOf(Pairing.load(ctx)) }
    var screen by rememberSaveable(stateSaver = ScreenSaver) { mutableStateOf<Screen>(Screen.Home) }
    val pending = link.value
    val current = cfg
    if (boxName.value == null) boxName.value = Pairing.boxName(ctx)
    if (pending != null || current == null) {
        PairScreen(pending, autoPair, paired = current != null,
            onPaired = { cfg = it; link.value = null; screen = Screen.Home },
            onLeave = { link.value = null })
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
                onDisconnect = { Pairing.clear(ctx); boxState.value = null; boxName.value = null; cfg = null; screen = Screen.Home })
        }
        Screen.Home -> HomeScreen(current, onOpen = { screen = Screen.Detail(it) }, onSettings = { screen = Screen.Settings })
    }
}

/**
 * Connecting: a code typed here, or a link from the connect page (the code, the box's address and
 * its CA), is looked at first, so the person sees whose account the phone will record into; Connect
 * spends the code for the phone's own token. The older links with a token in them still work,
 * behind the same question.
 */
@Composable
private fun PairScreen(initial: String?, auto: Boolean, paired: Boolean, onPaired: (BoxConfig) -> Unit, onLeave: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var code by rememberSaveable { mutableStateOf("") }
    var offerJson by rememberSaveable { mutableStateOf<String?>(null) }    // what the code connects to, once looked at
    var offerCode by rememberSaveable { mutableStateOf("") }
    var tokenLink by rememberSaveable { mutableStateOf<String?>(null) }     // an older link, waiting for Connect
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val device = remember { deviceName(ctx) }
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    val msgUsed = stringResource(R.string.err_used)
    val msgMany = stringResource(R.string.err_many)
    val msgUnreachable = stringResource(R.string.err_unreachable)
    val msgOther = stringResource(R.string.err_other)
    val bad = stringResource(R.string.pair_bad)
    val rejected = stringResource(R.string.box_rejected)

    fun explain(e: Throwable): String = when ((e as? ConnectApi.Failure)?.kind) {
        ConnectApi.Kind.USED -> msgUsed
        ConnectApi.Kind.TOO_MANY -> msgMany
        ConnectApi.Kind.UNREACHABLE -> msgUnreachable
        else -> String.format(msgOther, e.message ?: "")
    }

    // What a code connects to: a typed one through the connect address, one from a link at the box itself.
    fun look(c: String, base: String, ca: ByteArray?) {
        busy = true
        error = null
        scope.launch {
            withContext(Dispatchers.IO) { runCatching { ConnectApi(base, ca).peek(c) } }
                .onSuccess { offerJson = it.toJson(); offerCode = c }
                .onFailure { error = explain(it) }
            busy = false
        }
    }

    suspend fun saved(cfg: BoxConfig, person: String?, box: String) {
        Pairing.save(ctx, cfg)
        Pairing.saveMe(ctx, JSONObject().put("device", cfg.name).put("person", person ?: JSONObject.NULL))
        if (box.isNotBlank()) {
            Pairing.saveBoxName(ctx, box)
            boxName.value = box
        }
        boxState.value = "ok"
        val api = BoxApi(cfg)
        withContext(Dispatchers.IO) { runCatching { api.settings() } }.getOrNull()?.let { rememberDefault(ctx, it) }
        learnName(ctx, api)                     // the sealing key too, so the first recording is sealed
        onPaired(cfg)
    }

    fun connect() {
        val offer = Offer.parse(offerJson) ?: return
        busy = true
        error = null
        scope.launch {
            withContext(Dispatchers.IO) { runCatching { ConnectApi(offer.boxUrl, offer.caDer).redeem(offerCode, device) } }
                .onSuccess { j -> offerJson = null; saved(j.cfg, j.person, j.box.ifBlank { offer.box }) }
                .onFailure {
                    error = explain(it)
                    if ((it as? ConnectApi.Failure)?.kind == ConnectApi.Kind.USED) offerJson = null
                }
            busy = false
        }
    }

    // An older link: the token is in it; only a box that knows the token is saved.
    fun connectToken(cfg: BoxConfig) {
        busy = true
        error = null
        scope.launch {
            val api = BoxApi(cfg)
            withContext(Dispatchers.IO) { runCatching { api.settings() } }
                .onSuccess { s ->
                    Pairing.save(ctx, cfg)
                    rememberDefault(ctx, s)
                    boxState.value = "ok"
                    withContext(Dispatchers.IO) { runCatching { api.me() } }.getOrNull()?.let { Pairing.saveMe(ctx, it) }
                    learnName(ctx, api)
                    tokenLink = null
                    onPaired(cfg)
                }
                .onFailure { error = if (it is BoxApi.HttpError && it.code == 401) rejected else msgUnreachable }
            busy = false
        }
    }

    LaunchedEffect(initial) {
        when (val l = initial?.let { PairLink.parse(it) }) {
            is PairLink.Code -> if (offerJson == null || offerCode != l.code) {
                code = l.code
                look(l.code, l.url, l.caDer)
            }
            is PairLink.Token -> if (auto) connectToken(l.cfg) else tokenLink = initial
            null -> if (initial != null) error = bad
        }
    }

    val offer = Offer.parse(offerJson)
    val older = tokenLink?.let { (PairLink.parse(it) as? PairLink.Token)?.cfg }
    if (offer != null || older != null) {
        ConfirmScreen(offer?.person, offer?.box ?: older!!.host, device, paired, busy, error,
            onConnect = { if (offer != null) connect() else older?.let { connectToken(it) } },
            onCancel = { offerJson = null; tokenLink = null; error = null; onLeave() })
        return
    }

    Page {
        Bar(stringResource(if (paired) R.string.back else R.string.brand), if (paired) null else stringResource(R.string.not_connected),
            onLeft = if (paired) onLeave else null, mark = !paired)
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(stringResource(R.string.pair_kicker), pal().red)
            H1(stringResource(R.string.pair_title))
        }
        Column {
            Rule(thick = true)
            listOf(R.string.pair_step1, R.string.pair_step2).forEachIndexed { i, step ->
                Row(Modifier.fillMaxWidth().padding(vertical = 14.dp)) {
                    Text("%02d".format(i + 1), Modifier.width(40.dp), fontWeight = FontWeight.W700)
                    Text(stringResource(step), Modifier.weight(1f), lineHeight = 23.sp)
                }
                Rule()
            }
        }
        val ready = !busy && code.length == ConnectCode.LENGTH
        // The keyboard goes away, so the answer under the field is in view.
        fun submit() {
            if (!ready) return
            keyboard?.hide()
            focus.clearFocus()
            look(code, ConnectApi.base, ConnectApi.baseCa)
        }
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Label(stringResource(R.string.code_label))
            CodeField(code, { code = ConnectCode.normalize(it) }) { submit() }
            error?.let { Text(it, Modifier.padding(top = 4.dp), color = pal().red, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.W700) }
        }
        WideButton(stringResource(R.string.pair_button), enabled = ready) { submit() }
        Text(stringResource(R.string.pair_note), fontSize = 13.sp, lineHeight = 19.sp, color = pal().muted)
    }
}

/** Before a code is spent: whose account, which box, which phone. */
@Composable
private fun ConfirmScreen(person: String?, box: String, device: String, paired: Boolean, busy: Boolean, error: String?,
                          onConnect: () -> Unit, onCancel: () -> Unit) {
    val p = pal()
    Page {
        Bar(stringResource(R.string.brand), if (paired) null else stringResource(R.string.not_connected), mark = true)
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(stringResource(R.string.confirm_kicker), p.red)
            H1(person?.let { stringResource(R.string.confirm_title_for, it) } ?: stringResource(R.string.confirm_title), 36.sp)
        }
        Cells(2, listOf(
            CellSpec(stringResource(R.string.box_label)) { Value(box) },
            CellSpec(stringResource(R.string.phone_label)) { Value(device) },
        ))
        Block(stringResource(R.string.pair_button), if (busy) p.ghost else p.red, p.redDark, p.onRed, 34.sp,
            Modifier.fillMaxWidth().height(150.dp), onClick = { if (!busy) onConnect() }) {
            Box(Modifier.size(26.dp).clip(CircleShape).background(p.onRed))
        }
        LinkText(stringResource(R.string.cancel), onCancel)
        error?.let { Text(it, color = p.red, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.W700) }
        Text(stringResource(R.string.pair_note), fontSize = 13.sp, lineHeight = 19.sp, color = p.muted)
    }
}

/** The connect code in a 2dp box, shown as XXXX-XXXX whatever was typed or pasted. */
@Composable
private fun CodeField(value: String, onChange: (String) -> Unit, onGo: () -> Unit) {
    val p = pal()
    val style = LocalTextStyle.current.copy(fontSize = 30.sp, lineHeight = 36.sp, fontWeight = FontWeight.W900,
        letterSpacing = 1.5.sp, color = p.ink, fontFeatureSettings = "tnum")
    BasicTextField(value, onChange, Modifier.fillMaxWidth().border(2.dp, p.ink).padding(horizontal = 14.dp, vertical = 12.dp),
        singleLine = true, cursorBrush = SolidColor(p.ink), textStyle = style, visualTransformation = CodeDash,
        // The password keyboard offers no word suggestions (one tapped would garble the code); the
        // field still shows what was typed, through its own transformation.
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters, autoCorrectEnabled = false,
            keyboardType = KeyboardType.Password, imeAction = ImeAction.Go),
        keyboardActions = KeyboardActions(onGo = { onGo() }),
        decorationBox = { inner ->
            Box {
                if (value.isEmpty()) Text(stringResource(R.string.code_hint), style = style.copy(color = p.ghost))
                inner()
            }
        })
}

/** Shows a dash after the fourth character; the code itself has none. */
private object CodeDash : VisualTransformation {
    override fun filter(text: AnnotatedString): TransformedText {
        val t = text.text
        if (t.length <= 4) return TransformedText(text, OffsetMapping.Identity)
        return TransformedText(AnnotatedString(t.take(4) + "-" + t.drop(4)), object : OffsetMapping {
            override fun originalToTransformed(offset: Int) = if (offset > 4) offset + 1 else offset
            override fun transformedToOriginal(offset: Int) = (if (offset > 4) offset - 1 else offset).coerceAtMost(t.length)
        })
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
    val p = pal()
    val store = remember { Store(ctx) }
    val api = remember(cfg) { BoxApi(cfg) }
    val prefs = remember { ctx.getSharedPreferences("ui", Context.MODE_PRIVATE) }
    val active by RecState.active.collectAsState()
    val paused by RecState.paused.collectAsState()
    val recordedMs by RecState.recordedMs.collectAsState()
    val wave by RecState.wave.collectAsState()
    val micMuted by RecState.muted.collectAsState()
    val recError by RecState.error.collectAsState()
    val box by boxState.collectAsState()
    var title by rememberSaveable { mutableStateOf("") }
    var drive by remember { mutableStateOf(prefs.getString("storage", "local") == "drive") }
    var touched by remember { mutableStateOf(false) }
    var me by remember { mutableStateOf(Pairing.me(ctx)) }
    var items by remember { mutableStateOf(store.list()) }
    var parts by remember { mutableIntStateOf(0) }
    val states = remember { mutableStateMapOf<String, String>() }
    var denied by remember { mutableStateOf(false) }

    // While the screen is visible: the phone's list every 2 s, the box every 15 s, and whom the phone
    // records for each time the app comes to the front (a name set in the Meetings app shows up then).
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(cfg) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var tick = 0
            while (true) {
                items = withContext(Dispatchers.IO) { store.list() }
                parts = RecState.active.value?.let { a -> withContext(Dispatchers.IO) { store.completeChunks(a.clientId).size } } ?: 0
                if (tick % 7 == 0 || boxState.value != "ok") {
                    boxState.value = withContext(Dispatchers.IO) { runCatching { api.settings() } }.fold(
                        onSuccess = { s ->
                            rememberDefault(ctx, s)
                            if (!touched) drive = prefs.getString("storage", "local") == "drive"
                            "ok"
                        },
                        onFailure = { if (it is BoxApi.HttpError && it.code == 401) "rejected" else "unreachable" })
                }
                if (boxState.value == "ok" && (tick == 0 || (me == null && tick % 15 == 0))) {
                    withContext(Dispatchers.IO) { runCatching { api.me() } }.getOrNull()?.let {
                        Pairing.saveMe(ctx, it)
                        me = Pairing.me(ctx)
                    }
                    learnName(ctx, api)
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

    val titleLabel = stringResource(R.string.title_label)
    val boxLabel = stringResource(R.string.box_label)
    val storageLabel = stringResource(R.string.storage_label)
    val driveName = stringResource(R.string.drive_name)
    val storeDrive = stringResource(R.string.store_drive)
    val name by boxName.collectAsState()
    val (boxWord, boxColor) = boxText(box)
    val boxValue = name?.let { "$it, " + boxWord.lowercase() } ?: boxWord
    // Whom the recordings go to: the person when the box knows a name, else this phone's name.
    val whom = me?.let { m -> m.person?.let { stringResource(R.string.for_label) to it } ?: (stringResource(R.string.phone_label) to m.device) }

    val list: @Composable () -> Unit = {
        // the only part of this screen that scrolls
        Column(Modifier.fillMaxSize()) {
            Section(stringResource(R.string.recordings))
            Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
                if (items.isEmpty()) Text(stringResource(R.string.none_yet), Modifier.padding(top = 12.dp), color = p.muted)
                val a = active
                items.forEachIndexed { i, r ->
                    val live = r.clientId == a?.clientId
                    RecordingRow(i + 1, r, r.meetingId?.let { states[it] }, live && paused, if (live) recordedMs else null) {
                        r.meetingId?.let(onOpen) ?: UploadWorker.enqueue(ctx, r.clientId, now = true)
                    }
                }
            }
        }
    }

    FitPage(active == null) { h, tight, onShort ->
        val short = h < 760.dp
        val notes: @Composable () -> Unit = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // on a short screen the Microphone cell says it ("Off while paused"), as in the web app
                if (active != null && paused && !short) Text(stringResource(R.string.paused_note), fontSize = 14.sp, lineHeight = 20.sp)
                recError?.let { Text(it, color = p.red, fontSize = 14.sp, fontWeight = FontWeight.W700) }
                if (denied) Text(stringResource(R.string.mic_denied), color = p.red, fontSize = 14.sp, fontWeight = FontWeight.W700)
            }
        }
        Bar(stringResource(R.string.brand), stringResource(R.string.settings), onRight = onSettings, mark = true)
        val a = active
        if (a == null) {
            if (tight) Kicker(stringResource(R.string.new_recording))          // the ghost timer gives way
            else Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Kicker(stringResource(R.string.new_recording))
                Timer(0, p.ghost, timerSize(h))
            }
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Label(titleLabel)
                LineField(title, { title = it }, stringResource(R.string.title_hint))
            }
            Cells(2, buildList {
                add(CellSpec(boxLabel) { Value(boxValue, boxColor) })
                add(CellSpec(storageLabel) { Toggle(drive, { drive = it; touched = true }, driveName, storeDrive) })
                whom?.let { (label, name) -> add(CellSpec(label) { Value(name) }) }
            }, compact = short)
            val recordLabel = stringResource(R.string.record)
            BlockAndList(Modifier.weight(1f).fillMaxWidth(), minBlock = 112.dp, maxBlock = 186.dp, gap = pageGap(h, tight),
                onShort = onShort, notes = notes, list = list, block = {
                Block(recordLabel, p.red, p.redDark, p.onRed, 40.sp, Modifier.fillMaxSize(), onClick = { record() }) {
                    Box(Modifier.size(30.dp).clip(CircleShape).background(p.onRed))
                }
            })
        } else {
            val r = items.firstOrNull { it.clientId == a.clientId }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (paused) Kicker(stringResource(R.string.kicker_paused), p.ink)
                else Kicker(stringResource(R.string.kicker_recording), p.red)
                Timer(recordedMs, if (paused) p.dim else p.ink, timerSize(h))
            }
            Wave(wave, paused, waveHeight(h, tight))
            val untitled = stringResource(R.string.untitled)
            val storageValue = if (r?.storage == "drive") driveName else stringResource(R.string.storage_box)
            val mic = when {
                paused -> stringResource(R.string.mic_paused) to p.ink
                micMuted -> stringResource(R.string.mic_taken) to p.red
                else -> stringResource(R.string.mic_on) to p.ink
            }
            val partsText = if (parts > 0) stringResource(R.string.parts, minOf(r?.uploaded ?: 0, parts), parts) else null
            Cells(2, listOf(
                CellSpec(titleLabel) { Value(r?.title?.ifBlank { null } ?: untitled) },
                CellSpec(boxLabel) { if (partsText != null) Value(partsText) else Value(boxValue, boxColor) },
                CellSpec(storageLabel) { Value(storageValue) },
                CellSpec(stringResource(R.string.mic_label)) { Value(mic.first, mic.second) },
            ), compact = short)
            BlockAndList(Modifier.weight(1f).fillMaxWidth(), minBlock = 100.dp, maxBlock = 163.dp, gap = pageGap(h, tight),
                onShort = onShort, notes = notes, list = list, block = {
                Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    val half = Modifier.weight(1f).fillMaxHeight()
                    if (paused) {
                        Block(stringResource(R.string.resume), p.ink, p.muted, p.paper, 30.sp, half,
                            onClick = { RecorderService.resume(ctx) }) { PlaySign(p.paper) }
                    } else {
                        Block(stringResource(R.string.pause), p.ink, p.muted, p.paper, 30.sp, half,
                            onClick = { RecorderService.pause(ctx) }) { PauseSign(p.paper) }
                    }
                    Block(stringResource(R.string.stop), p.red, p.redDark, p.onRed, 30.sp, half,
                        onClick = { RecorderService.stop(ctx) }) { Box(Modifier.size(26.dp).background(p.onRed)) }
                }
            })
        }
    }
}

/** A text field drawn as a line: the text over a 1dp rule, the hint in grey. */
@Composable
private fun LineField(value: String, onChange: (String) -> Unit, hint: String) {
    val p = pal()
    BasicTextField(value, onChange, Modifier.fillMaxWidth(), singleLine = true, cursorBrush = SolidColor(p.ink),
        textStyle = LocalTextStyle.current.copy(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.W500, color = p.ink),
        decorationBox = { inner ->
            Column {
                Box(Modifier.padding(top = 6.dp, bottom = 10.dp)) {
                    if (value.isEmpty()) Text(hint, fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.W500, color = p.muted)
                    inner()
                }
                Rule()
            }
        })
}

@Composable
private fun RecordingRow(n: Int, r: LocalRecording, meetingState: String?, paused: Boolean, liveMs: Long?, onClick: () -> Unit) {
    val p = pal()
    val (status, alert) = when {
        r.state == LocalRecording.RECORDING && paused -> stringResource(R.string.st_paused) to false
        r.state == LocalRecording.RECORDING -> stringResource(R.string.st_recording) to true
        r.state == LocalRecording.STOPPED && r.error == "rejected" -> stringResource(R.string.st_rejected) to true
        r.state == LocalRecording.STOPPED && r.uploaded < r.chunks && r.error != "unreachable" ->
            stringResource(R.string.st_uploading) to true
        r.state == LocalRecording.STOPPED -> stringResource(R.string.st_waiting) to false
        meetingState == "done" -> stringResource(R.string.st_done) to false
        meetingState == "failed" -> stringResource(R.string.st_failed) to true
        else -> stringResource(R.string.st_processing) to false
    }
    val length = liveMs ?: r.durationMs
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 11.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("%02d".format(n), Modifier.width(34.dp).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W700)
            Column(Modifier.weight(1f).alignByBaseline()) {
                Text(r.title.ifBlank { stringResource(R.string.untitled) }, fontSize = 15.sp, fontWeight = FontWeight.W500,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (r.interrupted) Text(stringResource(R.string.interrupted), fontSize = 12.sp, lineHeight = 16.sp, color = p.muted)
            }
            if (length > 0) Text(clock(length), Modifier.alignByBaseline(), fontSize = 15.sp, style = tnum())
            Text(status, Modifier.widthIn(min = 86.dp).alignByBaseline(), fontSize = 15.sp, textAlign = TextAlign.End,
                color = if (alert) p.red else p.ink, fontWeight = if (alert) FontWeight.W700 else FontWeight.W400, maxLines = 1)
        }
        Rule()
    }
}

@Composable
private fun DetailScreen(cfg: BoxConfig, mid: String, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val api = remember(cfg) { BoxApi(cfg) }
    val scope = rememberCoroutineScope()
    val box by boxState.collectAsState()
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

    val (boxValue, boxColor) = boxLine(box)
    Page {
        Bar(stringResource(R.string.back), boxValue, onLeft = onBack, rightColor = boxColor)
        val mm = m
        if (mm == null) {
            if (unreachable) Text(stringResource(R.string.box_unreachable), color = pal().muted)
            return@Page
        }
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(dateTime(mm.optString("started_at")))
            H1(mm.optString("title").ifBlank { mid }, 36.sp)
        }
        val speakers = mm.optInt("speakers")
        val storeDrive = stringResource(R.string.store_drive)
        Cells(3, listOf(
            CellSpec(stringResource(R.string.length_label)) {
                Text(clock((mm.optDouble("duration_s", 0.0) * 1000).toLong()), fontSize = 17.sp, fontWeight = FontWeight.W500, style = tnum())
            },
            CellSpec(stringResource(R.string.speakers_label)) { Value(if (speakers > 0) "$speakers" else "…") },
            CellSpec(stringResource(R.string.drive_short)) {
                Toggle(drive, { v ->
                    drive = v
                    scope.launch(Dispatchers.IO) { runCatching { api.setStorage(mid, if (v) "drive" else "local") } }
                }, null, storeDrive)
            },
        ))
        val folder = mm.optString("drive_url").takeIf { it.startsWith("https://") }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(stringResource(when {
                !drive -> R.string.note_local
                folder != null -> R.string.note_done
                else -> R.string.note_waiting
            }), fontSize = 14.sp)
            if (drive && folder != null) LinkText(stringResource(R.string.open_folder)) { open(ctx, folder) }
        }
        if (mm.optString("state") != "done") Text(stringResource(R.string.not_ready), color = pal().muted)
        else Summary(mm.optString("summary_md"))
        mm.optString("url").takeIf { it.startsWith("https://") }?.let { url ->
            LinkText(stringResource(R.string.open_box)) { open(ctx, url) }
        }
    }
}

@Composable
private fun Summary(md: String) {
    Column {
        for (part in summaryParts(md)) {
            when (part) {
                is SummaryPart.Lead -> {
                    Text(part.text, fontSize = 18.sp, fontWeight = FontWeight.W500, lineHeight = 26.sp)
                    Spacer(Modifier.height(22.dp))
                }
                is SummaryPart.Heading -> {
                    Spacer(Modifier.height(12.dp))
                    Section(part.text)
                }
                is SummaryPart.Item -> NumberedRow(part.n, part.text, part.side)
                is SummaryPart.Para -> Text(part.text, Modifier.padding(vertical = 6.dp), fontSize = 15.sp, lineHeight = 22.sp)
            }
        }
    }
}

@Composable
private fun SettingsScreen(cfg: BoxConfig, onBack: () -> Unit, onDisconnect: () -> Unit) {
    val ctx = LocalContext.current
    val box by boxState.collectAsState()
    val me = remember { Pairing.me(ctx) }
    val pm = remember { ctx.getSystemService(PowerManager::class.java) }
    var ignoring by remember { mutableStateOf(pm.isIgnoringBatteryOptimizations(ctx.packageName)) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(Unit) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {      // back from the system dialog
            ignoring = pm.isIgnoringBatteryOptimizations(ctx.packageName)
        }
    }
    val (boxValue, boxColor) = boxLine(box)
    Page {
        Bar(stringResource(R.string.back), boxValue, onLeft = onBack, rightColor = boxColor)
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(stringResource(R.string.settings_kicker))
            H1(stringResource(R.string.settings))
        }
        val phone = me?.device?.takeIf { it.isNotBlank() } ?: cfg.name.ifBlank { null }
        Cells(2, buildList {
            me?.person?.let { person -> add(CellSpec(stringResource(R.string.recording_for)) { Value(person) }) }
            phone?.let { add(CellSpec(stringResource(R.string.phone_label)) { Value(it) }) }
            add(CellSpec(stringResource(R.string.box_label)) { Value(listOfNotNull(boxName.value, cfg.host).joinToString(" · ")) })
        })
        Column {
            Section(stringResource(R.string.battery_section))
            Spacer(Modifier.height(12.dp))
            if (ignoring) Text(stringResource(R.string.battery_ok), fontSize = 15.sp, lineHeight = 22.sp)
            else {
                Text(stringResource(R.string.battery_text), fontSize = 15.sp, lineHeight = 22.sp)
                Spacer(Modifier.height(12.dp))
                OutlineButton(stringResource(R.string.battery)) {
                    ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + ctx.packageName)))
                }
            }
        }
        OutlineButton(stringResource(R.string.disconnect), onDisconnect)
        Text(stringResource(R.string.version, BuildConfig.VERSION_NAME), fontSize = 12.sp, color = pal().muted)
    }
}

// ---- small helpers

private fun open(ctx: Context, url: String) =
    runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }

/** "18 Nov 2026 · 09:00" in the phone's language. */
private fun dateTime(iso: String): String = runCatching {
    val t = Instant.parse(iso).atZone(ZoneId.systemDefault())
    DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).format(t) + " · " + DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).format(t)
}.getOrDefault(iso)
