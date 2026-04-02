"""
PhantomShare — VPS WebSocket relay transfer.

Both sender and receiver connect to the VPS relay server.
The server pairs clients by session code and pipes raw bytes.
All data is E2E encrypted — the server never inspects content.

Protocol phases:
  1. Key Exchange + Version Negotiation (signaling-encrypted)
     Both sides send X25519 public key + protocol_version + app_version.
     Optionally includes reconnect_token for auto-reconnect.
     If versions are incompatible → clear error message → abort.
  2. Verification (signaling-encrypted)
     Both sides confirm verification code matches (user interaction).
     On auto-reconnect: skipped if reconnect_token matches.
  3. File Transfer (E2E encrypted with derived key)
     Sender: metadata → chunks → done
     Receiver: meta_ack → done_ack (with SHA-256 result)

     Resume support:
       After receiving relay_meta, the receiver checks for a matching
       .resume manifest from a previous interrupted transfer.  If found,
       relay_meta_ack includes resume=true + received_chunks list.
       The sender then skips already-received chunks.

     Auto-reconnect:
       On connection loss during transfer, both sides automatically
       reconnect with the same session code, re-do key exchange,
       skip verification (reconnect_token proves identity), and
       resume the transfer.

Wire format:
  [1 byte type][payload]

  'S' (0x53)  signaling : signaling_encrypt(JSON)
  'C' (0x43)  control   : e2e_encrypt(JSON)
  'D' (0x44)  data      : [4B seq BE] e2e_encrypt(compressed_chunk)

Control message types (JSON field "type"):
  relay_meta        sender → receiver   file info (+ transfer_id)
  relay_meta_ack    receiver → sender   ready to receive (+ resume info)
  relay_done        sender → receiver   all chunks sent
  relay_done_ack    receiver → sender   SHA-256 result
  relay_retransmit  receiver → sender   list of missing chunk indices
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import queue
import socket
import ssl
import struct
import threading
import time
import zlib
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

try:
    import websocket          # websocket-client (sync API)
    _HAS_WS = True
except ImportError:
    _HAS_WS = False


def _is_dns_error(exc: Exception) -> bool:
    """Return True if the exception is a DNS resolution failure (transient)."""
    # socket.gaierror is the canonical DNS error
    if isinstance(exc, socket.gaierror):
        return True
    # websocket-client wraps it; check the string as well
    msg = str(exc).lower()
    return "getaddrinfo" in msg or "name or service not known" in msg


from .config import (
    VPS_RELAY_URL,
    VPS_CHUNK_SIZE,
    VPS_MAX_FILE_SIZE,
    APP_VERSION,
    PROTOCOL_VERSION,
    MIN_PROTOCOL_VERSION,
    RESUME_MANIFEST_EXT,
    RESUME_MAX_AGE,
    RESUME_SAVE_INTERVAL,
    RECONNECT_MAX_RETRIES,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    VPS_CERT_FINGERPRINTS,
    CERT_PINNING_ENABLED,
    CHUNK_SIZE_MIN,
    CHUNK_SIZE_MAX,
    CHUNK_SIZE_ADAPTIVE,
)
from .crypto_utils import (
    CryptoSession,
    derive_signaling_key,
    signaling_encrypt,
    signaling_decrypt,
)

# ── Certificate Pinning ────────────────────────────────────────────

class CertificatePinningError(Exception):
    """Raised when server certificate doesn't match pinned fingerprints."""
    pass


def _get_cert_fingerprint(cert_der: bytes) -> str:
    """Compute SHA-256 fingerprint of a DER-encoded certificate."""
    return hashlib.sha256(cert_der).hexdigest()


def _verify_cert_pinning(host: str, port: int = 443) -> bool:
    """
    Verify the server's certificate against pinned fingerprints.
    
    Returns True if pinning is disabled or certificate matches.
    Raises CertificatePinningError if certificate doesn't match.
    """
    if not CERT_PINNING_ENABLED:
        return True
    
    if not VPS_CERT_FINGERPRINTS:
        log.warning("Certificate pinning enabled but no fingerprints configured")
        return True
    
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)
                fingerprint = _get_cert_fingerprint(cert_der)
                
                # Timing-safe comparison for each pinned fingerprint
                for pinned in VPS_CERT_FINGERPRINTS:
                    if hmac.compare_digest(fingerprint.lower(), pinned.lower()):
                        log.debug(f"Certificate pinning verified for {host}")
                        return True
                
                log.error(
                    f"Certificate pinning failed for {host}. "
                    f"Got fingerprint: {fingerprint}"
                )
                raise CertificatePinningError(
                    f"Server certificate fingerprint mismatch. "
                    f"Expected one of {len(VPS_CERT_FINGERPRINTS)} pinned certificates."
                )
    except CertificatePinningError:
        raise
    except ssl.SSLCertVerificationError as e:
        log.error(f"SSL certificate verification failed: {e}")
        raise CertificatePinningError(f"SSL verification failed: {e}")
    except Exception as e:
        log.warning(f"Could not verify certificate pinning: {e}")
        # On network errors, don't block — let the WebSocket handle it
        return True


