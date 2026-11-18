"""Encryption for meetings, at rest on the box and on their way from the phone.

At rest: every file a meeting leaves under data/meetings, uploads and outbox is
AES-256-GCM in 1 MiB segments. Each segment has its own nonce and is bound to
its file, its place and whether it is the last, so the audio player can read
from the middle and a cut or shuffled file is noticed. The index database is
SQLCipher with a key derived from the same one. The key is DATA_KEY_FILE in
secrets/ (deploy/make-keys.sh): data/ without it is noise. It does not stop
root on the running box, which holds the key. Keep a copy of the key
somewhere else: without it the meetings are gone.

On the way: a phone makes a P-256 key pair per recording, agrees a key with
the box's sealing key (ECDH, then HKDF-SHA256) and encrypts each chunk with
AES-256-GCM before it stores or sends it; the recording carries the public
half. Only the box's private sealing key (SEAL_KEY_FILE) opens the chunks, so
what waits on the phone or passes a proxy is ciphertext.
"""
import base64
import hashlib
import hmac
import io
import os
import secrets
import shutil
import struct
import tempfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"IRV1"
SEG = 1 << 20
HEAD = len(MAGIC) + 4 + 16          # magic, segment size, file id
OVER = 12 + 16                      # each segment's nonce and tag
SEAL_ALG = "ECDH-P256+HKDF-SHA256+A256GCM"
SEAL_SALT = b"indie-recorder/seal/v1"


