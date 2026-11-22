package com.brainbot.indierecorder

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaPlayer
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
import androidx.compose.runtime.DisposableEffect
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
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.layout.FirstBaseline
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
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
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import java.io.File
import java.time.LocalDate
import java.time.ZoneId
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
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
private fun Kicker(text: String, color: Color = pal().muted, modifier: Modifier = Modifier) =
    Text(text.uppercase(), modifier, fontSize = 12.sp, fontWeight = FontWeight.W700, letterSpacing = 0.5.sp, color = color)

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
 * The main screen (Ready, Recording, Paused) never scrolls. Its parts stand one under the other
 * with the page's gaps (Close: 4dp, a kicker over its timer), the last one, the Recordings button,
 * at the bottom. The parts that grow (Grow: the block, the timer, the waveform) get their full size
 * where the page has room for it and give way on a short screen (growHeights). Where not even their
 * least fits (a small phone with large text), the page turns tight: the gaps close up and the screen
 * leaves out what it can do without. The content gets the page's height and whether it is tight.
 * key: the screen (tight starts over for another one).
 */
@Composable
private fun FitPage(key: Any?, content: @Composable (Dp, Boolean) -> Unit) {
    BoxWithConstraints(Modifier.fillMaxSize().background(pal().paper).systemBarsPadding().imePadding()) {
        val h = maxHeight
        var tight by remember(key, h, LocalDensity.current.fontScale) { mutableStateOf(false) }
        val gap = pageGap(h, tight)
        Layout({ content(h, tight) }, Modifier.fillMaxSize().padding(start = 24.dp, end = 24.dp, bottom = 16.dp)) { parts, c ->
            val grow = parts.map { it.layoutId as? Grow }
            val loose = Constraints(maxWidth = c.maxWidth, maxHeight = c.maxHeight)
            val fixed = parts.mapIndexed { i, part -> if (grow[i] == null) part.measure(loose) else null }
            val after = parts.map { (if (it.layoutId == Close) 4.dp else gap).roundToPx() }
            val least = grow.map { it?.least?.roundToPx() ?: 0 }
            val room = c.maxHeight - fixed.sumOf { it?.height ?: 0 } - after.dropLast(1).sum()
            if (room < least.sum()) tight = true
            val heights = growHeights(room, least, grow.map { it?.full?.roundToPx() ?: 0 }, grow.map { it?.first == true })
            val placed = parts.mapIndexed { i, part -> fixed[i] ?: part.measure(Constraints.fixed(c.maxWidth, heights[i])) }
            layout(c.maxWidth, c.maxHeight) {
                var y = 0
                placed.forEachIndexed { i, p ->
                    if (i == placed.lastIndex) y = maxOf(y, c.maxHeight - p.height)      // what the parts leave free stays above it
                    p.place(0, y)
                    y += p.height + after[i]
                }
            }
        }
    }
}

/** A part of the main screen that grows with the page from least to full; first: the room goes to it before the others. */
private class Grow(val least: Dp, val full: Dp, val first: Boolean = false)

/** A part that stays close to the part under it: a kicker over its timer. */
private object Close

/** The main screen's gaps, as in the web app: 22dp, on short screens 2.2% of the height (at least 10dp); 8dp when tight. */
private fun pageGap(h: Dp, tight: Boolean): Dp = when {
    tight -> 8.dp
    h >= 760.dp -> 22.dp
    else -> (h * 0.022f).coerceAtLeast(10.dp)
}

/** The timer's least, as in the web app: 12% of the page's height, from 56 to 104dp. */
private fun timerSize(h: Dp): Dp = (h * 0.12f).coerceIn(56.dp, 104.dp)

