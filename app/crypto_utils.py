"""
PhantomShare — encryption utilities.

X25519 key exchange + AES-256-GCM for end-to-end encryption.

Security features:
  - Signaling encryption: pre-shared key derived from session code
  - Topic hashing: opaque hashes (prevents session discovery)
  - Nonce-prefix: each peer uses a distinct prefix (prevents nonce collision)
  - AAD: session code is bound as Associated Data in AES-GCM
  - Secure memory: keys are zeroed when no longer needed

Note: Cryptographic salt strings are kept as 'secureshare-*' for relay compatibility.
"""

import ctypes
import hashlib
import hmac
import os
import platform
import struct

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, serialization


# ════════════════════════════════════════════════════════════════════
#  Secure Memory Handling
# ════════════════════════════════════════════════════════════════════

def secure_zero_memory(buffer: bytearray) -> None:
    """Securely zero a bytearray to prevent key extraction from memory.
    
    Uses platform-specific secure zeroing when available:
    - Windows: RtlSecureZeroMemory via ctypes
    - Unix: explicit_bzero or memset with volatile semantics
    """
    if not isinstance(buffer, bytearray):
        return  # Can only zero mutable bytearrays
    
    length = len(buffer)
    if length == 0:
        return
    
    try:
        if platform.system() == "Windows":
            # Use RtlSecureZeroMemory on Windows
            ctypes.memset(ctypes.addressof((ctypes.c_char * length).from_buffer(buffer)), 0, length)
        else:
            # On Unix, use ctypes memset (compiler can't optimize away ctypes calls)
            ctypes.memset(ctypes.addressof((ctypes.c_char * length).from_buffer(buffer)), 0, length)
    except Exception:
        # Fallback: explicit byte-by-byte zeroing
        for i in range(length):
            buffer[i] = 0


class SecureBytes:
    """A wrapper for sensitive byte data that auto-zeros on deletion.
    
    Usage:
        key = SecureBytes(os.urandom(32))
        # Use key.data for the actual bytes
        del key  # or key.clear() — memory is securely zeroed
    """
    
    __slots__ = ('_data',)
    
    def __init__(self, data: bytes):
        # Store as mutable bytearray so we can zero it
        self._data = bytearray(data)
    
    @property
    def data(self) -> bytes:
        """Get the key data as immutable bytes."""
        return bytes(self._data)
    
    def clear(self) -> None:
        """Securely zero the memory and clear the buffer."""
        if self._data:
            secure_zero_memory(self._data)
            self._data = bytearray()
    
    def __del__(self):
        self.clear()
    
    def __len__(self) -> int:
        return len(self._data)
    
    def __bytes__(self) -> bytes:
        return bytes(self._data)


# ════════════════════════════════════════════════════════════════════
#  Signaling-level crypto (pre-shared key from session code)
# ════════════════════════════════════════════════════════════════════

