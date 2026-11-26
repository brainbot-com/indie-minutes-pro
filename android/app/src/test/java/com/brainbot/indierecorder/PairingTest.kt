package com.brainbot.indierecorder

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.net.URLEncoder
import java.util.Base64

class PairingTest {
    private val der = byteArrayOf(0x30, 0x82.toByte(), 1, 2, 3)
    private val ca = Base64.getUrlEncoder().withoutPadding().encodeToString(der)
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    @Test
    fun parsesTheDevicesPageLink() {
        val c = BoxConfig.parse("indierecorder://pair?u=${enc("https://192.168.1.20:8443/")}&t=abcdefghijklmnopqrstuvwxyz012345" +
            "&ca=$ca&n=${enc("Luis's phone")}")!!
        assertEquals("https://192.168.1.20:8443", c.url)
        assertEquals("192.168.1.20:8443", c.host)
        assertEquals("abcdefghijklmnopqrstuvwxyz012345", c.token)
        assertArrayEquals(der, c.caDer)
        assertEquals("Luis's phone", c.name)
    }

    @Test
    fun refusesWhatIsNotAPairingLink() {
        assertNull(BoxConfig.parse("https://192.168.1.20:8443/app/#pair=abcdefghijklmnopqrstuvwxyz"))
        assertNull(BoxConfig.parse("indierecorder://pair?u=${enc("http://192.168.1.20")}&t=abcdefghijklmnopqrstuvwxyz&ca=$ca"))
        assertNull(BoxConfig.parse("indierecorder://pair?u=${enc("https://192.168.1.20:8443")}&t=short&ca=$ca"))
        assertNull(BoxConfig.parse("indierecorder://pair?u=${enc("https://192.168.1.20:8443")}&t=abcdefghijklmnopqrstuvwxyz"))
    }
}