/** The waveform's least, as in the web app: 10% of the page's height, from 56 to 88dp. */
private fun waveHeight(h: Dp): Dp = (h * 0.1f).coerceIn(56.dp, 88.dp)

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
private fun NumberedRow(n: Int, text: AnnotatedString, side: String? = null) {
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

/** A row's red action, as the web apps' .btn.go: the word, then a white square. */
@Composable
private fun RedButton(label: String, onClick: () -> Unit) {
    val p = pal()
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    Row(Modifier.heightIn(min = 44.dp).background(if (pressed) p.redDark else p.red)
        .clickable(source, indication = null, role = Role.Button, onClick = onClick).padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, color = p.onRed, fontSize = 15.sp, fontWeight = FontWeight.W700)
        Box(Modifier.size(12.dp).background(p.onRed))
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

/**
 * The way from the main screen to the meetings, an outlined button as in the web app: the word on
 * the left, how many there are and an arrow on the right, and before them in red what is under way
 * ("1 uploading"). Where one line is too narrow for all of it (large text), the red note goes under
 * the word, so nothing is cut off.
 */
@Composable
private fun MeetingsButton(count: Int, busy: String?, height: Dp, onClick: () -> Unit) {
    val p = pal()
    val source = remember { MutableInteractionSource() }
    val pressed by source.collectIsPressedAsState()
    val ink = if (pressed) p.paper else p.ink
    val word = stringResource(R.string.meetings)
    Layout(contents = listOf(
        { Text(word, color = ink, fontSize = 15.sp, lineHeight = 20.sp, fontWeight = FontWeight.W700, maxLines = 1, softWrap = false) },
        { busy?.let { Text(it, color = p.red, fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.W700, maxLines = 1, softWrap = false) } },
        { Text("$count", color = ink, fontSize = 15.sp, lineHeight = 20.sp, fontWeight = FontWeight.W700, style = tnum()) },
        { Chevron(ink) },
    ), Modifier.fillMaxWidth().heightIn(min = height).background(if (pressed) p.ink else Color.Transparent).border(2.dp, p.ink)
        .clickable(source, indication = null, role = Role.Button, onClick = onClick).padding(horizontal = 16.dp, vertical = 8.dp)
    ) { (w, b, n, a), c ->
        val loose = Constraints(maxWidth = c.maxWidth, maxHeight = c.maxHeight)
        val label = w.first().measure(loose)
        val note = b.firstOrNull()?.measure(loose)
        val num = n.first().measure(loose)
        val arrow = a.first().measure(loose)
        val gap = 14.dp.roundToPx()
        val wide = 18.dp.roundToPx()
        val end = num.width + wide + arrow.width
        val below = note != null && label.width + wide + note.width + gap + end > c.maxWidth
        val left = label.height + if (below) note!!.height else 0
        val height = maxOf(c.minHeight, left, num.height, arrow.height)
        layout(c.maxWidth, height) {
            label.place(0, (height - left) / 2)
            num.place(c.maxWidth - end, (height - num.height) / 2)
            arrow.place(c.maxWidth - arrow.width, (height - arrow.height) / 2)
            note?.let {
                if (below) it.place(0, (height - left) / 2 + label.height)
                else it.place(c.maxWidth - end - gap - it.width, (height - num.height) / 2 + num[FirstBaseline] - it[FirstBaseline])
            }
        }
    }
}

/** The arrow of a button that leads to another screen, as in the web app: a square's top and right side, turned to point right. */
@Composable
private fun Chevron(color: Color) = Canvas(Modifier.size(16.dp)) {
    val s = 10.dp.toPx()
    val w = 3.dp.toPx()
    translate(left = -2.5.dp.toPx()) {          // the point stands right of the square's middle
        rotate(45f) {
            val o = Offset((size.width - s) / 2, (size.height - s) / 2)
            drawRect(color, o, Size(s, w))
            drawRect(color, Offset(o.x + s - w, o.y), Size(w, s))
        }
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
 * The recorded time, big: a part of the main screen that grows (Grow), its figures from least to
 * 104dp as the page allows, never wider than the screen. Sized in dp, not sp: a picture of the time,
 * it does not grow with the phone's font size.
 */
@Composable
private fun Timer(ms: Long, color: Color, least: Dp) {
    val text = timer(ms)
    // its line is 0.92 of the figures' size
    FitText(text, with(LocalDensity.current) { 104.dp.toSp() }, color, Modifier.layoutId(Grow(least * 0.92f, 104.dp * 0.92f)).fillMaxWidth(),
        min = 24.sp, tracking = -0.05f, lineFactor = 0.92f, tabular = true, fitKey = text.replace(Regex("[0-9]"), "0"))
}

/**
 * The live waveform, the same as the PWA's: one bar per 100 ms (Waveform),
 * 3dp wide on a 5dp step, the newest at the right edge, mirrored around the
 * middle, at least 2dp tall; dim while paused. Its height comes with the modifier.
 */
@Composable
private fun Wave(bars: List<Float>, paused: Boolean, modifier: Modifier) {
    val color = if (paused) pal().dim else pal().ink
    val description = stringResource(R.string.input_level)
    Canvas(modifier.fillMaxWidth().semantics { contentDescription = description }) {
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

/** The person's meetings as the box last listed them (list_meetings), from any of their phones; null before the first answer. */
private val meetingList = MutableStateFlow<List<MeetingRow>?>(null)

/** Why the list could not be had: "unreachable", "rejected" (the box no longer takes the phone), "nobody" (the phone is no one's). */
private val listProblem = MutableStateFlow<String?>(null)

/** Asks the box for the person's meetings: all of them, whichever phone recorded them. */
private suspend fun loadMeetings(api: BoxApi) {
    withContext(Dispatchers.IO) { runCatching { MeetingRow.list(api.op("list_meetings") as JSONArray) } }
        .onSuccess { meetingList.value = it; listProblem.value = null }
        .onFailure { listProblem.value = problemOf(it) }
}

/** What went wrong with an op, as the screens say it. */
private fun problemOf(e: Throwable): String = when ((e as? BoxApi.OpError)?.code) {
    401 -> "rejected"
    403 -> "nobody"
    400 -> "refused"
    else -> "unreachable"
}

@Composable
private fun problemText(problem: String): String = stringResource(when (problem) {
    "nobody" -> R.string.list_nobody
    "rejected" -> R.string.box_rejected
    "refused" -> R.string.meeting_gone
    else -> R.string.list_unreachable
})

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
    data object Meetings : Screen
    data class Meeting(val meetingId: String) : Screen
    data object Settings : Screen
}

/** The open screen survives a rotation or a switch to dark mode (both recreate the activity). */
private val ScreenSaver = Saver<Screen, String>(
    save = { s ->
        when (s) {
            Screen.Home -> "home"; Screen.Meetings -> "meetings"; Screen.Settings -> "settings"
            is Screen.Meeting -> "meeting:" + s.meetingId
        }
    },
    restore = { v ->
        when {
            v == "meetings" -> Screen.Meetings
            v == "settings" -> Screen.Settings
            v.startsWith("meeting:") -> Screen.Meeting(v.removePrefix("meeting:"))
            else -> Screen.Home
        }
    },
)

@Composable
fun Root(link: MutableState<String?>, autoPair: Boolean) {
    val ctx = LocalContext.current
    var cfg by remember { mutableStateOf(Pairing.load(ctx)) }
    var screen by rememberSaveable(stateSaver = ScreenSaver) { mutableStateOf<Screen>(Screen.Home) }
    // what the main screen and the list keep while another screen is open: a typed title, how far the list is scrolled
    val kept = rememberSaveableStateHolder()
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
        is Screen.Meeting -> {
            BackHandler { screen = Screen.Meetings }
            MeetingScreen(current, s.meetingId, onBack = { screen = Screen.Meetings })
        }
        Screen.Meetings -> {
            BackHandler { screen = Screen.Home }
            kept.SaveableStateProvider("meetings") {
                MeetingsScreen(current, onOpen = { screen = Screen.Meeting(it) }, onBack = { screen = Screen.Home })
            }
        }
        Screen.Settings -> {
            BackHandler { screen = Screen.Home }
            SettingsScreen(current, onBack = { screen = Screen.Home },
                onDisconnect = {
                    Pairing.clear(ctx); boxState.value = null; boxName.value = null
                    meetingList.value = null; listProblem.value = null; cfg = null; screen = Screen.Home
                })
        }
        Screen.Home -> kept.SaveableStateProvider("home") {
            HomeScreen(current, onMeetings = { screen = Screen.Meetings }, onSettings = { screen = Screen.Settings })
        }
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
private fun HomeScreen(cfg: BoxConfig, onMeetings: () -> Unit, onSettings: () -> Unit) {
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
    var denied by remember { mutableStateOf(false) }

    // While the screen is visible: the phone's list every 2 s, the box and the person's meetings every
    // 14 s, and whom the phone records for each time the app comes to the front (a name set in the
    // indie-recorder app shows up then).
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
                if (boxState.value == "ok" && tick % 7 == 0) loadMeetings(api)
                tick++
                delay(2000)
            }
        }
    }
    val listed by meetingList.collectAsState()
    LaunchedEffect(listed) { listed?.let { prefs.edit().putInt("meetings", it.size).apply() } }

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

    // On the Meetings button: how many the person has on the box (from any of their phones), and in
    // red what is under way: what the box does not have all of yet (the one being recorded too, as in
    // the web app), else the meetings it works on.
    val count = listed?.size ?: prefs.getInt("meetings", 0)
    val uploading = items.count { it.state != LocalRecording.FINALIZED }
    val working = listed?.count { it.state != "done" && it.state != "failed" } ?: 0
    val busy = when {
        uploading > 0 -> pluralStringResource(R.plurals.n_uploading, uploading, uploading)
        working > 0 -> pluralStringResource(R.plurals.n_processing, working, working)
        else -> null
    }

    FitPage(active == null) { h, tight ->
        val short = h < 760.dp
        Bar(stringResource(R.string.brand), stringResource(R.string.settings), onRight = onSettings, mark = true)
        val a = active
        if (a == null) {
            if (tight) Kicker(stringResource(R.string.new_recording))          // the ghost timer gives way
            else {
                Kicker(stringResource(R.string.new_recording), modifier = Modifier.layoutId(Close))
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
            Block(stringResource(R.string.record), p.red, p.redDark, p.onRed, 40.sp,
                Modifier.layoutId(Grow(112.dp, 186.dp, first = true)).fillMaxWidth(), onClick = { record() }) {
                Box(Modifier.size(30.dp).clip(CircleShape).background(p.onRed))
            }
        } else {
            val r = items.firstOrNull { it.clientId == a.clientId }
            if (paused) Kicker(stringResource(R.string.kicker_paused), p.ink, Modifier.layoutId(Close))
            else Kicker(stringResource(R.string.kicker_recording), p.red, Modifier.layoutId(Close))
            Timer(recordedMs, if (paused) p.dim else p.ink, timerSize(h))
            Wave(wave, paused, if (tight) Modifier.height(40.dp) else Modifier.layoutId(Grow(waveHeight(h), 88.dp)))
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
            Row(Modifier.layoutId(Grow(100.dp, 163.dp, first = true)).fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
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
        }
        // on a short screen the Microphone cell says it ("Off while paused"), as in the web app
        val pausedNote = a != null && paused && !short
        if (pausedNote || recError != null || denied) Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (pausedNote) Text(stringResource(R.string.paused_note), fontSize = 14.sp, lineHeight = 20.sp)
            recError?.let { Text(it, color = p.red, fontSize = 14.sp, fontWeight = FontWeight.W700) }
            if (denied) Text(stringResource(R.string.mic_denied), color = p.red, fontSize = 14.sp, fontWeight = FontWeight.W700)
        }
        MeetingsButton(count, busy, if (short) 56.dp else 64.dp, onMeetings)
    }
}

/**
 * The person's meetings, as in the other apps (recorder/static/meetings.js): what this phone has not
 * handed to the box yet first, under "On this phone"; then every meeting on the box, whichever phone
 * recorded it, by the phone's own day, newest first. A meeting opens its screen; a recording the box
 * does not have yet is sent again. This screen scrolls like the others.
 */
@Composable
private fun MeetingsScreen(cfg: BoxConfig, onOpen: (String) -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val store = remember { Store(ctx) }
    val api = remember(cfg) { BoxApi(cfg) }
    val active by RecState.active.collectAsState()
    val paused by RecState.paused.collectAsState()
    val recordedMs by RecState.recordedMs.collectAsState()
    val box by boxState.collectAsState()
    val listed by meetingList.collectAsState()
    val problem by listProblem.collectAsState()
    var items by remember { mutableStateOf(store.list()) }

    // While the screen is visible: the phone's own every 2 s, the box's list on opening and every 10 s.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(cfg) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            var tick = 0
            while (true) {
                items = withContext(Dispatchers.IO) { store.list() }
                if (tick % 5 == 0) loadMeetings(api)
                tick++
                delay(2000)
            }
        }
    }

    val (boxValue, boxColor) = boxLine(box)
    val locale = ctx.resources.configuration.locales[0]
    val zone = remember { ZoneId.systemDefault() }
    val wide = LocalConfiguration.current.screenWidthDp >= 560      // room for the time, as in the web apps
    val today = LocalDate.now(zone)
    val todayWord = stringResource(R.string.today)
    val yesterdayWord = stringResource(R.string.yesterday)
    Page {
        Bar(stringResource(R.string.back), boxValue, onLeft = onBack, rightColor = boxColor)
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(stringResource(R.string.only_you))
            H1(stringResource(R.string.meetings))
        }
        val unsent = items.filter { it.state != LocalRecording.FINALIZED }
        if (unsent.isNotEmpty()) Column {
            Section(stringResource(R.string.on_this_phone))
            val a = active
            unsent.forEachIndexed { i, r ->
                val live = r.clientId == a?.clientId
                RecordingRow(i + 1, r, live && paused, if (live) recordedMs else null) {
                    UploadWorker.enqueue(ctx, r.clientId, now = true)
                }
            }
        }
        problem?.let { Text(problemText(it), fontSize = 14.sp, lineHeight = 20.sp, color = pal().red, fontWeight = FontWeight.W700) }
        val rows = listed
        if (rows != null && rows.isEmpty() && unsent.isEmpty()) Column {
            Section(stringResource(R.string.none_head))
            Text(stringResource(R.string.none_text), Modifier.padding(top = 12.dp), fontSize = 15.sp, lineHeight = 22.sp, color = pal().muted)
        }
        rows?.let { meetingDays(it, zone) }?.forEach { (day, dayRows) ->
            Column {
                Section(dayLabel(day, today, locale, todayWord, yesterdayWord))
                dayRows.forEachIndexed { i, m -> MeetingListRow(i + 1, m, zone, locale, wide) { onOpen(m.id) } }
            }
        }
    }
}

/** A meeting in the list, as the web apps' rows: number, title, (time,) length, and where it is or how far. */
@Composable
private fun MeetingListRow(n: Int, m: MeetingRow, zone: ZoneId, locale: Locale, wide: Boolean, onClick: () -> Unit) {
    val p = pal()
    val st = rowState(m.state, m.storage)
    val word = stringResource(when (st) {
        RowState.UPLOADING -> R.string.st_uploading
        RowState.FAILED -> R.string.st_failed
        RowState.PROCESSING -> R.string.st_processing
        RowState.DRIVE -> R.string.drive_short
        RowState.LOCAL -> R.string.st_local
    })
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 11.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("%02d".format(n), Modifier.width(34.dp).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W700, style = tnum())
            Text(m.title, Modifier.weight(1f).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W500,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (wide) m.startedAt?.let { Text(meetingTime(it, zone, locale), Modifier.alignByBaseline(), fontSize = 15.sp, style = tnum()) }
            Text(meetingLength(m.durationS), Modifier.alignByBaseline(), fontSize = 15.sp, style = tnum())
            Text(word, Modifier.widthIn(min = 70.dp).alignByBaseline(), fontSize = 14.sp, textAlign = TextAlign.End,
                color = if (st.busy) p.red else p.ink, fontWeight = if (st.busy) FontWeight.W700 else FontWeight.W400, maxLines = 1)
        }
        Rule()
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

/** A recording of this phone that the box does not have all of yet: recording, paused, uploading or waiting. */
@Composable
private fun RecordingRow(n: Int, r: LocalRecording, paused: Boolean, liveMs: Long?, onClick: () -> Unit) {
    val p = pal()
    val (status, alert) = when {
        r.state == LocalRecording.RECORDING && paused -> stringResource(R.string.st_paused) to false
        r.state == LocalRecording.RECORDING -> stringResource(R.string.st_recording) to true
        r.state == LocalRecording.STOPPED && r.error == "rejected" -> stringResource(R.string.st_rejected) to true
        r.state == LocalRecording.STOPPED && r.uploaded < r.chunks && r.error != "unreachable" ->
            stringResource(R.string.st_uploading) to true
        r.state == LocalRecording.STOPPED -> stringResource(R.string.st_waiting) to false
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
            if (length > 0) Text(meetingLength(length / 1000.0), Modifier.alignByBaseline(), fontSize = 15.sp, style = tnum())
            Text(status, Modifier.widthIn(min = 86.dp).alignByBaseline(), fontSize = 15.sp, textAlign = TextAlign.End,
                color = if (alert) p.red else p.ink, fontWeight = if (alert) FontWeight.W700 else FontWeight.W400, maxLines = 1)
        }
        Rule()
    }
}

/** The names typed into a meeting's speaker cards survive a rotation. */
private val NamesSaver = Saver<SnapshotStateMap<String, String>, String>(
    save = { JSONObject(it.toMap()).toString() },
    restore = { v -> mutableStateMapOf<String, String>().apply { val o = JSONObject(v); o.keys().forEach { put(it, o.getString(it)) } } },
)

/**
 * One meeting, as in the other apps (recorder/static/meetings.js): when and what, its length, its
 * speakers and whether it is kept in Drive; the summary; then folds for the speakers' names (their
 * share of the talk, their lines, their voice, suggested names), the transcript (fetched when opened)
 * and more. While the box works on it, the screen looks again every 5 s.
 */
@Composable
private fun MeetingScreen(cfg: BoxConfig, mid: String, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val p = pal()
    val api = remember(cfg) { BoxApi(cfg) }
    val scope = rememberCoroutineScope()
    val box by boxState.collectAsState()
    val name by boxName.collectAsState()
    var m by remember(mid) { mutableStateOf<JSONObject?>(null) }
    var problem by remember(mid) { mutableStateOf<String?>(null) }
    var note by remember(mid) { mutableStateOf<String?>(null) }       // "Saved.", "A new summary is on its way."
    var drive by remember(mid) { mutableStateOf(false) }
    var spkOpen by rememberSaveable(mid) { mutableStateOf(false) }
    var trOpen by rememberSaveable(mid) { mutableStateOf(false) }
    var moreOpen by rememberSaveable(mid) { mutableStateOf(false) }
    var transcript by remember(mid) { mutableStateOf<List<TranscriptPart>?>(null) }
    val names = rememberSaveable(mid, saver = NamesSaver) { mutableStateMapOf() }
    var playing by remember { mutableStateOf<String?>(null) }
    val player = remember { MediaPlayer() }
    DisposableEffect(Unit) { onDispose { runCatching { player.release() } } }

    suspend fun load() {
        withContext(Dispatchers.IO) { runCatching { api.obj("get_meeting", JSONObject().put("meeting_id", mid).put("include_transcript", false)) } }
            .onSuccess { j ->
                m = j
                drive = j.optString("storage") == "drive"
                problem = null
                speakerCards(j).forEach { c -> if (c.label !in names) names[c.label] = c.name }
            }
            .onFailure { problem = problemOf(it) }
    }

    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(mid) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                load()
                val state = m?.optString("state")
                if (state == "done" || state == "failed") break
                delay(5000)
            }
        }
    }
    // the transcript when its fold is open, again after names changed
    LaunchedEffect(trOpen, transcript == null) {
        if (!trOpen || transcript != null) return@LaunchedEffect
        withContext(Dispatchers.IO) { runCatching { api.obj("get_meeting", JSONObject().put("meeting_id", mid).put("include_transcript", true)) } }
            .onSuccess { transcript = transcriptParts(text(it, "transcript_md") ?: "") }
            .onFailure { problem = problemOf(it) }
    }

    val saved = stringResource(R.string.saved)
    val queued = stringResource(R.string.queued)
    val unreachable = stringResource(R.string.list_unreachable)
    fun setDrive(v: Boolean) {
        drive = v
        scope.launch {
            withContext(Dispatchers.IO) { runCatching { api.op("set_storage", JSONObject().put("meeting_id", mid).put("storage", if (v) "drive" else "local")) } }
                .onSuccess { load(); loadMeetings(api) }
                .onFailure { drive = !v; note = it.message }
        }
    }
    fun saveNames() {
        val o = JSONObject()
        names.forEach { (k, v) -> o.put(k, v) }
        scope.launch {
            withContext(Dispatchers.IO) { runCatching { api.op("rename_speakers", JSONObject().put("meeting_id", mid).put("names", o)) } }
                .onSuccess { load(); transcript = null; note = saved }
                .onFailure { note = it.message }
        }
    }
    fun newSummary() = scope.launch {
        withContext(Dispatchers.IO) { runCatching { api.op("new_summary", JSONObject().put("meeting_id", mid)) } }
            .onSuccess { note = queued }
            .onFailure { note = it.message }
    }
    // Seven seconds of a speaker's voice, from the box itself (the link names brain-tls), played from the cache.
    fun play(c: SpeakerCard) {
        runCatching { player.reset() }
        if (playing == c.label) { playing = null; return }
        playing = null
        val path = clipPath(c.clipUrl) ?: return
        scope.launch {
            val f = withContext(Dispatchers.IO) { runCatching { File(ctx.cacheDir, "clip.wav").apply { writeBytes(api.bytes(path)) } } }.getOrNull()
            if (f == null) { note = unreachable; return@launch }
            runCatching {
                player.setDataSource(f.path)
                player.setOnCompletionListener { playing = null }
                player.prepare()
                player.start()
                playing = c.label
            }
        }
    }

    val (boxValue, boxColor) = boxLine(box)
    val locale = ctx.resources.configuration.locales[0]
    val zone = remember { ZoneId.systemDefault() }
    val where = name ?: stringResource(R.string.box_label)
    Page {
        Bar(stringResource(R.string.back), boxValue, onLeft = onBack, rightColor = boxColor)
        val mm = m
        if (mm == null) {
            problem?.let { Text(problemText(it), color = p.muted) }
            return@Page
        }
        val started = instant(text(mm, "started_at")) ?: instant(text(mm, "created_at"))
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Kicker(started?.let { meetingDate(it, zone, locale) + " · " + meetingTime(it, zone, locale) } ?: "")
            H1(text(mm, "title") ?: mid, 36.sp)
        }
        val cards = speakerCards(mm)
        val speakers = if (mm.optJSONArray("speakers") != null) cards.size else mm.optInt("speakers")
        val storeDrive = stringResource(R.string.store_drive)
        Column {
            Cells(3, listOf(
                CellSpec(stringResource(R.string.length_label)) {
                    Text(meetingLength(mm.optDouble("duration_s", 0.0)), fontSize = 17.sp, fontWeight = FontWeight.W500, style = tnum())
                },
                CellSpec(stringResource(R.string.speakers_label)) { Value(if (speakers > 0) "$speakers" else stringResource(R.string.not_yet)) },
                CellSpec(stringResource(R.string.drive_name)) { Toggle(drive, { setDrive(it) }, null, storeDrive) },
            ))
            Spacer(Modifier.height(12.dp))
            val folder = text(mm, "drive_url")?.takeIf { it.startsWith("https://") }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(note ?: when {
                    !drive -> stringResource(R.string.note_local, where)
                    folder != null -> stringResource(R.string.note_done)
                    else -> stringResource(R.string.note_waiting)
                }, Modifier.weight(1f, fill = false), fontSize = 14.sp, lineHeight = 20.sp)
                if (drive && folder != null) LinkText(stringResource(R.string.open_folder)) { open(ctx, folder) }
            }
        }
        if (mm.optString("state") == "done") Summary(mm.optString("summary_md"))
        else Text(stringResource(if (mm.optString("state") == "failed") R.string.st_failed else R.string.not_ready), color = p.muted)
        Column {
            Rule(thick = true)
            if (cards.isNotEmpty()) {
                val unnamed = cards.count { it.name.isBlank() }
                Fold(stringResource(R.string.speaker_names) + if (unnamed > 0) stringResource(R.string.unnamed, unnamed) else "",
                    spkOpen, { spkOpen = !spkOpen }) {
                    cards.forEachIndexed { i, c ->
                        SpeakerCardView(i + 1, c, names[c.label] ?: "", { names[c.label] = it }, playing == c.label) { play(c) }
                    }
                    Spacer(Modifier.height(16.dp))
                    OutlineButton(stringResource(R.string.save_names)) { saveNames() }
                }
            }
            Fold(stringResource(R.string.transcript), trOpen, { trOpen = !trOpen }) {
                val t = transcript
                when {
                    t == null -> Text("…", color = p.muted)
                    t.isEmpty() -> Text(stringResource(R.string.not_yet), color = p.muted)
                    else -> TranscriptView(t)
                }
            }
            Fold(stringResource(R.string.more), moreOpen, { moreOpen = !moreOpen }) {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlineButton(stringResource(R.string.new_summary)) { newSummary() }
                    text(mm, "url")?.takeIf { it.startsWith("https://") }?.let { url ->
                        LinkText(stringResource(R.string.open_box, where)) { open(ctx, url) }
                    }
                }
            }
        }
    }
}

