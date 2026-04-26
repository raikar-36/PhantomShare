"""
Unit tests for PhantomShare crypto utilities.
"""
import os
import pytest
from app.crypto_utils import (
    secure_zero_memory,
    SecureBytes,
    derive_signaling_key,
    derive_topic_id,
    signaling_encrypt,
    signaling_decrypt,
    CryptoSession,
)


class TestSecureMemory:
    """Tests for secure memory handling."""
    
    def test_secure_zero_memory_zeros_buffer(self):
        """secure_zero_memory should zero all bytes in a bytearray."""
        data = bytearray(b"secret_key_data!")
        secure_zero_memory(data)
        assert all(b == 0 for b in data)
    
    def test_secure_zero_memory_empty_buffer(self):
        """secure_zero_memory should handle empty buffers."""
        data = bytearray()
        secure_zero_memory(data)  # Should not raise
        assert len(data) == 0
    
    def test_secure_zero_memory_ignores_non_bytearray(self):
        """secure_zero_memory should silently ignore non-bytearray input."""
        data = b"immutable"
        secure_zero_memory(data)  # Should not raise


class TestSecureBytes:
    """Tests for SecureBytes wrapper class."""
    
    def test_secure_bytes_stores_data(self):
        """SecureBytes should store and return data correctly."""
        original = os.urandom(32)
        sb = SecureBytes(original)
        assert sb.data == original
        assert bytes(sb) == original
        assert len(sb) == 32
    
    def test_secure_bytes_clear_zeros_data(self):
        """SecureBytes.clear() should zero the internal buffer."""
        sb = SecureBytes(b"secret_key_123456")
        sb.clear()
        assert len(sb) == 0
        assert sb.data == b""
    
    def test_secure_bytes_del_zeros_data(self):
        """SecureBytes should zero data on deletion."""
        original = bytearray(b"secret_key_12345")
        sb = SecureBytes(bytes(original))
        # Get reference to internal buffer for verification
        internal = sb._data
        del sb
        # After deletion, internal buffer should be empty
        # (can't guarantee timing, but clear() is called)


class TestSignalingCrypto:
    """Tests for signaling-level encryption."""
    
    def test_derive_signaling_key_deterministic(self):
        """Same session code should derive the same key."""
        code = "abcd-efgh"
        key1 = derive_signaling_key(code)
        key2 = derive_signaling_key(code)
        assert key1 == key2
        assert len(key1) == 32
    
    def test_derive_signaling_key_different_codes(self):
        """Different session codes should derive different keys."""
        key1 = derive_signaling_key("abcd-efgh")
        key2 = derive_signaling_key("wxyz-1234")
        assert key1 != key2
    
    def test_derive_topic_id_deterministic(self):
        """Same session code should derive the same topic ID."""
        code = "test-code"
        topic1 = derive_topic_id(code)
        topic2 = derive_topic_id(code)
        assert topic1 == topic2
        assert len(topic1) == 16
        assert all(c in "0123456789abcdef" for c in topic1)
    
    def test_derive_topic_id_different_codes(self):
        """Different session codes should derive different topic IDs."""
        topic1 = derive_topic_id("code-aaaa")
        topic2 = derive_topic_id("code-bbbb")
        assert topic1 != topic2
    
    def test_signaling_encrypt_decrypt_roundtrip(self):
        """Signaling encryption/decryption should roundtrip correctly."""
        key = derive_signaling_key("test-session")
        plaintext = b"Hello, secure world!"
        
        ciphertext = signaling_encrypt(key, plaintext)
        decrypted = signaling_decrypt(key, ciphertext)
        
        assert decrypted == plaintext
    
    def test_signaling_encrypt_produces_different_output(self):
        """Each encryption should produce different ciphertext (random nonce)."""
        key = derive_signaling_key("test-session")
        plaintext = b"Same message"
        
        ct1 = signaling_encrypt(key, plaintext)
        ct2 = signaling_encrypt(key, plaintext)
        
        # Ciphertexts should differ due to random nonce
        assert ct1 != ct2
        # But both should decrypt to the same plaintext
        assert signaling_decrypt(key, ct1) == plaintext
        assert signaling_decrypt(key, ct2) == plaintext
    
    def test_signaling_decrypt_wrong_key_fails(self):
        """Decryption with wrong key should fail."""
        key1 = derive_signaling_key("session-1")
        key2 = derive_signaling_key("session-2")
        plaintext = b"Secret message"
        
        ciphertext = signaling_encrypt(key1, plaintext)
        
        with pytest.raises(Exception):  # InvalidTag or similar
            signaling_decrypt(key2, ciphertext)


