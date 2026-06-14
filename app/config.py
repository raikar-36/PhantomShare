"""
PhantomShare — configuration constants.

VPS-only architecture for secure end-to-end encrypted file sharing.

Configuration priority (highest to lowest):
  1. Environment variables (PHANTOMSHARE_*)
  2. Config file (~/.phantomshare/config.json)
  3. Default values below
"""

import json
import os
from pathlib import Path

# ── Configuration File ────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".phantomshare"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config_file() -> dict:
    """Load configuration from JSON file if it exists."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _get_config(key: str, default, env_prefix: str = "PHANTOMSHARE_"):
    """Get config value with environment variable override.
    
    Priority: env var > config file > default
    """
    # Check environment variable first
    env_key = f"{env_prefix}{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        # Type conversion based on default type
        if isinstance(default, bool):
            return env_val.lower() in ("true", "1", "yes")
        elif isinstance(default, int):
            return int(env_val)
        elif isinstance(default, float):
            return float(env_val)
        return env_val
    
    # Check config file
    config = _load_config_file()
    if key in config:
        return config[key]
    
    return default


# ── VPS Relay Server ──────────────────────────────────────────────
VPS_RELAY_URL = _get_config("relay_url", "wss://secureshare-relay.duckdns.org")
VPS_MAX_FILE_SIZE = _get_config("max_file_size", 5 * 1024**3)  # 5 GiB
VPS_CHUNK_SIZE = _get_config("chunk_size", 512 * 1024)  # 512 KB default

# ── Adaptive Chunk Sizing ─────────────────────────────────────────
CHUNK_SIZE_MIN = _get_config("chunk_size_min", 64 * 1024)  # 64 KB
CHUNK_SIZE_MAX = _get_config("chunk_size_max", 2 * 1024 * 1024)  # 2 MB
CHUNK_SIZE_ADAPTIVE = _get_config("chunk_size_adaptive", True)

# ── Certificate Pinning ───────────────────────────────────────────
# SHA-256 fingerprints of trusted relay server certificates.
# Multiple fingerprints allow for certificate rotation.
# To get a certificate fingerprint:
#   openssl s_client -connect host:443 | openssl x509 -outform DER | sha256sum
VPS_CERT_FINGERPRINTS = [
    "7b0688cfaa5ff53f53940f30b706d26ce4decdc0cac96f96baf09209f132caf3",
    "2bc91838b0ec99257faaf2e2ea7c4ad3cde3066b2205bbc82b299ff512d05a3c",
]
CERT_PINNING_ENABLED = _get_config("cert_pinning", True)

# ── Protocol Version ──────────────────────────────────────────────
PROTOCOL_VERSION     = 1   # current wire-protocol version
MIN_PROTOCOL_VERSION = 1   # minimum compatible version (reject older)

# ── Session ────────────────────────────────────────────────────────
SESSION_CODE_LENGTH = _get_config("session_code_length", 10)

# ── Resume ─────────────────────────────────────────────────────────
RESUME_MANIFEST_EXT  = ".resume"          # manifest file extension
RESUME_MAX_AGE       = _get_config("resume_max_age", 7 * 24 * 3600)  # 7 days
RESUME_SAVE_INTERVAL = 64                 # save manifest every N chunks

# ── Auto-reconnect ────────────────────────────────────────────────
RECONNECT_MAX_RETRIES = _get_config("reconnect_retries", 5)
RECONNECT_BASE_DELAY  = _get_config("reconnect_delay", 5)  # seconds
RECONNECT_MAX_DELAY   = 60                # seconds cap

# ── App ────────────────────────────────────────────────────────────
APP_NAME = "PhantomShare"
APP_VERSION = "1.0.0"

# ── Links ──────────────────────────────────────────────────────────
HOMEPAGE_URL = "https://secureshare-relay.duckdns.org"
GITHUB_URL = "https://github.com/artmarchenko/SecureShare"