/** A fold, as the web apps' details.fold: its name in small capitals, + or − on the right, then what it holds; a 1dp rule under it. */
@Composable
private fun Fold(title: String, open: Boolean, onToggle: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    Column {
        Row(Modifier.fillMaxWidth().clickable(onClick = onToggle).padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(title.uppercase(), Modifier.weight(1f), fontSize = 11.sp, fontWeight = FontWeight.W700, letterSpacing = 0.55.sp)
            Text(if (open) "−" else "+", fontSize = 18.sp, fontWeight = FontWeight.W500)
        }
        if (open) Column(Modifier.padding(bottom = 16.dp), content = content)
        Rule()
    }
}

/**
 * A speaker: how much they said and when first, Play (seven seconds of their voice), a few of their
 * lines, names suggested for them with why (a tap puts one in the field), and the name field.
 */
@Composable
private fun SpeakerCardView(n: Int, c: SpeakerCard, name: String, onName: (String) -> Unit, playing: Boolean, onPlay: () -> Unit) {
    val p = pal()
    val by = mapOf("recorder" to stringResource(R.string.by_recorder), "agent" to stringResource(R.string.by_agent),
        "person" to stringResource(R.string.by_person))
    Column(Modifier.fillMaxWidth().padding(vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("%02d".format(n), Modifier.width(34.dp).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W700)
            Text(c.label, Modifier.weight(1f).alignByBaseline(), fontSize = 15.sp, fontWeight = FontWeight.W700)
            if (c.clipUrl != null) Text(stringResource(if (playing) R.string.stop_play else R.string.play),
                Modifier.alignByBaseline().clickable(onClick = onPlay).padding(vertical = 4.dp), fontSize = 14.sp,
                fontWeight = FontWeight.W700, textDecoration = TextDecoration.Underline)
        }
        if (c.turns > 0) Text(stringResource(R.string.talk, c.share, c.turns, meetingLength(c.first)), fontSize = 13.sp, lineHeight = 18.sp, color = p.muted)
        c.lines.forEach { Text("\"$it\"", fontSize = 14.sp, lineHeight = 20.sp, color = p.muted) }
        c.suggestions.forEach { s ->
            val use = stringResource(R.string.use_name, s.name)
            Text(buildAnnotatedString {
                withStyle(SpanStyle(color = p.red, fontWeight = FontWeight.W700, textDecoration = TextDecoration.Underline)) { append(use) }
                withStyle(SpanStyle(color = p.muted)) { append("  " + (by[s.by] ?: "") + if (s.why.isNotBlank()) ": " + s.why else "") }
            }, Modifier.clickable { onName(s.name) }, fontSize = 14.sp, lineHeight = 20.sp)
        }
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Label(stringResource(R.string.name_label))
            NameField(name, onName)
        }
    }
    Rule()
}

