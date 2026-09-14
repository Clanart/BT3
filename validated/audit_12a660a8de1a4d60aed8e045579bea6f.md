### Title
Unthrottled Keyring Passphrase Brute-Force via Daemon RPC `unlock_keyring`/`validate_keyring_passphrase` - ([File: chia/daemon/server.py])

### Summary
The chia daemon exposes two local RPC commands, `unlock_keyring` and `validate_keyring_passphrase`, that both check a caller-supplied passphrase against the master keyring passphrase with `Keychain.master_passphrase_is_valid()`. Neither command enforces any attempt limit, lockout, or delay, unlike the interactive CLI path which caps attempts at `MAX_RETRIES = 3` with a `FAILED_ATTEMPT_DELAY`. Any local RPC caller connected to the daemon (GUI, wallet, or any process with a valid daemon connection) can call these commands in an unbounded loop to brute-force the keyring master passphrase, and `validate_keyring_passphrase` acts as an oracle that discloses success/failure with distinct error strings for "missing key" vs "bad passphrase" vs valid.

### Finding Description
`unlock_keyring` in `chia/daemon/server.py` validates the passphrase and, on success, calls `Keychain.set_cached_master_passphrase(key)` which unlocks the keyring for the remainder of the daemon's lifetime, granting access to every private key managed by that keychain instance. [1](#0-0) 

`validate_keyring_passphrase` performs the identical check via `Keychain.master_passphrase_is_valid(key, force_reload=True)` but only returns a boolean/error, without any unlock side effect, functioning as a pure oracle that an attacker can call repeatedly and cheaply to test candidate passphrases before attempting the state-changing `unlock_keyring`. [2](#0-1) 

Contrast this with the sanctioned interactive path, `obtain_current_passphrase()` in `chia/util/keyring_wrapper.py`, which caps retries at `MAX_RETRIES = 3` and inserts `FAILED_ATTEMPT_DELAY = 0.5` seconds between attempts: [3](#0-2) [4](#0-3) 

The daemon's RPC surface has no equivalent throttling mechanism at all — the architecture notes confirm there is no rate limiter on daemon messages, only mutual TLS gating the connection itself: [5](#0-4) 

The existing test suite exercises repeated wrong-passphrase attempts against both routes back-to-back with no lockout triggered, confirming the unthrottled behavior is by design/expected in current tests: [6](#0-5) [7](#0-6) 

### Impact Explanation
A successful passphrase guess via `unlock_keyring` unlocks the entire local keychain, exposing every wallet's private keys (via `KeychainServer`/`KeychainProxy` and subsequently `wallet_rpc_api.get_private_key`), enabling unauthorized signing and movement of all coins controlled by any key stored in that keychain — a direct path to unauthorized coin movement/theft for any locally reachable caller that can reach the daemon's websocket (any local RPC caller, including lower-privileged local processes/services registered with the daemon).

### Likelihood Explanation
Any process capable of establishing the standard mutual-TLS daemon connection (the same connection used by the wallet, GUI, and CLI tooling) can invoke these commands programmatically without any additional privilege check, and can retry indefinitely with no delay or lockout, making automated brute-forcing of a weak/guessable master passphrase practical, especially since `validate_keyring_passphrase` provides a free oracle that avoids triggering the unlock side-effects while enumerating candidates.

### Recommendation
Add attempt limiting/backoff (mirroring `MAX_RETRIES`/`FAILED_ATTEMPT_DELAY` used in the interactive path) to both `unlock_keyring` and `validate_keyring_passphrase` in `chia/daemon/server.py`, tracked per-connection or globally per daemon instance, and consider unifying error responses so `validate_keyring_passphrase` does not provide a lower-cost oracle than `unlock_keyring`.

### Proof of Concept
1. Connect to the daemon via the standard mutual-TLS websocket connection used by any local client (GUI/CLI/wallet).
2. Repeatedly send `validate_keyring_passphrase` (or `unlock_keyring`) requests with candidate passphrases, e.g.:
```
create_payload("validate_keyring_passphrase", {"key": "<candidate>"}, "test", "daemon")
```
3. Observe no lockout, delay, or rate limiting occurs across arbitrarily many attempts (as demonstrated by the existing test harness sending multiple wrong/missing/empty passphrase attempts back-to-back with immediate responses each time).
4. Once the correct passphrase is found, call `unlock_keyring` with it to unlock the keychain and subsequently retrieve private keys through the wallet RPC (`get_private_key`), enabling unauthorized transaction signing.

### Citations

**File:** chia/daemon/server.py (L519-549)
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

**File:** chia/util/keyring_wrapper.py (L61-65)
```python
DEFAULT_PASSPHRASE_PROMPT = (
    colorama.Fore.YELLOW + colorama.Style.BRIGHT + "(Unlock Keyring)" + colorama.Style.RESET_ALL + " Passphrase: "
)
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

**File:** chia/_tests/core/daemon/test_daemon.py (L1514-1537)
```python
    RouteCase(
        route="unlock_keyring",
        description="wrong current passphrase",
        request={"key": "wrong passphrase"},
        response={"success": False, "error": "bad passphrase"},
    ),
    RouteCase(
        route="unlock_keyring",
        description="incorrect type",
        request={"key": True},
        response={"success": False, "error": "missing key"},
    ),
    RouteCase(
        route="unlock_keyring",
        description="missing data",
        request={},
        response={"success": False, "error": "missing key"},
    ),
    RouteCase(
        route="unlock_keyring",
        description="correct",
        request={"key": "this is a passphrase"},
        response={"success": True, "error": None},
    ),
```