def b64u(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class VaultError(RuntimeError):
    pass


class _Writer:
    def __init__(self, aes, f, seg=SEG):
        self.aes, self.f, self.seg = aes, f, seg
        self.fid, self.buf, self.n = secrets.token_bytes(16), bytearray(), 0
        f.write(MAGIC + struct.pack(">I", seg) + self.fid)

    def write(self, data):
        self.buf += data
        while len(self.buf) > self.seg:            # keep the rest back: the last segment is marked as such
            self._segment(bytes(self.buf[:self.seg]), False)
            del self.buf[:self.seg]

    def close(self):
        self._segment(bytes(self.buf), True)
        self.buf = bytearray()

    def _segment(self, plain, last):
        nonce = secrets.token_bytes(12)
        self.f.write(nonce + self.aes.encrypt(nonce, plain, self.fid + struct.pack(">QB", self.n, last)))
        self.n += 1


class Vault:
    """Off (no key): files are read and written as they are."""

    def __init__(self, key=None, roots=(), scratch=None):
        self.aes = AESGCM(key) if key else None
        self.key = key
        self.roots = [Path(r).resolve() for r in roots]
        self.scratch_dir = Path(scratch) if scratch else Path(tempfile.gettempdir())

    @property
    def on(self):
        return self.aes is not None

    def covers(self, path):
        if not self.on:
            return False
        p = Path(path).resolve()
        return any(p == r or r in p.parents for r in self.roots)

    def subkey(self, purpose):
        """A key for something else (the index database) from the data key."""
        return HKDF(hashes.SHA256(), 32, b"indie-recorder/vault/v1", purpose.encode()).derive(self.key)

    def fingerprint(self):
        return hmac.new(self.key, b"indie-recorder/vault/fingerprint", hashlib.sha256).hexdigest()[:16]

    # -- bytes in memory --------------------------------------------------------

    def seal(self, data):
        out = io.BytesIO()
        w = _Writer(self.aes, out)
        w.write(data)
        w.close()
        return out.getvalue()

    def open(self, blob):
        if not blob.startswith(MAGIC):
            return blob                              # written before encryption at rest, or not covered
        if not self.on:
            raise VaultError("this file is encrypted and the box has no data key (DATA_KEY_FILE)")
        return b"".join(self._plain(io.BytesIO(blob), len(blob)))

    # -- files --------------------------------------------------------------------

    def write(self, path, data):
        with self.writer(path) as f:
            f.write(data)

    def read(self, path):
        with open(path, "rb") as f:
            head = f.read(len(MAGIC))
            if head != MAGIC:
                return head + f.read()
            size = os.fstat(f.fileno()).st_size
            return b"".join(self._plain(f, size))

    @contextmanager
    def writer(self, path):
        """Writes a file whole or not at all; sealed when the path is covered."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        # the box user and the Drive agent must read them (mkstemp makes 0600)
        os.fchmod(fd, 0o644)
        try:
            with os.fdopen(fd, "wb") as f:
                if self.covers(path):
                    w = _Writer(self.aes, f)
                    yield w
                    w.close()
                else:
                    yield f
            os.replace(tmp, path)
        except BaseException:
            with _quiet():
                os.unlink(tmp)
            raise

    def is_sealed(self, path):
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC

    def size(self, path):
        """The plain size, without reading the file."""
        size = os.path.getsize(path)
        if not self.is_sealed(path):
            return size
        seg = self._header(path)[0]
        n = max(1, -(-(size - HEAD) // (seg + OVER)))
        return size - HEAD - n * OVER

    def stream(self, path, start=0, end=None):
        """Plain bytes [start, end) of a file, segment by segment (for range requests)."""
        if not self.is_sealed(path):
            with open(path, "rb") as f:
                f.seek(start)
                left = None if end is None else end - start
                while left is None or left > 0:
                    block = f.read(SEG if left is None else min(SEG, left))
                    if not block:
                        return
                    if left is not None:
                        left -= len(block)
                    yield block
            return
        seg, fid = self._header(path)
        size = os.path.getsize(path)
        n = max(1, -(-(size - HEAD) // (seg + OVER)))
        plain = size - HEAD - n * OVER
        end = plain if end is None else min(end, plain)
        with open(path, "rb") as f:
            i = start // seg
            while i < n and i * seg < end:
                f.seek(HEAD + i * (seg + OVER))
                rec = f.read(seg + OVER)
                block = self._decrypt(rec, fid, i, i == n - 1)
                lo = max(0, start - i * seg)
                hi = min(len(block), end - i * seg)
                if hi > lo:
                    yield block[lo:hi]
                i += 1

    def export(self, src, dst):
        """A plain copy, for ffmpeg and the engines, in the scratch space."""
        with open(dst, "wb") as out:
            for block in self.stream(src):
                out.write(block)

    def take(self, src, dst):
        """A plain file into the covered tree (sealed there)."""
        with open(src, "rb") as f, self.writer(dst) as out:
            while block := f.read(SEG):
                out.write(block)

    def take_tree(self, src, dst):
        for p in sorted(Path(src).rglob("*")):
            if p.is_file():
                self.take(p, Path(dst) / p.relative_to(src))

    @contextmanager
    def scratch(self, name):
        """A private folder in RAM (a tmpfs on the box) for plain audio during a job."""
        d = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=self.scratch_dir))
        try:
            yield d
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def seal_in_place(self, path):
        """Encrypts an older plain file; checks the result before it replaces the original."""
        path = Path(path)
        if not self.covers(path) or self.is_sealed(path):
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while block := f.read(SEG):
                digest.update(block)
        tmp = path.with_name(path.name + ".sealing")
        with open(path, "rb") as f, open(tmp, "wb") as out:
            w = _Writer(self.aes, out)
            while block := f.read(SEG):
                w.write(block)
            w.close()
        check = hashlib.sha256()
        for block in self.stream(tmp):
            check.update(block)
        if check.digest() != digest.digest():
            os.unlink(tmp)
            raise VaultError(f"{path}: the encrypted copy does not match, left as it was")
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
        return True

    # -- inside ------------------------------------------------------------------

    def _header(self, path):
        with open(path, "rb") as f:
            head = f.read(HEAD)
        return struct.unpack(">I", head[4:8])[0], head[8:24]

    def _plain(self, f, size):
        f.seek(0)
        head = f.read(HEAD)
        seg, fid = struct.unpack(">I", head[4:8])[0], head[8:24]
        n = max(1, -(-(size - HEAD) // (seg + OVER)))
        for i in range(n):
            yield self._decrypt(f.read(seg + OVER), fid, i, i == n - 1)

    def _decrypt(self, rec, fid, i, last):
        if not self.on:
            raise VaultError("this file is encrypted and the box has no data key (DATA_KEY_FILE)")
        try:
            return self.aes.decrypt(rec[:12], rec[12:], fid + struct.pack(">QB", i, last))
        except Exception as e:
            raise VaultError("a file failed its integrity check (wrong key, or it was changed or cut)") from e


@contextmanager
def _quiet():
    try:
        yield
    except OSError:
        pass


# -- the box's sealing key: phones encrypt for it -----------------------------------


class Sealer:
    def __init__(self, pem):
        self.private = serialization.load_pem_private_key(pem, password=None)
        raw = self.private.public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
        self.public = b64u(raw)
        self.kid = hashlib.sha256(raw).hexdigest()[:16]

    def describe(self):
        return {"kid": self.kid, "alg": SEAL_ALG, "key": self.public}

    def chunk_key(self, epk, client_id):
        """The recording's chunk key from the phone's public half (b64url, uncompressed P-256)."""
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(epk))
        shared = self.private.exchange(ec.ECDH(), peer)
        return HKDF(hashes.SHA256(), 32, SEAL_SALT, client_id.encode()).derive(shared)


def chunk_nonce(idx):
    return b"\0\0\0\0" + struct.pack(">Q", idx)


def chunk_aad(client_id, idx):
    return f"indie-recorder/chunk/v1:{client_id}:{idx}".encode()


def open_chunk(key, client_id, idx, data):
    try:
        return AESGCM(key).decrypt(chunk_nonce(idx), data, chunk_aad(client_id, idx))
    except Exception as e:
        raise VaultError(f"chunk {idx} does not open with this recording's key") from e


def seal_chunk(key, client_id, idx, plain):
    """What a phone does (the apps implement it themselves; this is for tests and the simulator)."""
    return AESGCM(key).encrypt(chunk_nonce(idx), plain, chunk_aad(client_id, idx))


def phone_side(box_public, client_id):
    """A phone's half: (its public key as b64url, the chunk key). For tests and the simulator."""
    eph = ec.generate_private_key(ec.SECP256R1())
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(box_public))
    shared = eph.exchange(ec.ECDH(), peer)
    key = HKDF(hashes.SHA256(), 32, SEAL_SALT, client_id.encode()).derive(shared)
    raw = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64u(raw), key