/** A speaker's name: the text over a 1dp rule. */
@Composable
private fun NameField(value: String, onChange: (String) -> Unit) {
    val p = pal()
    BasicTextField(value, onChange, Modifier.fillMaxWidth(), singleLine = true, cursorBrush = SolidColor(p.ink),
        textStyle = LocalTextStyle.current.copy(fontSize = 17.sp, lineHeight = 24.sp, fontWeight = FontWeight.W500, color = p.ink),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Words, imeAction = ImeAction.Done),
        decorationBox = { inner ->
            Column {
                Box(Modifier.padding(top = 4.dp, bottom = 8.dp)) { inner() }
                Rule()
            }
        })
}

/** The transcript as turns: who (bold) and when (quiet), then what they said. */
@Composable
private fun TranscriptView(parts: List<TranscriptPart>) {
    val p = pal()
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        for (part in parts) when (part) {
            is TranscriptPart.Meta -> Text(part.text, fontSize = 13.sp, lineHeight = 19.sp, color = p.muted)
            is TranscriptPart.Said -> Text(part.text, fontSize = 15.sp, lineHeight = 23.sp)
            is TranscriptPart.Turn -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(buildAnnotatedString {
                    withStyle(SpanStyle(fontWeight = FontWeight.W700)) { append(part.who) }
                    withStyle(SpanStyle(color = p.muted, fontSize = 13.sp, fontFeatureSettings = "tnum")) { append("  " + part.at) }
                }, fontSize = 15.sp)
                if (part.said.isNotEmpty()) Text(part.said.joinToString("\n\n"), fontSize = 15.sp, lineHeight = 23.sp)
            }
        }
    }
}