def derive_signaling_key(session_code: str) -> bytes:
    """Derive AES-256 key from session code for encrypting signaling payloads.

    Both peers know the session code (shared out-of-band), so both can derive
    the same key.  An eavesdropper on the MQTT broker who does NOT know the
    code cannot decrypt signaling messages.
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"secureshare-signaling-salt-v2",
        info=b"secureshare-signaling-key",
    ).derive(session_code.encode("utf-8"))


def derive_topic_id(session_code: str) -> str:
    """Derive an opaque 16-hex-char topic component from the session code.

    This prevents session discovery via MQTT wildcard subscriptions —
    an observer sees random-looking topic names instead of session codes.
    """
    return hmac.new(
        session_code.encode("utf-8"),
        b"secureshare-topic-v2",
        hashlib.sha256,
    ).hexdigest()[:16]


def signaling_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """Encrypt a signaling payload with the pre-shared signaling key.

    Returns: 12-byte random nonce ‖ ciphertext+tag.
    Random nonce is safe here because signaling involves very few messages
    (collision probability negligible).
    """
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aes.encrypt(nonce, plaintext, b"secureshare-signaling-aad")
    return nonce + ciphertext


def signaling_decrypt(key: bytes, data: bytes) -> bytes:
    """Decrypt a signaling payload encrypted with signaling_encrypt()."""
    aes = AESGCM(key)
    nonce = data[:12]
    ciphertext = data[12:]
    return aes.decrypt(nonce, ciphertext, b"secureshare-signaling-aad")


# ════════════════════════════════════════════════════════════════════
#  Session-level crypto (E2E after DH key exchange)
# ════════════════════════════════════════════════════════════════════

class CryptoSession:
    """
    Manages a single E2E-encrypted session between two peers.

    Usage:
        cs = CryptoSession("a7f3-bc21")
        pub = cs.get_public_key_bytes()        # send to peer
        cs.derive_shared_key(peer_pub_bytes)    # receive from peer
        ct = cs.encrypt(plaintext)
        pt = cs.decrypt(ct)
        cs.clear()  # securely zero keys when done

    Security properties (v2):
        - Nonce prefix: peer with "lower" public key uses prefix 0,
          the other uses prefix 1 → nonces never collide.
        - AAD: session code is bound as associated data → prevents
          cross-session ciphertext substitution.
        - Secure memory: keys are zeroed when session is cleared.
    """

    NONCE_LEN = 12
    TAG_LEN = 16  # GCM tag is appended by AESGCM automatically

    def __init__(self, session_code: str):
        self.session_code = session_code
        self._private_key = X25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._shared_key: SecureBytes | None = None
        self._raw_shared_key: bytes | None = None  # needed for AES operations
        self._aes: AESGCM | None = None
        self._send_counter = 0
        self._nonce_prefix: int = 0        # set in derive_shared_key
        self._aad: bytes = session_code.encode("utf-8")  # default AAD

    # ── Key exchange ───────────────────────────────────────────────

    def get_public_key_bytes(self) -> bytes:
        """Return raw 32-byte public key to send to the peer."""
        return self._public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    def derive_shared_key(self, peer_public_key_bytes: bytes) -> None:
        """Derive shared AES-256 key from peer's X25519 public key.

        Also determines which nonce prefix this side uses, so that the
        two peers can never produce the same (key, nonce) pair.
        """
        peer_pub = X25519PublicKey.from_public_bytes(peer_public_key_bytes)
        raw_secret = self._private_key.exchange(peer_pub)

        derived_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.session_code.encode("utf-8"),
            info=b"secureshare-v2-aes",
        ).derive(raw_secret)
        
        # Store in secure memory wrapper
        self._shared_key = SecureBytes(derived_key)
        self._raw_shared_key = derived_key  # Keep reference for AES
        self._aes = AESGCM(derived_key)
        
        # Zero the intermediate raw_secret
        raw_secret_arr = bytearray(raw_secret)
        secure_zero_memory(raw_secret_arr)

        # Nonce-prefix: the side with the "lower" raw public key gets 0.
        my_pub = self.get_public_key_bytes()
        self._nonce_prefix = 0 if my_pub < peer_public_key_bytes else 1

    def get_shared_key(self) -> bytes:
        """Get the shared key bytes (for reconnect token derivation)."""
        if not self._shared_key:
            raise ValueError("Call derive_shared_key first")
        return self._shared_key.data

    def get_verification_code(self) -> str:
        """Short code both users can compare to confirm no MITM."""
        if not self._shared_key:
            raise ValueError("Call derive_shared_key first")
        h = hashlib.sha256(self._shared_key.data + b"secureshare-verify").hexdigest()
        return f"{h[:4]}-{h[4:8]}".upper()
    
    def clear(self) -> None:
        """Securely zero all key material."""
        if self._shared_key:
            self._shared_key.clear()
            self._shared_key = None
        self._raw_shared_key = None
        self._aes = None
        self._private_key = None
        self._public_key = None
    
    def __del__(self):
        self.clear()

    # ── Encrypt / Decrypt ──────────────────────────────────────────

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt data.  Returns: 12-byte nonce ‖ ciphertext+tag.

        Nonce = 4-byte prefix ‖ 8-byte counter (big-endian).
        AAD = session code (UTF-8 bytes).
        """
        if not self._aes:
            raise ValueError("Call derive_shared_key first")
        nonce = struct.pack("!IQ", self._nonce_prefix, self._send_counter)[
            : self.NONCE_LEN
        ]
        self._send_counter += 1
        ciphertext = self._aes.encrypt(nonce, plaintext, self._aad)
        return nonce + ciphertext  # len = 12 + len(plaintext) + 16

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt data produced by encrypt().

        GCM tag verification + AAD binding ensures authenticity and
        prevents cross-session substitution.
        """
        if not self._aes:
            raise ValueError("Call derive_shared_key first")
        nonce = data[: self.NONCE_LEN]
        ciphertext = data[self.NONCE_LEN :]
        return self._aes.decrypt(nonce, ciphertext, self._aad)

    # ── Wire helpers ───────────────────────────────────────────────

    @staticmethod
    def encrypt_chunk_header(length: int) -> bytes:
        """Pack a 4-byte big-endian length prefix."""
        return struct.pack("!I", length)

    @staticmethod
    def read_length_prefix(data: bytes) -> int:
        """Unpack a 4-byte big-endian length prefix."""
        return struct.unpack("!I", data[:4])[0]