# -- the vault the service uses -------------------------------------------------------

VAULT = Vault()
SEALER = None


def configure(settings):
    """Loads the keys (made by deploy/make-keys.sh); refuses to start without them
    when encryption at rest is on, and with a different data key than the data
    was written with."""
    global VAULT, SEALER
    roots = [settings.meetings_dir, settings.uploads_dir, settings.outbox_dir]   # what holds meeting content
    key = None
    if settings.encrypt_at_rest:
        try:
            key = unb64u(Path(settings.data_key_file).read_text().strip())
        except FileNotFoundError:
            raise VaultError(f"no data key at {settings.data_key_file}: run deploy/make-keys.sh "
                             "(or set ENCRYPT_AT_REST=0)") from None
        if len(key) != 32:
            raise VaultError(f"{settings.data_key_file} must hold 32 bytes (base64url)")
    vault = Vault(key, roots, scratch=settings.scratch_dir)
    marker = settings.data_dir / ".vault"
    if vault.on:
        if marker.exists() and marker.read_text().strip() != vault.fingerprint():
            raise VaultError(f"the data in {settings.data_dir} was encrypted with another key than {settings.data_key_file}")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(vault.fingerprint() + "\n")
    elif marker.exists():
        raise VaultError(f"the data in {settings.data_dir} is encrypted: set ENCRYPT_AT_REST=1 and DATA_KEY_FILE")
    settings.scratch_dir.mkdir(parents=True, exist_ok=True)
    VAULT = vault
    try:
        SEALER = Sealer(Path(settings.seal_key_file).read_bytes())
    except FileNotFoundError:
        SEALER = None                                 # phones then send chunks without the extra layer
    return vault