def _create_pinned_ssl_context() -> ssl.SSLContext:
    """Create an SSL context for WebSocket connections."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


ProgressCB = Callable[[int, int, float], None]
StatusCB   = Callable[[str], None]
VerifyCB   = Callable[[str], bool]   # verification_code → user_confirmed

_SIG = 0x53   # 'S'  signaling frame (key exchange / verification)
_CTL = 0x43   # 'C'  control frame   (E2E encrypted)
_DAT = 0x44   # 'D'  data frame      (E2E encrypted)

_COMPRESS_FLAG = 0x01
_RAW_FLAG      = 0x00


# ── Compression helpers ────────────────────────────────────────────

def _compress(data: bytes) -> bytes:
    c = zlib.compress(data, level=1)
    return (bytes([_COMPRESS_FLAG]) + c) if len(c) < len(data) - 64 else (bytes([_RAW_FLAG]) + data)


def _decompress(data: bytes) -> bytes:
    return zlib.decompress(data[1:]) if data[0] == _COMPRESS_FLAG else data[1:]


# ── Adaptive Chunk Sizing ──────────────────────────────────────────

def _measure_connection_latency(host: str, port: int = 443, samples: int = 3) -> float:
    """Measure TCP connection latency in milliseconds.
    
    Returns median of multiple samples for stability.
    """
    latencies = []
    for _ in range(samples):
        try:
            t0 = time.perf_counter()
            with socket.create_connection((host, port), timeout=5) as s:
                s.close()
            latency_ms = (time.perf_counter() - t0) * 1000
            latencies.append(latency_ms)
        except Exception:
            latencies.append(500)  # Assume high latency on error
    
    latencies.sort()
    return latencies[len(latencies) // 2]  # median


def calculate_adaptive_chunk_size(latency_ms: float, file_size: int = 0) -> int:
    """Calculate optimal chunk size based on network latency.
    
    Lower latency → larger chunks (better throughput)
    Higher latency → smaller chunks (better responsiveness)
    
    The formula balances throughput vs. responsiveness.
    """
    if not CHUNK_SIZE_ADAPTIVE:
        return VPS_CHUNK_SIZE
    
    # Base calculation: target ~200ms per chunk round-trip
    # chunk_size ≈ (200ms / latency_ms) * base_chunk
    if latency_ms < 10:
        # Very low latency (local/fast): use max
        optimal = CHUNK_SIZE_MAX
    elif latency_ms > 300:
        # High latency: use min
        optimal = CHUNK_SIZE_MIN
    else:
        # Scale linearly between min and max
        # latency 10ms → max, latency 300ms → min
        ratio = (300 - latency_ms) / (300 - 10)
        optimal = int(CHUNK_SIZE_MIN + ratio * (CHUNK_SIZE_MAX - CHUNK_SIZE_MIN))
    
    # Align to 64KB boundaries for efficient I/O
    optimal = (optimal // (64 * 1024)) * (64 * 1024)
    
    # Clamp to configured bounds
    return max(CHUNK_SIZE_MIN, min(CHUNK_SIZE_MAX, optimal))


# ── Parallel Chunk Processing ──────────────────────────────────────

# Number of chunks to read ahead (bounded queue prevents memory bloat)
CHUNK_PIPELINE_SIZE = 4


class ChunkPipeline:
    """Pipeline for parallel read → compress/encrypt → send operations.
    
    Uses a bounded queue to read chunks ahead while previous chunks
    are being compressed, encrypted, and sent. This overlaps I/O with
    CPU work for better throughput on multi-core systems.
    """
    
    def __init__(
        self,
        filepath: Path,
        chunk_size: int,
        crypto,  # CryptoSession
        skip_chunks: set,
        cancelled_flag: threading.Event,
        connection_lost_flag: threading.Event,
    ):
        self._filepath = filepath
        self._chunk_size = chunk_size
        self._crypto = crypto
        self._skip_chunks = skip_chunks
        self._cancelled = cancelled_flag
        self._connection_lost = connection_lost_flag
        
        # Queue holds (seq, raw_chunk) tuples
        self._queue: queue.Queue = queue.Queue(maxsize=CHUNK_PIPELINE_SIZE)
        self._reader_thread: threading.Thread | None = None
        self._reader_error: Exception | None = None
        self._done = threading.Event()
    
    def start(self, total_chunks: int) -> None:
        """Start the reader thread."""
        self._done.clear()
        self._reader_error = None
        self._reader_thread = threading.Thread(
            target=self._reader_worker,
            args=(total_chunks,),
            daemon=True,
        )
        self._reader_thread.start()
    
    def _reader_worker(self, total_chunks: int) -> None:
        """Background thread that reads chunks into the queue."""
        try:
            with open(self._filepath, "rb") as f:
                for seq in range(total_chunks):
                    if self._cancelled.is_set() or self._connection_lost.is_set():
                        break
                    
                    if seq in self._skip_chunks:
                        f.seek((seq + 1) * self._chunk_size)
                        continue
                    
                    chunk = f.read(self._chunk_size)
                    if not chunk:
                        break
                    
                    # Block if queue is full (back-pressure)
                    while not (self._cancelled.is_set() or self._connection_lost.is_set()):
                        try:
                            self._queue.put((seq, chunk), timeout=0.1)
                            break
                        except queue.Full:
                            continue
        except Exception as e:
            self._reader_error = e
        finally:
            self._done.set()
    
    def get_next(self, timeout: float = 0.5) -> tuple[int, bytes] | None:
        """Get next (seq, chunk) from pipeline. Returns None when done."""
        while True:
            if self._cancelled.is_set() or self._connection_lost.is_set():
                return None
            
            try:
                return self._queue.get(timeout=timeout)
            except queue.Empty:
                if self._done.is_set() and self._queue.empty():
                    return None
                continue
    
    def is_done(self) -> bool:
        """Check if reader is done and queue is empty."""
        return self._done.is_set() and self._queue.empty()
    
    def get_error(self) -> Exception | None:
        """Get any error from reader thread."""
        return self._reader_error
    
    def stop(self) -> None:
        """Stop the pipeline."""
        self._cancelled.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _make_transfer_id(name: str, size: int, sha256: str) -> str:
    """Deterministic transfer ID from file metadata.

    Two independent sessions for the same file produce the same ID,
    enabling the receiver to detect a resumable partial download.
    """
    raw = f"{name}|{size}|{sha256}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


# ── Reconnect token ──────────────────────────────────────────────

def _make_reconnect_token(shared_key: bytes, session_code: str) -> str:
    """Derive a reconnect token from the DH shared key.

    Both peers compute the same token after key exchange.  On
    reconnect, including this token in the key-exchange message
    proves that the peer participated in the original session
    → verification popup can be safely skipped.
    """
    raw = hmac.new(
        shared_key,
        session_code.encode() + b"secureshare-reconnect-v1",
        hashlib.sha256,
    ).digest()[:16]
    return base64.b64encode(raw).decode()


# ── Resume manifest helpers ───────────────────────────────────────

def _manifest_path(save_dir: Path, file_name: str) -> Path:
    """Return the path to the .resume manifest for a given file."""
    return save_dir / (file_name + ".part" + RESUME_MANIFEST_EXT)


def _save_manifest(
    path: Path,
    transfer_id: str,
    file_name: str,
    file_size: int,
    file_sha256: str,
    chunk_size: int,
    total_chunks: int,
    received_chunks: set[int],
) -> None:
    """Persist the resume manifest to disk (atomic write)."""
    data = {
        "transfer_id":    transfer_id,
        "file_name":      file_name,
        "file_size":      file_size,
        "file_sha256":    file_sha256,
        "chunk_size":     chunk_size,
        "total_chunks":   total_chunks,
        "received_chunks": sorted(received_chunks),
        "timestamp":      time.time(),
    }
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp.replace(path)
    except Exception as exc:
        log.debug("Failed to save resume manifest: %s", exc)
        tmp.unlink(missing_ok=True)


def _load_manifest(
    save_dir: Path, file_name: str, transfer_id: str
) -> Optional[dict]:
    """Load a matching resume manifest if it exists and is still valid.

    Returns manifest dict with 'received_chunks' as a set, or None.
    """
    mpath = _manifest_path(save_dir, file_name)
    if not mpath.exists():
        return None
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        mpath.unlink(missing_ok=True)
        return None

    # Validate transfer_id and age
    if data.get("transfer_id") != transfer_id:
        log.info("Resume manifest transfer_id mismatch — ignoring")
        return None

    age = time.time() - data.get("timestamp", 0)
    if age > RESUME_MAX_AGE:
        log.info("Resume manifest too old (%.0f h) — ignoring", age / 3600)
        mpath.unlink(missing_ok=True)
        return None

    # Convert list → set for fast lookup
    data["received_chunks"] = set(data.get("received_chunks", []))
    return data


def _delete_manifest(save_dir: Path, file_name: str) -> None:
    """Remove the .resume manifest file."""
    mpath = _manifest_path(save_dir, file_name)
    mpath.unlink(missing_ok=True)


# ── Key Exchange (common for sender and receiver) ─────────────────

def _do_key_exchange(
    ws,
    session_code: str,
    on_status: Optional[StatusCB],
    reconnect_token: Optional[str] = None,
) -> tuple[Optional[CryptoSession], Optional[str]]:
    """
    Perform X25519 key exchange over the WebSocket with version negotiation.

    Both sides send their public key + protocol version simultaneously
    (signaling-encrypted).  The VPS relay pipes A→B and B→A, so each
    side receives the other's key.

    If reconnect_token is provided, it is included in the signaling
    message so the peer can verify the reconnect without a popup.

    Returns (CryptoSession, peer_reconnect_token) or (None, None).
    """
    crypto = CryptoSession(session_code)
    sig_key = derive_signaling_key(session_code)

    # Send our public key + version info + optional reconnect token
    pub_key_b64 = base64.b64encode(crypto.get_public_key_bytes()).decode()
    msg: dict = {
        "type":             "pub_key",
        "key":              pub_key_b64,
        "protocol_version": PROTOCOL_VERSION,
        "app_version":      APP_VERSION,
    }
    if reconnect_token:
        msg["reconnect_token"] = reconnect_token

    sig_payload = json.dumps(msg).encode()
    ws.send_binary(bytes([_SIG]) + signaling_encrypt(sig_key, sig_payload))

    if on_status:
        on_status("🔑 Key exchange...")

    # Receive peer's public key (blocks until peer connects + sends)
    try:
        raw = ws.recv()
    except Exception as e:
        if on_status:
            on_status(f"❌ Key exchange error: {e}")
        return None, None

    if not raw or not isinstance(raw, bytes) or len(raw) < 2 or raw[0] != _SIG:
        if on_status:
            on_status("❌ Invalid key exchange format")
        return None, None

    try:
        peer_msg = json.loads(signaling_decrypt(sig_key, raw[1:]))
    except Exception:
        if on_status:
            on_status("❌ Failed to decrypt peer's key")
        return None, None

    if peer_msg.get("type") != "pub_key" or "key" not in peer_msg:
        if on_status:
            on_status("❌ Invalid key exchange message")
        return None, None

    # ── Version compatibility check ─────────────────────────────
    peer_proto = peer_msg.get("protocol_version", 0)
    peer_app   = peer_msg.get("app_version", "unknown")

    log.info(
        "Version negotiation: us=proto%d/app%s, peer=proto%d/app%s",
        PROTOCOL_VERSION, APP_VERSION, peer_proto, peer_app,
    )
    if on_status:
        on_status(f"🔗 Protocol: v{PROTOCOL_VERSION} ↔ v{peer_proto} (app {APP_VERSION} ↔ {peer_app})")

    if peer_proto < MIN_PROTOCOL_VERSION:
        if on_status:
            on_status(f"❌ Incompatible peer version (protocol v{peer_proto}, need v{MIN_PROTOCOL_VERSION}+). Ask the peer to update.")
        return None, None

    if PROTOCOL_VERSION < peer_proto:
        # Peer requires a newer protocol — we might be too old
        log.warning(
            "Peer has newer protocol version (%d > %d). "
            "Consider updating the app.",
            peer_proto, PROTOCOL_VERSION,
        )
        if on_status:
            on_status(f"⚠️ Peer has a newer version (v{peer_app}). We recommend updating.")

    # ── Derive shared key ───────────────────────────────────────
    peer_pub_key = base64.b64decode(peer_msg["key"])
    crypto.derive_shared_key(peer_pub_key)

    peer_reconnect_token = peer_msg.get("reconnect_token")
    return crypto, peer_reconnect_token


def _do_verification(
    ws,
    crypto: CryptoSession,
    sig_key: bytes,
    on_verify: VerifyCB,
    on_status: Optional[StatusCB],
    auto_verify: bool = False,
) -> bool:
    """
    Show verification code and exchange confirmation with peer.

    If auto_verify is True (reconnect scenario), skip the user popup
    and auto-confirm.  Both sides still exchange 'verified' messages.

    Returns True if both sides verified successfully.
    """
    verification_code = crypto.get_verification_code()

    if auto_verify:
        if on_status:
            on_status("🔄 Auto-verification (reconnect)")
        # Send confirmation without user interaction
        confirm_payload = json.dumps({"type": "verified"}).encode()
        ws.send_binary(bytes([_SIG]) + signaling_encrypt(sig_key, confirm_payload))

        try:
            raw = ws.recv()
        except Exception as e:
            if on_status:
                on_status(f"❌ Auto-verification error: {e}")
            return False

        if not raw or not isinstance(raw, bytes) or len(raw) < 2 or raw[0] != _SIG:
            return False

        try:
            peer_msg = json.loads(signaling_decrypt(sig_key, raw[1:]))
        except Exception:
            return False

        if peer_msg.get("type") == "verified":
            if on_status:
                on_status("✅ Auto-verification confirmed")
            return True
        return False

    # ── Normal verification (user interaction) ────────────────
    if on_status:
        on_status(f"🔑 Verification code: {verification_code}")

    # Ask user to verify
    if not on_verify(verification_code):
        # User rejected — notify peer
        reject_payload = json.dumps({"type": "verify_reject"}).encode()
        try:
            ws.send_binary(bytes([_SIG]) + signaling_encrypt(sig_key, reject_payload))
        except Exception:
            pass
        if on_status:
            on_status("❌ Verification rejected")
        return False

    # Send verification confirmation
    confirm_payload = json.dumps({"type": "verified"}).encode()
    ws.send_binary(bytes([_SIG]) + signaling_encrypt(sig_key, confirm_payload))

    if on_status:
        on_status("✅ Verification confirmed, waiting for peer's confirmation...")

    # Wait for peer's verification
    try:
        raw = ws.recv()
    except Exception as e:
        if on_status:
            on_status(f"❌ Verification error: {e}")
        return False

    if not raw or not isinstance(raw, bytes) or len(raw) < 2 or raw[0] != _SIG:
        if on_status:
            on_status("❌ Invalid verification format")
        return False

    try:
        peer_msg = json.loads(signaling_decrypt(sig_key, raw[1:]))
    except Exception:
        if on_status:
            on_status("❌ Verification decryption error")
        return False

    if peer_msg.get("type") == "verify_reject":
        if on_status:
            on_status("❌ Peer rejected verification")
        return False

    if peer_msg.get("type") != "verified":
        if on_status:
            on_status("❌ Invalid verification message")
        return False

    if on_status:
        on_status("✅ Both sides confirmed verification")

    return True


# ════════════════════════════════════════════════════════════════════
#  VPSRelaySender
# ════════════════════════════════════════════════════════════════════

class VPSRelaySender:
    """
    Send a file through the VPS relay server.

    Handles the entire flow: connect → key exchange → verify → transfer.
    Supports auto-reconnect on connection loss during transfer.
    GUI only needs to provide callbacks for progress, status, and verification.
    """

    def __init__(
        self,
        session_code: str,
        filepath: str | Path,
        on_progress: Optional[ProgressCB] = None,
        on_status:   Optional[StatusCB]   = None,
        on_verify:   Optional[VerifyCB]   = None,
    ):
        self._code       = session_code
        self._filepath   = Path(filepath)
        self.on_progress = on_progress
        self.on_status   = on_status
        self.on_verify   = on_verify or (lambda code: True)
        self._cancelled  = False
        self._ws: Optional[websocket.WebSocket] = None
        self._crypto: Optional[CryptoSession] = None
        self._ctl_queue: queue.Queue = queue.Queue()
        self._connection_lost = threading.Event()

        # Cached file metadata (computed once, reused across reconnects)
        self._file_hash: Optional[str] = None
        self._transfer_id: Optional[str] = None
        self._reconnect_token: Optional[str] = None
        self._chunk_size: int = VPS_CHUNK_SIZE  # May be updated by adaptive sizing

    def cancel(self) -> None:
        self._cancelled = True
        self._connection_lost.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ── Public entry point (with auto-reconnect) ──────────────────

    def send(self) -> bool:
        """
        Connect to VPS, perform key exchange + verification, send file.
        Auto-reconnects on connection loss (up to RECONNECT_MAX_RETRIES).
        Returns True on success, False on failure/cancel.
        """
        if not _HAS_WS:
            self._log("❌ websocket-client package required")
            return False

        # Pre-compute file metadata once (expensive for large files)
        try:
            file_name = self._filepath.name
            file_size = self._filepath.stat().st_size
            self._log(f"🔍 Computing hash for {file_name}...")
            self._file_hash = _sha256_file(self._filepath)
            self._transfer_id = _make_transfer_id(
                file_name, file_size, self._file_hash
            )
        except Exception as exc:
            self._log(f"❌ File read error: {exc}")
            return False

        for attempt in range(RECONNECT_MAX_RETRIES + 1):
            if self._cancelled:
                return False

            if attempt > 0:
                delay = min(
                    RECONNECT_BASE_DELAY * 2 ** (attempt - 1),
                    RECONNECT_MAX_DELAY,
                )
                self._log(f"🔄 Reconnecting in {delay:.0f}s (attempt {attempt}/{RECONNECT_MAX_RETRIES})...")
                time.sleep(delay)
                if self._cancelled:
                    return False

            self._connection_lost.clear()
            self._ctl_queue = queue.Queue()

            try:
                result = self._send_attempt(is_reconnect=(attempt > 0))
                if result is True:
                    return True
                if result is False:
                    # Permanent failure (cancel, verification rejected, etc.)
                    return False
                # result is None → connection lost, retry
                self._log("⚠️ Connection lost")
            except Exception as exc:
                self._log(f"❌ Error: {exc}")
                log.exception("VPSRelaySender error")
            finally:
                self._close()

        self._log("❌ Reconnection attempts exhausted")
        return False

    def _send_attempt(self, is_reconnect: bool = False) -> Optional[bool]:
        """Single send attempt.

        Returns:
          True  — success
          False — permanent failure (don't retry)
          None  — connection lost (can retry)
        """
        # ── 1. Connect to VPS ─────────────────────────────────────
        if is_reconnect:
            self._log("🌐 Reconnecting to relay server...")
        else:
            self._log("🌐 Connecting to relay server...")
        
        # Verify certificate pinning before connecting
        try:
            parsed = urlparse(VPS_RELAY_URL)
            host = parsed.hostname or "secureshare-relay.duckdns.org"
            port = parsed.port or 443
            _verify_cert_pinning(host, port)
        except CertificatePinningError as e:
            self._log(f"❌ Security error: {e}")
            return False  # Permanent failure — don't retry with bad cert
        
        try:
            self._ws = websocket.WebSocket(sslopt={"ssl_context": _create_pinned_ssl_context()})
            self._ws.connect(VPS_RELAY_URL, timeout=30)
            self._ws.settimeout(300)       # 5 min to wait for peer
            self._ws.send(self._code)      # register session code
        except Exception as exc:
            self._log(f"❌ Relay connection error: {exc}")
            # DNS failures are transient — always allow retry
            if _is_dns_error(exc):
                return None
            return None if is_reconnect else False

        self._log("⏳ Waiting for receiver...")

        # ── 2. Key exchange ───────────────────────────────────────
        self._crypto, peer_token = _do_key_exchange(
            self._ws, self._code, self.on_status,
            reconnect_token=self._reconnect_token,
        )
        if not self._crypto:
            return None if is_reconnect else False

        # Compute reconnect token from NEW shared key
        new_token = _make_reconnect_token(
            self._crypto.get_shared_key(), self._code
        )

        # ── 3. Verification ───────────────────────────────────────
        sig_key = derive_signaling_key(self._code)
        self._ws.settimeout(120)

        # Auto-verify on reconnect if peer sent a matching token
        # (timing-safe comparison to prevent side-channel leaks)
        auto_verify = (
            is_reconnect
            and self._reconnect_token is not None
            and peer_token is not None
            and hmac.compare_digest(peer_token, self._reconnect_token)
        )

        if not _do_verification(
            self._ws, self._crypto, sig_key,
            self.on_verify, self.on_status,
            auto_verify=auto_verify,
        ):
            return False  # verification rejected = permanent failure

        # Save reconnect token (from the NEW key exchange)
        self._reconnect_token = new_token

        # ── 4. Start background receiver ──────────────────────────
        recv_thread = threading.Thread(target=self._recv_worker, daemon=True)
        recv_thread.start()

        # ── 5. Send metadata ──────────────────────────────────────
        # Calculate adaptive chunk size based on connection latency
        parsed = urlparse(VPS_RELAY_URL)
        host = parsed.hostname or "secureshare-relay.duckdns.org"
        if CHUNK_SIZE_ADAPTIVE:
            latency_ms = _measure_connection_latency(host, 443, samples=2)
            self._chunk_size = calculate_adaptive_chunk_size(latency_ms)
            self._log(f"📊 Adaptive chunk: {self._chunk_size // 1024} KB (latency: {latency_ms:.0f} ms)")
        else:
            self._chunk_size = VPS_CHUNK_SIZE
        
        file_name    = self._filepath.name
        file_size    = self._filepath.stat().st_size
        total_chunks = (file_size + self._chunk_size - 1) // self._chunk_size

        self._send_ctl(json.dumps({
            "type":         "relay_meta",
            "name":         file_name,
            "size":         file_size,
            "sha256":       self._file_hash,
            "chunk_size":   self._chunk_size,
            "total_chunks": total_chunks,
            "transfer_id":  self._transfer_id,
        }).encode())

        # Wait for meta ACK (may include resume info)
        self._log("⏳ Waiting for metadata confirmation...")
        try:
            ack = self._ctl_queue.get(timeout=120)
        except queue.Empty:
            self._log("❌ Metadata timeout")
            return None  # retryable
        if ack.get("type") != "relay_meta_ack":
            self._log("❌ Unexpected metadata response")
            return None

        # ── 5b. Check if receiver requests resume ─────────────────
        skip_chunks: set[int] = set()
        resume_bytes = 0
        if ack.get("resume"):
            already = ack.get("received_chunks", [])
            skip_chunks = set(already)
            resume_bytes = len(skip_chunks) * self._chunk_size
            if total_chunks - 1 in skip_chunks:
                last_chunk_actual = file_size - (total_chunks - 1) * self._chunk_size
                resume_bytes = resume_bytes - self._chunk_size + last_chunk_actual
            resume_bytes = min(resume_bytes, file_size)
            self._log(f"🔄 Resuming: receiver has {len(skip_chunks)}/{total_chunks} chunks ({resume_bytes / (1024**2):.1f} MB)")

        # ── 6. Send file chunks (pipelined) ──────────────────────────
        from .gui import _human_size
        size_str = _human_size(file_size)
        chunks_to_send = total_chunks - len(skip_chunks)
        if skip_chunks:
            self._log(f"📦 Sending: {file_name} ({size_str}) — {chunks_to_send} chunks remaining")
        else:
            self._log(f"📦 Sending: {file_name} ({size_str})")

        t0 = time.monotonic()
        sent_bytes = resume_bytes
        last_prog  = t0

        if self.on_progress and resume_bytes > 0:
            self.on_progress(sent_bytes, file_size, 0)

        # Use pipelined chunk reading for better throughput
        pipeline = ChunkPipeline(
            filepath=self._filepath,
            chunk_size=self._chunk_size,
            crypto=self._crypto,
            skip_chunks=skip_chunks,
            cancelled_flag=threading.Event() if not hasattr(self, '_cancel_event') else self._cancel_event,
            connection_lost_flag=self._connection_lost,
        )
        # Set cancelled flag based on self._cancelled
        if self._cancelled:
            pipeline._cancelled.set()
        
        pipeline.start(total_chunks)
        
        try:
            while True:
                if self._cancelled:
                    pipeline.stop()
                    return False
                if self._connection_lost.is_set():
                    pipeline.stop()
                    return None
                
                result = pipeline.get_next()
                if result is None:
                    if pipeline.get_error():
                        log.error("Pipeline read error: %s", pipeline.get_error())
                    break
                
                seq, chunk = result
                self._send_dat(seq, chunk)
                sent_bytes += len(chunk)

                now = time.monotonic()
                if self.on_progress and (now - last_prog >= 0.3):
                    elapsed = now - t0
                    speed = (sent_bytes - resume_bytes) / elapsed if elapsed > 0 else 0
                    self.on_progress(sent_bytes, file_size, speed)
                    last_prog = now
        finally:
            pipeline.stop()

        # Final progress
        if self.on_progress:
            elapsed = time.monotonic() - t0
            speed = (sent_bytes - resume_bytes) / elapsed if elapsed > 0 else 0
            self.on_progress(sent_bytes, file_size, speed)

        # ── 7. Send DONE and wait for verification ────────────────
        done_payload = json.dumps({
            "type":         "relay_done",
            "sha256":       self._file_hash,
            "total_chunks": total_chunks,
        }).encode()
        self._send_ctl(done_payload)
        self._log("⏳ Waiting for integrity confirmation...")

        retransmit_rounds = 0
        deadline = time.monotonic() + 600

        while time.monotonic() < deadline and not self._cancelled:
            if self._connection_lost.is_set():
                return None  # connection lost → retry

            try:
                msg = self._ctl_queue.get(timeout=10)
            except queue.Empty:
                if self._connection_lost.is_set():
                    return None
                self._send_ctl(done_payload)
                continue

            if msg.get("type") == "relay_done_ack":
                ok = msg.get("verified", False)
                if ok:
                    self._log("🎉 File transferred and verified ✓")
                else:
                    self._log("⚠ Hash mismatch on receiver side")
                return ok

            elif msg.get("type") == "relay_retransmit" and retransmit_rounds < 5:
                missing = msg.get("missing", [])
                if not missing:
                    continue
                retransmit_rounds += 1
                self._log(f"🔄 Retransmitting {len(missing)} chunks (round {retransmit_rounds})...")
                with open(self._filepath, "rb") as f:
                    for seq_i in missing:
                        if self._cancelled:
                            return False
                        if self._connection_lost.is_set():
                            return None
                        f.seek(seq_i * self._chunk_size)
                        chunk = f.read(self._chunk_size)
                        if chunk:
                            self._send_dat(seq_i, chunk)
                self._send_ctl(done_payload)

        self._log("❌ Integrity confirmation timeout")
        return None  # retryable (might be connection issue)

    # ── Send helpers ───────────────────────────────────────────────

    def _send_ctl(self, plaintext: bytes) -> None:
        try:
            self._ws.send_binary(bytes([_CTL]) + self._crypto.encrypt(plaintext))
        except Exception as exc:
            log.debug("VPS send ctl error: %s", exc)
            self._connection_lost.set()

    def _send_dat(self, seq: int, chunk: bytes) -> None:
        try:
            payload = _compress(chunk)
            payload = self._crypto.encrypt(payload)
            frame   = bytes([_DAT]) + struct.pack("!I", seq) + payload
            self._ws.send_binary(frame)
        except Exception as exc:
            log.debug("VPS send dat error: %s", exc)
            self._connection_lost.set()

    def _recv_worker(self) -> None:
        """Receive control frames from the receiver (runs in background)."""
        try:
            while True:
                raw = self._ws.recv()
                if not raw:
                    break
                if isinstance(raw, bytes) and len(raw) >= 1 and raw[0] == _CTL:
                    try:
                        msg = json.loads(self._crypto.decrypt(raw[1:]))
                        self._ctl_queue.put(msg)
                    except Exception as exc:
                        log.debug("VPS recv ctl decode error: %s", exc)
        except Exception:
            pass
        finally:
            self._connection_lost.set()

    def _close(self) -> None:
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        log.info("[Sender] %s", msg)
        if self.on_status:
            self.on_status(msg)


# ════════════════════════════════════════════════════════════════════
#  VPSRelayReceiver
# ════════════════════════════════════════════════════════════════════

class VPSRelayReceiver:
    """
    Receive a file through the VPS relay server.

    Handles the entire flow: connect → key exchange → verify → receive.
    Supports auto-reconnect on connection loss during transfer.
    GUI only needs to provide callbacks for progress, status, and verification.
    """

    def __init__(
        self,
        session_code: str,
        save_dir: str | Path,
        on_progress: Optional[ProgressCB] = None,
        on_status:   Optional[StatusCB]   = None,
        on_verify:   Optional[VerifyCB]   = None,
    ):
        self._code       = session_code
        self._save_dir   = Path(save_dir)
        self.on_progress = on_progress
        self.on_status   = on_status
        self.on_verify   = on_verify or (lambda code: True)
        self._cancelled  = False
        self._ws: Optional[websocket.WebSocket] = None
        self._crypto: Optional[CryptoSession] = None

        self._reconnect_token: Optional[str] = None
        self._retryable = False  # set to True on connection-level errors

    def cancel(self) -> None:
        self._cancelled = True
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ── Public entry point (with auto-reconnect) ──────────────────

    def receive(self) -> Optional[Path]:
        """
        Connect to VPS, perform key exchange + verification, receive file.
        Auto-reconnects on connection loss (up to RECONNECT_MAX_RETRIES).
        Returns Path to saved file on success, None on failure/cancel.
        """
        if not _HAS_WS:
            self._log("❌ websocket-client package required")
            return None

        for attempt in range(RECONNECT_MAX_RETRIES + 1):
            if self._cancelled:
                return None

            if attempt > 0:
                delay = min(
                    RECONNECT_BASE_DELAY * 2 ** (attempt - 1),
                    RECONNECT_MAX_DELAY,
                )
                self._log(f"🔄 Reconnecting in {delay:.0f}s (attempt {attempt}/{RECONNECT_MAX_RETRIES})...")
                time.sleep(delay)
                if self._cancelled:
                    return None

            self._retryable = False

            try:
                result = self._receive_attempt(is_reconnect=(attempt > 0))
                if result is not None:
                    return result  # success (Path)
                if not self._retryable or self._cancelled:
                    return None   # permanent failure
                # retryable → continue loop
                self._log("⚠️ Connection lost")
            except Exception as exc:
                self._log(f"❌ Error: {exc}")
                log.exception("VPSRelayReceiver error")
            finally:
                self._close()

        self._log("❌ Reconnection attempts exhausted")
        return None

    def _receive_attempt(self, is_reconnect: bool = False) -> Optional[Path]:
        """Single receive attempt.

        Returns Path on success, None on failure.
        Sets self._retryable = True if the failure is connection-related.
        """
        # ── 1. Connect to VPS ─────────────────────────────────────
        if is_reconnect:
            self._log("🌐 Reconnecting to relay server...")
        else:
            self._log("🌐 Connecting to relay server...")
        
        # Verify certificate pinning before connecting
        try:
            parsed = urlparse(VPS_RELAY_URL)
            host = parsed.hostname or "secureshare-relay.duckdns.org"
            port = parsed.port or 443
            _verify_cert_pinning(host, port)
        except CertificatePinningError as e:
            self._log(f"❌ Security error: {e}")
            self._retryable = False  # Permanent failure — don't retry with bad cert
            return None
        
        try:
            self._ws = websocket.WebSocket(sslopt={"ssl_context": _create_pinned_ssl_context()})
            self._ws.connect(VPS_RELAY_URL, timeout=30)
            self._ws.settimeout(300)
            self._ws.send(self._code)
        except Exception as exc:
            self._log(f"❌ Relay connection error: {exc}")
            # DNS failures are transient — always allow retry
            self._retryable = is_reconnect or _is_dns_error(exc)
            return None

        self._log("⏳ Waiting for sender...")

        # ── 2. Key exchange ───────────────────────────────────────
        self._crypto, peer_token = _do_key_exchange(
            self._ws, self._code, self.on_status,
            reconnect_token=self._reconnect_token,
        )
        if not self._crypto:
            self._retryable = is_reconnect
            return None

        new_token = _make_reconnect_token(
            self._crypto.get_shared_key(), self._code
        )

        # ── 3. Verification ───────────────────────────────────────
        sig_key = derive_signaling_key(self._code)
        self._ws.settimeout(120)

        # Timing-safe comparison to prevent side-channel leaks
        auto_verify = (
            is_reconnect
            and self._reconnect_token is not None
            and peer_token is not None
            and hmac.compare_digest(peer_token, self._reconnect_token)
        )

        if not _do_verification(
            self._ws, self._crypto, sig_key,
            self.on_verify, self.on_status,
            auto_verify=auto_verify,
        ):
            return None  # permanent failure (verification rejected)

        self._reconnect_token = new_token

        # ── 4. Receive file ───────────────────────────────────────
        self._log("⏳ Waiting for metadata from sender...")
        self._ws.settimeout(120)

        file_name:      Optional[str]  = None
        file_size:      int            = 0
        file_hash:      str            = ""
        transfer_id:    str            = ""
        chunk_size:     int            = VPS_CHUNK_SIZE
        total_chunks:   int            = 0
        received_seqs:  set[int]       = set()
        bytes_received: int            = 0
        save_path:      Optional[Path] = None
        temp_path:      Optional[Path] = None
        out_file                       = None
        is_resume:      bool           = False
        chunks_since_save: int         = 0
        t0 = time.monotonic()
        last_prog = t0

        # Async disk writer (keeps receive loop fast)
        write_queue: queue.Queue = queue.Queue(maxsize=512)
        writer_thread: Optional[threading.Thread] = None

        def _writer() -> None:
            writes = 0
            while True:
                item = write_queue.get()
                if item is None:
                    if out_file and not out_file.closed:
                        try:
                            out_file.flush()
                        except Exception:
                            pass
                    write_queue.task_done()
                    break
                s, data = item
                try:
                    out_file.seek(s * chunk_size)
                    out_file.write(data)
                    writes += 1
                    if writes % 128 == 0:
                        out_file.flush()
                except Exception:
                    pass
                write_queue.task_done()

        try:
            while not self._cancelled:
                try:
                    raw = self._ws.recv()
                except Exception:
                    # Connection lost during transfer → retryable
                    if file_name and received_seqs and not self._cancelled:
                        self._retryable = True
                    break

                if not raw or not isinstance(raw, bytes):
                    continue

                msg_type = raw[0]

                # ── Control frame ──────────────────────────────────
                if msg_type == _CTL:
                    try:
                        msg = json.loads(self._crypto.decrypt(raw[1:]))
                    except Exception:
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "relay_meta":
                        raw_name     = msg["name"]
                        file_size    = msg["size"]
                        file_hash    = msg["sha256"]
                        chunk_size   = msg.get("chunk_size", VPS_CHUNK_SIZE)
                        total_chunks = msg.get("total_chunks", 0)
                        transfer_id  = msg.get("transfer_id", "")

                        # ── Security: sanitize file name (path traversal) ─
                        file_name = Path(raw_name).name  # strip dirs
                        if (
                            not file_name
                            or file_name in (".", "..")
                            or "\x00" in file_name
                        ):
                            self._log("❌ Unsafe filename from sender")
                            return None
                        # Defense-in-depth: verify resolved path stays in save_dir
                        _resolved = (self._save_dir / file_name).resolve()
                        if not str(_resolved).startswith(
                            str(self._save_dir.resolve())
                        ):
                            self._log("❌ Unsafe filename (path traversal)")
                            return None

                        # ── Security: validate file size ──────────────────
                        if not isinstance(file_size, int) or file_size <= 0:
                            self._log("❌ Invalid file size")
                            return None
                        if file_size > VPS_MAX_FILE_SIZE:
                            self._log(f"❌ File size ({file_size / (1024**3):.1f} GB) exceeds limit ({VPS_MAX_FILE_SIZE / (1024**3):.0f} GB)")
                            return None

                        # ── Security: validate chunk_size / total_chunks ──
                        if not isinstance(chunk_size, int) or chunk_size <= 0:
                            chunk_size = VPS_CHUNK_SIZE
                        if chunk_size > 4 * 1024 * 1024:  # max 4 MB
                            chunk_size = VPS_CHUNK_SIZE
                        expected_chunks = (file_size + chunk_size - 1) // chunk_size
                        if total_chunks != expected_chunks:
                            log.warning(
                                "total_chunks mismatch: got %d, expected %d",
                                total_chunks, expected_chunks,
                            )
                            total_chunks = expected_chunks

                        save_path = self._save_dir / file_name
                        temp_path = save_path.with_suffix(save_path.suffix + ".part")

                        # ── Resume detection ──────────────────────
                        manifest = None
                        if transfer_id:
                            manifest = _load_manifest(
                                self._save_dir, file_name, transfer_id
                            )

                        if (
                            manifest
                            and temp_path.exists()
                            and manifest.get("chunk_size") == chunk_size
                            and manifest.get("total_chunks") == total_chunks
                        ):
                            is_resume = True
                            received_seqs = manifest["received_chunks"]
                            bytes_received = len(received_seqs) * chunk_size
                            if total_chunks - 1 in received_seqs:
                                last_sz = file_size - (total_chunks - 1) * chunk_size
                                bytes_received = bytes_received - chunk_size + last_sz
                            bytes_received = min(bytes_received, file_size)

                            try:
                                out_file = open(temp_path, "r+b")
                            except Exception as exc:
                                self._log(f"❌ Could not open .part file: {exc}")
                                is_resume = False
                                received_seqs = set()
                                bytes_received = 0

                            if is_resume:
                                self._log(f"🔄 Resuming: found {len(received_seqs)}/{total_chunks} chunks ({bytes_received / (1024**2):.1f} MB)")

                        if not is_resume:
                            received_seqs = set()
                            bytes_received = 0
                            try:
                                out_file = open(temp_path, "w+b")
                                if file_size > 0:
                                    out_file.seek(file_size - 1)
                                    out_file.write(b"\x00")
                                    out_file.flush()
                                    out_file.seek(0)
                            except Exception as exc:
                                self._log(f"❌ Could not create file: {exc}")
                                return None

                        writer_thread = threading.Thread(
                            target=_writer, daemon=True, name="vps-relay-writer"
                        )
                        writer_thread.start()

                        from .gui import _human_size
                        size_str = _human_size(file_size)
                        if is_resume:
                            pct = bytes_received / file_size * 100 if file_size else 0
                            self._log(f"📥 Resuming: {file_name} ({size_str}) — {pct:.0f}% already received")
                        else:
                            self._log(f"📥 Receiving: {file_name} ({size_str})")

                        ack_msg: dict = {"type": "relay_meta_ack"}
                        if is_resume and received_seqs:
                            ack_msg["resume"] = True
                            ack_msg["received_chunks"] = sorted(received_seqs)

                        self._send_ctl(json.dumps(ack_msg).encode())
                        t0 = time.monotonic()
                        last_prog = t0

                        if self.on_progress and is_resume:
                            self.on_progress(bytes_received, file_size, 0)

                    elif msg_type == "relay_done":
                        total_chunks = msg.get("total_chunks", total_chunks)
                        file_hash    = msg.get("sha256", file_hash)

                        missing = sorted(set(range(total_chunks)) - received_seqs)

                        if missing:
                            if file_name and transfer_id:
                                _save_manifest(
                                    _manifest_path(self._save_dir, file_name),
                                    transfer_id, file_name, file_size,
                                    file_hash, chunk_size, total_chunks,
                                    received_seqs,
                                )
                            BATCH = 1000
                            for i in range(0, len(missing), BATCH):
                                batch = missing[i: i + BATCH]
                                self._send_ctl(json.dumps({
                                    "type":    "relay_retransmit",
                                    "missing": batch,
                                }).encode())
                            self._log(f"🔄 Requesting retransmission of {len(missing)} chunks...")

                        else:
                            write_queue.put(None)
                            write_queue.join()
                            if writer_thread:
                                writer_thread.join(timeout=30)

                            try:
                                out_file.close()
                            except Exception:
                                pass

                            self._log("🔍 Verifying SHA-256...")
                            verified = _sha256_file(temp_path) == file_hash

                            self._send_ctl(json.dumps({
                                "type":     "relay_done_ack",
                                "verified": verified,
                            }).encode())
                            time.sleep(1)

                            if verified:
                                _delete_manifest(self._save_dir, file_name)
                                if save_path.exists():
                                    save_path.unlink()
                                temp_path.rename(save_path)
                                elapsed = time.monotonic() - t0
                                avg = file_size / elapsed if elapsed > 0 else 0
                                self._log(f"✅ Saved: {save_path.name} ({avg / (1024*1024):.1f} MB/s)")
                                return save_path
                            else:
                                self._log("❌ Hash mismatch!")
                                _delete_manifest(self._save_dir, file_name)
                                temp_path.unlink(missing_ok=True)
                                return None

                # ── Data frame ─────────────────────────────────────
                elif msg_type == _DAT and file_name:
                    if len(raw) < 5:
                        continue
                    seq      = struct.unpack_from("!I", raw, 1)[0]
                    enc_data = raw[5:]

                    if seq not in received_seqs:
                        try:
                            chunk = _decompress(self._crypto.decrypt(enc_data))
                            received_seqs.add(seq)
                            bytes_received += len(chunk)
                            chunks_since_save += 1
                            try:
                                write_queue.put_nowait((seq, chunk))
                            except queue.Full:
                                received_seqs.discard(seq)
                                bytes_received -= len(chunk)
                                chunks_since_save -= 1
                        except Exception:
                            pass

                    if (
                        chunks_since_save >= RESUME_SAVE_INTERVAL
                        and file_name and transfer_id
                    ):
                        _save_manifest(
                            _manifest_path(self._save_dir, file_name),
                            transfer_id, file_name, file_size,
                            file_hash, chunk_size, total_chunks,
                            received_seqs,
                        )
                        chunks_since_save = 0

                    now = time.monotonic()
                    if self.on_progress and file_size and (now - last_prog >= 0.5):
                        elapsed = now - t0
                        self.on_progress(
                            bytes_received, file_size,
                            bytes_received / elapsed if elapsed > 0 else 0,
                        )
                        last_prog = now

        except Exception as exc:
            self._log(f"❌ Error: {exc}")
            log.exception("VPSRelayReceiver error")
            if file_name and received_seqs:
                self._retryable = True
        finally:
            try:
                write_queue.put(None)
            except Exception:
                pass
            if writer_thread and writer_thread.is_alive():
                writer_thread.join(timeout=10)
            if out_file:
                try:
                    out_file.close()
                except Exception:
                    pass

            # Save resume manifest on interruption
            if (
                file_name and transfer_id and received_seqs
                and len(received_seqs) < total_chunks
            ):
                self._log(f"💾 Progress saved: {len(received_seqs)}/{total_chunks} chunks — can be resumed")
                _save_manifest(
                    _manifest_path(self._save_dir, file_name),
                    transfer_id, file_name, file_size,
                    file_hash, chunk_size, total_chunks,
                    received_seqs,
                )

        return None

    # ── Send helper ────────────────────────────────────────────────

    def _send_ctl(self, plaintext: bytes) -> None:
        try:
            self._ws.send_binary(bytes([_CTL]) + self._crypto.encrypt(plaintext))
        except Exception as exc:
            log.debug("VPS recv-side send ctl: %s", exc)

    def _close(self) -> None:
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        log.info("[Receiver] %s", msg)
        if self.on_status:
            self.on_status(msg)
