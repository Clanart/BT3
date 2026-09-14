### Title
Missing Rate Limiting on Daemon Keychain Passphrase Unlock Allows Unbounded Brute-Force of the Master Passphrase - (File: chia/daemon/server.py)

### Summary
The daemon's `unlock_keyring` and `validate_keyring_passphrase` RPC commands allow an unlimited number of passphrase guesses per connection and across reconnects, with no lockout, counter, or backoff enforced server-side. This mirrors the CVE-2024-57610 bug class (no server-side brute-force protection on an authentication surface), applied to the local daemon/keychain RPC authorization boundary named as in-scope in this assessment.

### Finding Description
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` call `Keychain.master_passphrase_is_valid(key, force_reload=True)` directly on every invocation and return a simple success/failure response with no attempt counter, delay, or lockout state maintained by the daemon itself: [1](#0-0) [2](#0-1) 

`master_passphrase_is_valid()` simply forwards to `KeyringWrapper.master_passphrase_is_valid()`, which performs a stateless passphrase check with no per-call throttling: [3](#0-2) [4](#0-3) 

The only place a retry limit (`MAX_RETRIES = 3`, `FAILED_ATTEMPT_DELAY = 0.5`) is enforced is in the CLI's interactive `obtain_current_passphrase()` helper, which is a client-side convenience for prompting a human and is trivially bypassed by any caller invoking the daemon websocket command directly instead of going through the CLI prompt loop: [5](#0-4) [6](#0-5) 

`KeychainMaxUnlockAttempts` exists as an exception type, but it is only raised from `obtain_current_passphrase()`, not from the daemon's `unlock_keyring`/`validate_keyring_passphrase` handlers: [7](#0-6) 

Because the daemon dispatches `unlock_keyring` and `validate_keyring_passphrase` per-message with no shared attempt-tracking state, any caller able to open the daemon websocket (a local RPC caller under this assessment's definition) can submit an unbounded number of guesses to brute-force the master passphrase protecting the private keys in the keyring.

### Impact Explanation
A successful brute force of the master passphrase unlocks the keyring, exposing the private keys used to sign spend bundles for all wallets managed by that keychain. This directly enables unauthorized coin movement, since the attacker could subsequently sign and broadcast spends of the victim's coins using the recovered keys. This satisfies the "concrete unsigned or unauthorized coin movement" impact bar.

### Likelihood Explanation
Exploitation requires only repeated calls to the existing `unlock_keyring`/`validate_keyring_passphrase` daemon commands; no cryptographic bypass or malicious peer/network position is needed. The absence of any counter, incremental delay, or lockout state in the handler code means the only friction is the cost of the passphrase-derivation function itself, not any deliberate anti-brute-force control.

### Recommendation
Add server-side attempt tracking (count and/or exponential backoff) to `WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()`, keyed per connection/keyring, and raise `KeychainMaxUnlockAttempts` (or an equivalent lockout error) once a threshold is exceeded, matching the protection already present in the CLI's `obtain_current_passphrase()` path but currently absent from the daemon RPC handlers themselves.

### Proof of Concept
1. Connect to the daemon websocket with a valid local client (as any local RPC caller can).
2. Repeatedly send `unlock_keyring` (or `validate_keyring_passphrase`) commands with different guessed passphrases, as shown in the existing test harness pattern: [8](#0-7) 
3. Observe that the daemon processes every attempt independently with no lockout, allowing an unbounded number of guesses until the correct master passphrase is found.

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

**File:** chia/util/keyring_wrapper.py (L232-233)
```python
    def master_passphrase_is_valid(self, passphrase: str, force_reload: bool = False) -> bool:
        return self.keyring.check_passphrase(passphrase, force_reload=force_reload)
```

**File:** chia/util/keychain.py (L601-608)
```python
    @staticmethod
    def master_passphrase_is_valid(passphrase: str, force_reload: bool = False) -> bool:
        """
        Checks whether the provided passphrase can unlock the keyring. If force_reload
        is true, the keyring payload will be re-read from the backing file. If false,
        the passphrase will be checked against the in-memory payload.
        """
        return KeyringWrapper.get_shared_instance().master_passphrase_is_valid(passphrase, force_reload=force_reload)
```

**File:** chia/util/errors.py (L269-271)
```python
class KeychainMaxUnlockAttempts(KeychainException):
    def __init__(self) -> None:
        super().__init__("maximum passphrase attempts reached")
```

**File:** chia/_tests/core/daemon/test_daemon.py (L959-970)
```python
    # When: using the correct passphrase
    await ws.send_str(
        create_payload("validate_keyring_passphrase", {"key": "the correct passphrase"}, "test", "daemon")
    )
    # Expect: validation succeeds
    # TODO: unify error responses in the server, sometimes we add `error: None` sometimes not.
    assert_response(await ws.receive(), {**success_response_data, "error": None})

    # When: using the wrong passphrase
    await ws.send_str(create_payload("validate_keyring_passphrase", {"key": "the wrong passphrase"}, "test", "daemon"))
    # Expect: validation failure
    assert_response(await ws.receive(), bad_passphrase_case_response_data)
```
