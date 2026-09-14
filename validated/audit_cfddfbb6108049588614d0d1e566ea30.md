### Title
Missing brute-force/lockout protection on daemon keyring-passphrase RPCs allows local RPC callers to brute force the master passphrase and steal wallet keys - (File: `chia/daemon/server.py`)

### Summary
The Chia daemon exposes two websocket RPC commands, `unlock_keyring` and `validate_keyring_passphrase`, that check a client-supplied string against the keychain's master passphrase. Neither handler implements any attempt counter, lockout, or delay, so any client that can open a connection to the daemon (any local process holding a daemon TLS client cert) can send an unbounded number of guesses with zero throttling, mirroring the AzuraCast bug class of a login/credential-check endpoint whose rate limiting can be bypassed (in this case, simply because there is none at all on the machine-checkable RPC path).

### Finding Description
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` directly call `Keychain.master_passphrase_is_valid(key, force_reload=True)` and return a boolean success/failure with no other side effect that would slow down repeated attempts: [1](#0-0) [2](#0-1) 

There is no counter, sleep, or ban logic anywhere in these handlers or in `KeychainServer`/`Keychain.master_passphrase_is_valid()`. The only place in the codebase that enforces a retry limit and delay is the CLI's interactive-prompt helper `obtain_current_passphrase()`, which is a completely separate code path used only for terminal prompts, not for the daemon websocket command dispatch: [3](#0-2) [4](#0-3) 

Because `unlock_keyring`/`validate_keyring_passphrase` are dispatched as ordinary daemon commands, any local client that can complete the daemon's mutual-TLS handshake (as documented, the daemon's only real gate is "mutual TLS using daemon private certs plus local config") can hammer these two commands directly over the websocket with no per-connection or global rate limiter, since the daemon module explicitly has no rate limiter of its own: [5](#0-4) 

The passphrase itself is checked via PBKDF2-HMAC-SHA256 with a fixed iteration count, which is the only cost factor slowing down guesses — there is no additional throttling layered on top at the RPC boundary: [6](#0-5) 

A successful guess against `unlock_keyring` immediately caches the passphrase and unlocks the keyring for that daemon process: [7](#0-6) 

Once unlocked, the keychain exposes every private key stored under it (farmer, pool, and wallet spend keys) to subsequent `get_key`/`get_keys` daemon RPCs, which is the actual path to unauthorized coin movement.

### Impact Explanation
An attacker who can reach the local daemon socket (e.g., a malicious/compromised local application, another local user with access to daemon certs, or any process able to obtain the daemon's private client cert) can brute-force the master passphrase protecting a user's entire keychain with no lockout or slowdown beyond the fixed PBKDF2 cost. A successful guess unlocks all private keys, letting the attacker sign and broadcast spends from every wallet secured by that keychain — i.e., unauthorized coin movement/fund theft, not merely information disclosure. This is a credential-brute-force class bug on the local daemon/keychain RPC authorization boundary, directly analogous to the AzuraCast login rate-limiting bypass (CVE-2023-2531).

### Likelihood Explanation
Exploitability depends on the attacker already possessing a daemon TLS client identity, which the codebase treats as a "semi-trusted local/admin surface" rather than public network access. However, many local processes/services on the same machine share access to daemon certs (multiple chia services, GUI, CLI, any locally installed software that can read the cert directory), so the population of callers able to reach this RPC is broader than a single trusted admin. Given no rate limiting exists at all (not even a minimal per-connection counter), exploitation is trivial for any caller that clears the TLS bar, and passphrase strength (especially for the common "no master passphrase set" default or user-chosen weak passphrases) can be broken with a fast unthrottled loop.

### Recommendation
Add attempt-count and time-based lockout/backoff to the daemon-side `unlock_keyring` and `validate_keyring_passphrase` handlers in `chia/daemon/server.py` (mirroring `MAX_RETRIES`/`FAILED_ATTEMPT_DELAY` from `chia/util/keyring_wrapper.py`, but enforced per-connection/per-keyring rather than only for interactive CLI prompts), and consider closing/banning the websocket connection after repeated failures.

### Proof of Concept
1. Set a master passphrase on a keychain (`chia passphrase set`).
2. Connect directly to the daemon websocket using the local daemon client certs (as any test harness like `chia/_tests/core/daemon/test_daemon.py` does via `daemon_connection_and_temp_keychain`).
3. In a tight loop, send `create_payload("validate_keyring_passphrase", {"key": guess}, "attacker", "daemon")` for a large wordlist/guess set with no delay between requests.
4. Observe the daemon processes every guess immediately with no increasing delay, error, or connection termination, confirming the absence of brute-force protection, as shown by the response handling in [2](#0-1) .

### Citations

**File:** chia/daemon/server.py (L519-537)
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
```

**File:** chia/daemon/server.py (L551-570)
```python
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

**File:** chia/util/keyring_wrapper.py (L64-65)
```python
FAILED_ATTEMPT_DELAY = 0.5
MAX_RETRIES = 3
```

**File:** chia/util/keyring_wrapper.py (L100-114)
```python
    # Prompt interactively with up to MAX_RETRIES attempts
    for i in range(MAX_RETRIES):
        colorama.init()

        passphrase = prompt_for_passphrase(prompt)

        if KeyringWrapper.get_shared_instance().master_passphrase_is_valid(passphrase):
            # If using the passphrase cache, and the user inputted a passphrase, update the cache
            if use_passphrase_cache:
                KeyringWrapper.get_shared_instance().set_cached_master_passphrase(passphrase, validated=True)
            return passphrase

        time.sleep(FAILED_ATTEMPT_DELAY)
        print("Incorrect passphrase\n")
    raise KeychainMaxUnlockAttempts
```

**File:** .cursor/context/daemon.md (L58-61)
```markdown
- There is no peer-style protocol enum, streamable binary framing, node-type map,
  or rate limiter here. The primary gate is mutual TLS using daemon private
  certs plus local config (`self_hostname`, `daemon_port`,
  `daemon_max_message_size`, `daemon_heartbeat`).
```

**File:** chia/util/file_keyring.py (L29-54)
```python
SALT_BYTES = 16  # PBKDF2 param
NONCE_BYTES = 12  # ChaCha20Poly1305 nonce is 12-bytes
HASH_ITERS = 100000  # PBKDF2 param
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
```
