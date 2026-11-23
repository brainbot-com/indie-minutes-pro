package com.brainbot.indierecorder

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.URLEncoder
import java.util.Base64

class ConnectTest {
    private val der = byteArrayOf(0x30, 0x82.toByte(), 1, 2, 3)
    private val ca = Base64.getUrlEncoder().withoutPadding().encodeToString(der)
    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")

    @Test
    fun codesAreReadHoweverTheyAreTyped() {
        assertEquals("K7Q4M2XB", ConnectCode.normalize(" k7q4-m2xb "))
        assertEquals("K7Q4M2XB", ConnectCode.normalize("https://ai-knowledge.mz.brainbot.com/indie-recorder/connect#K7Q4M2XB"))
        assertEquals("K7Q4M2XB", ConnectCode.normalize("K7Q4 M2XB 9999"))       // at most 8 characters
        assertEquals("", ConnectCode.normalize("0O1IL"))                       // not in the alphabet
        assertEquals("K7Q4-M2XB", ConnectCode.shown("K7Q4M2XB"))
        assertEquals("K7Q", ConnectCode.shown("K7Q"))
    }

    @Test
    fun theConnectPagesLinkCarriesCodeAddressAndCa() {
        val l = PairLink.parse("indierecorder://pair?c=k7q4m2xb&u=${enc("https://10.1.9.7:8443/")}&ca=$ca") as PairLink.Code
        assertEquals("K7Q4M2XB", l.code)
        assertEquals("https://10.1.9.7:8443", l.url)
        assertArrayEquals(der, l.caDer)
    }

    @Test
    fun olderLinksWithATokenStillWork() {
        val l = PairLink.parse("indierecorder://pair?u=${enc("https://10.1.9.7:8443")}&t=abcdefghijklmnopqrstuvwxyz012345&ca=$ca")
        assertTrue(l is PairLink.Token)
        assertEquals("abcdefghijklmnopqrstuvwxyz012345", (l as PairLink.Token).cfg.token)
    }

    @Test
    fun refusesLinksThatCannotConnect() {
        assertNull(PairLink.parse("https://10.1.9.7:8443/connect#K7Q4M2XB"))
        assertNull(PairLink.parse("indierecorder://pair?c=K7Q4M2XB&u=${enc("http://10.1.9.7")}&ca=$ca"))   // no https
        assertNull(PairLink.parse("indierecorder://pair?c=K7Q4&u=${enc("https://10.1.9.7:8443")}&ca=$ca")) // short
        assertNull(PairLink.parse("indierecorder://pair?c=K7Q4M2XB&u=${enc("https://10.1.9.7:8443")}&ca=@@@@"))  // a CA that does not decode
    }

    @Test
    fun aBoxWithoutACaOfItsOwnConnectsWithThePhonesTrust() {
        // an Indie Box: a public certificate, or its own CA installed on the phone
        val l = PairLink.parse("indierecorder://pair?c=K7Q4M2XB&u=${enc("https://indiebox-7k2f.local:9207")}") as PairLink.Code
        assertEquals("https://indiebox-7k2f.local:9207", l.url)
        assertEquals(0, l.caDer.size)
    }

    @Test
    fun anOfferSurvivesItsOwnJson() {
        val o = Offer.from(JSONObject().put("person", "Ana").put("box", "aibox1").put("box_url", "https://10.1.9.7:8443/").put("ca", ca))!!
        val back = Offer.parse(o.toJson())!!
        assertEquals("Ana", back.person)
        assertEquals("aibox1", back.box)
        assertEquals("https://10.1.9.7:8443", back.boxUrl)
        assertArrayEquals(der, back.caDer)
        val nameless = Offer.from(JSONObject().put("person", JSONObject.NULL).put("box_url", "https://10.1.9.7:8443"))!!
        assertNull(nameless.person)
        assertNull(nameless.caDer)
        assertEquals("10.1.9.7:8443", nameless.box)
        assertNull(Offer.from(JSONObject().put("box_url", "http://10.1.9.7")))
    }

    @Test
    fun phonesGetReadableNames() {
        assertEquals("Pixel 8", readableDevice("Google", "Pixel 8"))
        assertEquals("Samsung SM-S911B", readableDevice("samsung", "SM-S911B"))
        assertEquals("OnePlus 12", readableDevice("OnePlus", "OnePlus 12"))
        assertEquals("Phone", readableDevice("", ""))
        assertEquals("Android SDK built for x86_64", readableDevice("unknown", "Android SDK built for x86_64"))
    }
}