/** The summary as the web apps draw it: the lead, then sections with numbered rows; known sections in the app's language. */
@Composable
private fun Summary(md: String) {
    val sections = mapOf("decisions" to stringResource(R.string.sec_decisions), "actions" to stringResource(R.string.sec_actions),
        "questions" to stringResource(R.string.sec_questions))
    Column {
        var leads = 0
        for (part in summaryParts(md)) {
            when (part) {
                is SummaryPart.Lead -> {
                    if (leads++ > 0) Spacer(Modifier.height(8.dp))
                    Text(bold(part.text), fontSize = 18.sp, fontWeight = FontWeight.W500, lineHeight = 26.sp)
                }
                is SummaryPart.Heading -> {
                    Spacer(Modifier.height(26.dp))
                    Section(part.key?.let { sections[it] } ?: part.text)
                }
                is SummaryPart.Item -> NumberedRow(part.n, bold(part.text), part.side)
                is SummaryPart.Para -> Text(bold(part.text), Modifier.padding(vertical = 6.dp), fontSize = 15.sp, lineHeight = 22.sp)
            }
        }
    }
}

/** The summary's **bold** as bold. */
private fun bold(text: String): AnnotatedString = buildAnnotatedString {
    var at = 0
    for (m in Regex("\\*\\*(.+?)\\*\\*").findAll(text)) {
        append(text.substring(at, m.range.first))
        withStyle(SpanStyle(fontWeight = FontWeight.W700)) { append(m.groupValues[1]) }
        at = m.range.last + 1
    }
    append(text.substring(at))
}

