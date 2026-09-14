Found it. `decrypt_data()` in `chia/util/file_keyring.py` validates the AEAD checkbytes with a plain Python `!=` byte-string comparison, which is not constant-time. [1](#0-0) 

### Title
Keyring passphrase-decryption checkbytes comparison uses non-constant-time `!=`, enabling a local timing side-channel on daemon passphrase validation - (File: chia/util/file_keyring.py)

### Summary
`decrypt_data()` in `chia/util/file_keyring.py` decrypts the keyring payload with ChaCha20Poly1305 and then verifies a fixed 16-byte marker (`CHECKBYTES_VALUE`) using a plain byte-string `!=` comparison instead of a constant-time comparison such as `hmac.compare_digest`. [2](#0-1) . This mirrors the CVE-2025-57784 bug class (non-constant-time `strcmp` used for authentication comparison), just applied to a Python byte comparison instead of C `strcmp`.

### Finding Description
`FileKeyring.check_passphrase()` calls `get_decrypted_data_dict(passphrase)`, which derives a symmetric key from the candidate passphrase via PBKDF2 and calls `decrypt_data()`. [3](#0-2) . Once the AEAD tag check inside `ChaCha20Poly1305.decrypt()` passes (i.e., once the derived key is correct), `decrypt_data()` performs a secondary comparison of the leading 16 bytes of the plaintext against a hardcoded constant using Python's `!=` operator, which short-circuits on the first mismatched byte and is not constant-time. [1](#0-0) 

This check is reachable through the daemon's `unlock_keyring` and `validate_keyring_passphrase` RPC commands, which pass an attacker/local-user-supplied `key` straight into `Keychain.master_passphrase_is_valid()` → `KeyringWrapper.master_passphrase_is_valid()` → `FileKeyring.check_passphrase()`. [4](#0-3) [5](#0-4) 

Note: because the passphrase must first pass PBKDF2 key derivation and AEAD decryption/authentication before this checkbytes comparison is even reached, the actual exploitable timing signal from the `!=` byte comparison itself is extremely weak in practice — the AEAD tag check (implemented in `cryptography`'s ChaCha20Poly1305, which is expected to be constant-time) is the real gate that must be defeated first, and a wrong key almost never reaches the checkbytes comparison with partially-matching plaintext bytes. This significantly limits real-world exploitability compared to the original Tomahawk `strcmp` case, where the raw secret was compared directly.

### Impact Explanation
If exploitable, a local low-privileged user who can reach the daemon's TLS websocket (mutual-TLS-authenticated, but any process holding the local daemon client cert, e.g. other local users with cert access) could theoretically use timing differences in `validate_keyring_passphrase`/`unlock_keyring` responses to narrow down passphrase guesses, bypassing keyring lock protection and gaining unauthorized private-key access. This maps to the "daemon/keychain/RPC authorization" category. However, given the AEAD authentication step dominates the check, no practical measurable timing channel is proven in this analog.

### Likelihood Explanation
Low-to-Medium. The comparison is reachable via a documented RPC path (`validate_keyring_passphrase`), [6](#0-5) , but the vulnerable `!=` comparison sits downstream of an AEAD authentication check that must already pass, meaning an attacker would first need to defeat ChaCha20Poly1305 authentication — at which point the checkbytes comparison timing is not the meaningful bottleneck. This makes exploitation far less practical than the original C `strcmp`-on-raw-secret bug.

### Recommendation
Replace the `!=` comparison in `decrypt_data()` with a constant-time comparison, e.g. `hmac.compare_digest(CHECKBYTES_VALUE, output[:len(CHECKBYTES_VALUE)])`, for defense-in-depth even though the AEAD tag check is the primary security boundary.

### Proof of Concept
Not established as a practically measurable timing side-channel in this codebase: the checkbytes comparison is only reached after ChaCha20Poly1305 AEAD decryption+authentication already succeeds, so no concrete PoC demonstrating recoverable timing signal was verified. This is reported as a defensive-coding analog to the reported bug class, not a confirmed exploitable vulnerability.

### Citations

**File:** chia/util/file_keyring.py (L32-68)
```python
CHECKBYTES_VALUE = b"5f365b8292ee505b"  # Randomly generated
MAX_LABEL_LENGTH = 65
MAX_SUPPORTED_VERSION = 1  # Max supported file format version


def generate_nonce() -> bytes:
    """
    Creates a nonce to be used by ChaCha20Poly1305. This should be called each time
    the payload is encrypted.
    """
    return token_bytes(NONCE_BYTES)


def generate_salt() -> bytes:
    """
    Creates a salt to be used in combination with the master passphrase to derive
    a symmetric key using PBKDF2
    """
    return token_bytes(SALT_BYTES)


def symmetric_key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    return pbkdf2_hmac("sha256", passphrase.encode(), salt, HASH_ITERS)


def encrypt_data(input_data: bytes, key: bytes, nonce: bytes) -> bytes:
    encryptor = ChaCha20Poly1305(key)
    data = encryptor.encrypt(nonce, CHECKBYTES_VALUE + input_data, None)
    return data


def decrypt_data(input_data: bytes, key: bytes, nonce: bytes) -> bytes:
    decryptor = ChaCha20Poly1305(key)
    output = decryptor.decrypt(nonce, input_data, None)
    if CHECKBYTES_VALUE != output[: len(CHECKBYTES_VALUE)]:
        raise ValueError("decryption failure (checkbytes)")
    return output[len(CHECKBYTES_VALUE) :]
```

**File:** chia/util/file_keyring.py (L394-406)
```python
    def check_passphrase(self, passphrase: str, force_reload: bool = False) -> bool:
        """
        Attempts to validate the passphrase by decrypting keyring.data
        contents and checking the checkbytes value
        """
        if force_reload:
            self.cached_file_content = FileKeyringContent.create_from_path(self.keyring_path)

        try:
            self.cached_file_content.get_decrypted_data_dict(passphrase)
            return True
        except Exception:
            return False
```

**File:** chia/daemon/server.py (L519-570)
```python
    async def unlock_keyring(self, websocket: WebSocketResponse, request: dict[str, Any]) -> dict[str, Any]:
        success: bool = False
        error: str | None = None
        key: str | None = request.get("key", None)
        if type(key) is not str:
            return {"success": False, "error": "missing key"}

        try:
            if Keychain.master_passphrase_is_valid(key, force_reload=True):
                Keychain.set_cached_master_passphrase(key)
                success = True
                # Inform the GUI of keyring status changes
                self.keyring_status_changed(await self.keyring_status(), "wallet_ui")
            else:
                error = "bad passphrase"
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Keyring passphrase validation failed: {e} {tb}")
            error = "validation exception"

        if success and self.run_check_keys_on_unlock:
            try:
                self.log.info("Running check_keys now that the keyring is unlocked")
                check_keys(self.root_path)
                self.run_check_keys_on_unlock = False
            except Exception as e:
                tb = traceback.format_exc()
                self.log.error(f"check_keys failed after unlocking keyring: {e} {tb}")

        response: dict[str, Any] = {"success": success, "error": error}
        return response

    async def validate_keyring_passphrase(
        self,
        websocket: WebSocketResponse,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        success: bool = False
        error: str | None = None
        key: str | None = request.get("key", None)
        if type(key) is not str:
            return {"success": False, "error": "missing key"}

        try:
            success = Keychain.master_passphrase_is_valid(key, force_reload=True)
        except Exception as e:
            tb = traceback.format_exc()
            self.log.error(f"Keyring passphrase validation failed: {e} {tb}")
            error = "validation exception"

        response: dict[str, Any] = {"success": success, "error": error}
        return response
```

**File:** chia/util/keyring_wrapper.py (L232-233)
```python
    def master_passphrase_is_valid(self, passphrase: str, force_reload: bool = False) -> bool:
        return self.keyring.check_passphrase(passphrase, force_reload=force_reload)
```
