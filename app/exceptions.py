"""
PhantomShare — custom exception hierarchy.

Provides structured error handling with specific exception types
for different failure modes.
"""


class PhantomShareError(Exception):
    """Base exception for all PhantomShare errors."""
    pass


class CryptoError(PhantomShareError):
    """Encryption or decryption failed."""
    pass


class KeyExchangeError(CryptoError):
    """Key exchange failed during handshake."""
    pass


class VerificationError(CryptoError):
    """Peer verification failed (possible MITM)."""
    pass


class TransferError(PhantomShareError):
    """File transfer failed."""
    pass


class TransferCancelledError(TransferError):
    """Transfer was cancelled by user."""
    pass


class IntegrityError(TransferError):
    """File integrity check failed (hash mismatch)."""
    pass


class ResumeError(TransferError):
    """Could not resume partial transfer."""
    pass


class NetworkError(PhantomShareError):
    """Network or connection error."""
    pass


class ConnectionLostError(NetworkError):
    """Connection was lost during transfer."""
    pass


class RelayServerError(NetworkError):
    """Relay server returned an error."""
    pass


class CertificatePinningError(NetworkError):
    """Server certificate doesn't match pinned fingerprints."""
    pass


class ProtocolError(PhantomShareError):
    """Protocol version mismatch or invalid message."""
    pass


class ConfigurationError(PhantomShareError):
    """Invalid configuration."""
    pass
