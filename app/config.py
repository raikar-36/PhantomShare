"""
PhantomShare — configuration constants.

VPS-only architecture for secure end-to-end encrypted file sharing.
"""

# ── VPS Relay Server ──────────────────────────────────────────────
VPS_RELAY_URL = "wss://secureshare-relay.duckdns.org"
VPS_MAX_FILE_SIZE = 5 * 1024**3        # 5 GiB — server session limit
VPS_CHUNK_SIZE = 512 * 1024            # 512 KB per WebSocket chunk

# ── Certificate Pinning ───────────────────────────────────────────
# SHA-256 fingerprints of trusted relay server certificates.
# Multiple fingerprints allow for certificate rotation.
# To get a certificate fingerprint:
#   openssl s_client -connect host:443 | openssl x509 -outform DER | sha256sum
VPS_CERT_FINGERPRINTS = [
    "7b0688cfaa5ff53f53940f30b706d26ce4decdc0cac96f96baf09209f132caf3",
]
CERT_PINNING_ENABLED = True  # Set to False to disable pinning (dev only)

# ── Protocol Version ──────────────────────────────────────────────
PROTOCOL_VERSION     = 1   # current wire-protocol version
MIN_PROTOCOL_VERSION = 1   # minimum compatible version (reject older)

# ── Session ────────────────────────────────────────────────────────
SESSION_CODE_LENGTH = 10  # 36^10 ≈ 3.6 quadrillion combinations

# ── Resume ─────────────────────────────────────────────────────────
RESUME_MANIFEST_EXT  = ".resume"          # manifest file extension
RESUME_MAX_AGE       = 7 * 24 * 3600      # 7 days — auto-cleanup
RESUME_SAVE_INTERVAL = 64                 # save manifest every N chunks

# ── Auto-reconnect ────────────────────────────────────────────────
RECONNECT_MAX_RETRIES = 5                 # max reconnect attempts
RECONNECT_BASE_DELAY  = 5                 # seconds (exponential backoff)
RECONNECT_MAX_DELAY   = 60                # seconds cap

# ── App ────────────────────────────────────────────────────────────
APP_NAME = "PhantomShare"
APP_VERSION = "1.0.0"

# ── Links ──────────────────────────────────────────────────────────
HOMEPAGE_URL = "https://secureshare-relay.duckdns.org"
GITHUB_URL = "https://github.com/artmarchenko/SecureShare"