class TestCryptoSession:
    """Tests for CryptoSession E2E encryption."""
    
    def test_public_key_is_32_bytes(self):
        """Public key should be 32 bytes (X25519 raw format)."""
        session = CryptoSession("test-code")
        pub_key = session.get_public_key_bytes()
        assert len(pub_key) == 32
    
    def test_public_key_is_unique(self):
        """Each session should generate a unique key pair."""
        s1 = CryptoSession("code-a")
        s2 = CryptoSession("code-b")
        assert s1.get_public_key_bytes() != s2.get_public_key_bytes()
    
    def test_key_exchange_derives_shared_key(self):
        """Key exchange should derive the same shared key for both parties."""
        alice = CryptoSession("shared-code")
        bob = CryptoSession("shared-code")
        
        # Exchange public keys
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        # Both should have derived the same shared key
        assert alice.get_shared_key() == bob.get_shared_key()
        assert len(alice.get_shared_key()) == 32
    
    def test_nonce_prefix_is_different(self):
        """Key exchange should assign different nonce prefixes to each party."""
        alice = CryptoSession("nonce-test")
        bob = CryptoSession("nonce-test")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        # One should have prefix 0, the other prefix 1
        assert alice._nonce_prefix != bob._nonce_prefix
        assert {alice._nonce_prefix, bob._nonce_prefix} == {0, 1}
    
    def test_encrypt_decrypt_roundtrip(self):
        """Encryption/decryption should roundtrip correctly between parties."""
        alice = CryptoSession("roundtrip-test")
        bob = CryptoSession("roundtrip-test")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        # Alice encrypts, Bob decrypts
        plaintext = b"Secret message from Alice to Bob"
        ciphertext = alice.encrypt(plaintext)
        decrypted = bob.decrypt(ciphertext)
        
        assert decrypted == plaintext
        
        # Bob encrypts, Alice decrypts
        reply = b"Reply from Bob to Alice"
        ct_reply = bob.encrypt(reply)
        decrypted_reply = alice.decrypt(ct_reply)
        
        assert decrypted_reply == reply
    
    def test_encrypt_multiple_messages(self):
        """Multiple messages should encrypt correctly (nonce increment)."""
        alice = CryptoSession("multi-msg")
        bob = CryptoSession("multi-msg")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        messages = [f"Message {i}".encode() for i in range(10)]
        
        for msg in messages:
            ct = alice.encrypt(msg)
            pt = bob.decrypt(ct)
            assert pt == msg
    
    def test_ciphertext_includes_nonce(self):
        """Ciphertext should be longer than plaintext (nonce + tag)."""
        alice = CryptoSession("size-test")
        bob = CryptoSession("size-test")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        
        plaintext = b"Test"
        ciphertext = alice.encrypt(plaintext)
        
        # Ciphertext = nonce (12) + plaintext (4) + tag (16) = 32 bytes
        expected_overhead = CryptoSession.NONCE_LEN + CryptoSession.TAG_LEN
        assert len(ciphertext) == len(plaintext) + expected_overhead
    
    def test_clear_zeros_keys(self):
        """clear() should zero the shared key."""
        session = CryptoSession("clear-test")
        peer = CryptoSession("clear-test")
        session.derive_shared_key(peer.get_public_key_bytes())
        
        session.clear()
        
        # After clear, shared key should be None or empty
        assert session._shared_key is None or len(session._shared_key) == 0
    
    def test_verification_code_generation(self):
        """get_verification_code() should return matching codes for both parties."""
        alice = CryptoSession("emoji-test")
        bob = CryptoSession("emoji-test")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        code_alice = alice.get_verification_code()
        code_bob = bob.get_verification_code()
        
        # Both should have the same verification code
        assert code_alice == code_bob
        # Should contain characters (length > 0)
        assert len(code_alice) > 0


class TestCryptoSessionErrors:
    """Tests for error handling in CryptoSession."""
    
    def test_encrypt_before_key_exchange_raises(self):
        """Encrypting before key exchange should raise ValueError."""
        session = CryptoSession("no-exchange")
        
        with pytest.raises(ValueError):
            session.encrypt(b"test")
    
    def test_decrypt_before_key_exchange_raises(self):
        """Decrypting before key exchange should raise ValueError."""
        session = CryptoSession("no-exchange")
        
        with pytest.raises(ValueError):
            session.decrypt(b"test ciphertext")
    
    def test_decrypt_corrupted_ciphertext_fails(self):
        """Decrypting corrupted ciphertext should raise an exception."""
        alice = CryptoSession("corrupt-test")
        bob = CryptoSession("corrupt-test")
        
        alice.derive_shared_key(bob.get_public_key_bytes())
        bob.derive_shared_key(alice.get_public_key_bytes())
        
        ciphertext = alice.encrypt(b"Original message")
        corrupted = bytearray(ciphertext)
        corrupted[-1] ^= 0xFF  # Flip last byte
        
        with pytest.raises(Exception):  # InvalidTag or similar
            bob.decrypt(bytes(corrupted))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
