package com.brainbot.indierecorder

import java.math.BigInteger
import java.nio.ByteBuffer
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Chunks are sealed on the phone before they are kept or sent, the same way as in
 * the web app (recorder/vault.py, pwa/app.js): a P-256 key pair per recording, ECDH
 * with the box's sealing key, HKDF-SHA256 (salt "indie-recorder/seal/v1", info the
 * recording's client id) gives its AES-256-GCM key. Each chunk gets the nonce
 * 0,0,0,0 + its index (8 bytes, big-endian) and the AAD
 * "indie-recorder/chunk/v1:<client id>:<index>". Only the box opens them.
 */
object Seal {
    private val SALT = "indie-recorder/seal/v1".toByteArray()

    /** The box's sealing key from its health answer: its id and the uncompressed P-256 point. */
    class BoxKey(val kid: String, val raw: ByteArray)

    /** A recording's seal: what the box needs (kid, the phone's public half) and the chunk key. */
    class Start(val kid: String, val epk: String, val key: ByteArray)

    private val curve: ECParameterSpec by lazy { (newKeyPair().public as ECPublicKey).params }

    fun newKeyPair(): KeyPair =
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    /** A new seal for a recording; the key pair is only an argument for the tests. */
    fun start(box: BoxKey, clientId: String, pair: KeyPair = newKeyPair()): Start =
        Start(box.kid, b64u(raw(pair.public as ECPublicKey)), chunkKey(box.raw, pair.private, clientId))

    fun chunkKey(boxRaw: ByteArray, mine: PrivateKey, clientId: String): ByteArray {
        val agreement = KeyAgreement.getInstance("ECDH")
        agreement.init(mine)
        agreement.doPhase(publicKey(boxRaw), true)
        return hkdf(agreement.generateSecret(), SALT, clientId.toByteArray())
    }

    fun seal(key: ByteArray, clientId: String, idx: Int, plain: ByteArray): ByteArray =
        gcm(Cipher.ENCRYPT_MODE, key, clientId, idx).doFinal(plain)

    /** For a chunk sealed for a box key that changed before anything was sent (Uploader). */
    fun open(key: ByteArray, clientId: String, idx: Int, sealed: ByteArray): ByteArray =
        gcm(Cipher.DECRYPT_MODE, key, clientId, idx).doFinal(sealed)

    private fun gcm(mode: Int, key: ByteArray, clientId: String, idx: Int): Cipher {
        val nonce = ByteBuffer.allocate(12).putInt(0).putLong(idx.toLong()).array()
        return Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
            updateAAD("indie-recorder/chunk/v1:$clientId:$idx".toByteArray())
        }
    }

    /** HKDF-SHA256 for one 32-byte block (RFC 5869). */
    fun hkdf(ikm: ByteArray, salt: ByteArray, info: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(salt, "HmacSHA256"))
        val prk = mac.doFinal(ikm)
        mac.init(SecretKeySpec(prk, "HmacSHA256"))
        mac.update(info)
        mac.update(1.toByte())
        return mac.doFinal()
    }

    fun publicKey(raw: ByteArray): ECPublicKey {
        require(raw.size == 65 && raw[0] == 4.toByte()) { "not an uncompressed P-256 point" }
        val point = ECPoint(BigInteger(1, raw.copyOfRange(1, 33)), BigInteger(1, raw.copyOfRange(33, 65)))
        return KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(point, curve)) as ECPublicKey
    }

    fun raw(key: ECPublicKey): ByteArray = byteArrayOf(4) + fixed32(key.w.affineX) + fixed32(key.w.affineY)

    private fun fixed32(n: BigInteger): ByteArray {
        val b = n.toByteArray().let { if (it.size > 32) it.copyOfRange(it.size - 32, it.size) else it }
        return ByteArray(32 - b.size) + b
    }

    fun b64u(data: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(data)
    fun unb64u(text: String): ByteArray = Base64.getUrlDecoder().decode(text.trimEnd('='))
}