@Composable
private fun SettingsScreen(cfg: BoxConfig, onBack: () -> Unit, onDisconnect: () -> Unit) {
    val ctx = LocalContext.current
    val box by boxState.collectAsState()
    val me = remember { Pairing.me(ctx) }
    val pm = remember { ctx.getSystemService(PowerManager::class.java) }
    var ignoring by remember { mutableStateOf(pm.isIgnoringBatteryOptimizations(ctx.packageName)) }
    val api = remember(cfg) { BoxApi(cfg) }
    val scope = rememberCoroutineScope()
    var google by remember { mutableStateOf<JSONObject?>(null) }       // the status op's "google"
    var googleAsk by remember { mutableStateOf(false) }
    var googleNote by remember { mutableStateOf<String?>(null) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(Unit) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {      // back from the system dialog, or from Google's sign-in
            ignoring = pm.isIgnoringBatteryOptimizations(ctx.packageName)
            withContext(Dispatchers.IO) { runCatching { api.obj("status").optJSONObject("google") } }.getOrNull()?.let {
                google = it
                if (it.optBoolean("connected")) googleNote = null
            }
        }
    }
    val finish = stringResource(R.string.g_finish)
    fun connectGoogle() = scope.launch {
        withContext(Dispatchers.IO) { runCatching { api.obj("drive_connect").getString("url") } }
            .onSuccess { open(ctx, it); googleNote = finish }
            .onFailure { googleNote = it.message }
    }
    fun disconnectGoogle() = scope.launch {
        withContext(Dispatchers.IO) { runCatching { api.obj("drive_disconnect") } }
            .onSuccess { google = it; googleAsk = false }
            .onFailure { googleNote = it.message }
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
        // Google Drive, as in the indie-recorder app: the person connects their own once; the box then
        // puts the meetings they keep in Drive there itself.
        val g = google
        if (g != null && g.optBoolean("available")) Column {
            Section(stringResource(R.string.storage_label))
            val connected = g.optBoolean("connected")
            Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(Modifier.weight(1f)) {
                    Text(stringResource(R.string.drive_name), fontSize = 16.sp)
                    Text(googleNote ?: when {
                        connected -> stringResource(R.string.g_connected, text(g, "email") ?: "")
                        text(g, "problem") == "expired" -> stringResource(R.string.g_expired)
                        else -> stringResource(R.string.g_not)
                    }, fontSize = 13.sp, lineHeight = 18.sp, color = pal().muted)
                }
                if (connected) OutlineButton(stringResource(R.string.g_disconnect)) { googleAsk = true }
                else RedButton(stringResource(R.string.g_connect)) { connectGoogle() }
            }
            if (googleAsk && connected) {
                Text(stringResource(R.string.g_ask), fontSize = 14.sp, lineHeight = 20.sp)
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    RedButton(stringResource(R.string.g_disconnect)) { disconnectGoogle() }
                    LinkText(stringResource(R.string.cancel)) { googleAsk = false }
                }
                Spacer(Modifier.height(12.dp))
            }
            Rule()
        }
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
