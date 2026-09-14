### Title
Daemon keyring-passphrase RPCs (`unlock_keyring`, `validate_keyring_passphrase`) allow unlimited brute-force attempts against the master passphrase - ([File: chia/daemon/server.py])

### Summary
The chia daemon exposes `unlock_keyring` and `validate_keyring_passphrase` websocket RPC commands that check a caller-supplied passphrase against the encrypted keyring without any attempt counting, throttling, or lockout, unlike the CLI/interactive passphrase-entry path which enforces `MAX_RETRIES = 3` before raising `KeychainMaxUnlockAttempts`.

### Finding Description
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` take a `key` field from the request and call `Keychain.master_passphrase_is_valid(key, force_reload=True)`, returning `{"success": False, "error": "bad passphrase"}` (or `success: False` for validate) on failure with no state tracking of prior failures, no delay, and no cap on the number of RPC calls a connected client can issue. [1](#0-0) [2](#0-1) 

By contrast, the interactive/CLI path in `chia/util/keyring_wrapper.py` enforces a bounded number of attempts (`MAX_RETRIES = 3`) with a delay (`FAILED_ATTEMPT_DELAY = 0.5`) between tries, and raises `KeychainMaxUnlockAttempts` once exhausted, which callers like `start_funcs.py` explicitly catch and abort on. [3](#0-2) [4](#0-3) 

This mirrors CVE-2018-8171's bug class (CWE-287): a login/credential-check code path exists that is not subject to the same incorrect-attempt limiting as its sibling path, letting a caller who can reach the unthrottled RPC repeatedly try passphrase guesses.

### Impact Explanation
A successful brute force of the keyring master passphrase over the daemon RPC would let the caller unlock the keyring and subsequently retrieve mnemonic/private key material via other daemon RPCs, enabling unauthorized signing and coin movement for every key stored in that keyring. This matches the "unauthorized coin movement" impact category.

### Likelihood Explanation
Exploitation requires a client that can already open a websocket connection to the daemon (the daemon authenticates connections via mutual TLS using certs under the user's `CHIA_ROOT`), so the practical likelihood is bounded by whatever already has legitimate access to those certs (e.g., a local GUI/service process or another local RPC caller). Given such access, there is no cost, delay, or attempt cap preventing rapid repeated passphrase guesses via `unlock_keyring`/`validate_keyring_passphrase`, unlike the deliberately throttled CLI path.

### Recommendation
Add attempt counting/backoff (or reuse the existing `MAX_RETRIES`/`FAILED_ATTEMPT_DELAY`/`KeychainMaxUnlockAttempts` mechanism from `chia/util/keyring_wrapper.py`) to the `unlock_keyring` and `validate_keyring_passphrase` RPC handlers in `chia/daemon/server.py`, so repeated failed passphrase attempts over RPC are throttled and eventually locked out the same way the interactive prompt is.

### Proof of Concept
1. Connect to the daemon websocket with valid client credentials (as any local RPC caller can).
2. Repeatedly send `validate_keyring_passphrase` (or `unlock_keyring`) requests with different `key` values.
3. Observe that the daemon responds to every attempt individually with `{"success": false, ...}` with no increasing delay, lockout, or error indicating too many attempts, as confirmed by `test_validate_keyring_passphrase_rpc` in the test suite, which sends multiple different passphrases back-to-back without hitting any limit. [5](#0-4)

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

**File:** chia/util/keyring_wrapper.py (L64-114)
```python
FAILED_ATTEMPT_DELAY = 0.5
MAX_RETRIES = 3


def prompt_for_passphrase(prompt: str) -> str:
    if sys.platform == "win32" or sys.platform == "cygwin":
        print(prompt, end="", flush=True)
        prompt = ""
    return getpass(prompt)


def obtain_current_passphrase(prompt: str = DEFAULT_PASSPHRASE_PROMPT, use_passphrase_cache: bool = False) -> str:
    from chia.util.keyring_wrapper import KeyringWrapper

    """
    Obtains the master passphrase for the keyring, optionally using the cached
    value (if previously set). If the passphrase isn't already cached, the user is
    prompted interactively to enter their passphrase a max of MAX_RETRIES times
    before failing.
    """

    if use_passphrase_cache:
        passphrase, validated = KeyringWrapper.get_shared_instance().get_cached_master_passphrase()
        if passphrase:
            # If the cached passphrase was previously validated, we assume it's... valid
            if validated:
                return passphrase

            # Cached passphrase needs to be validated
            if KeyringWrapper.get_shared_instance().master_passphrase_is_valid(passphrase):
                KeyringWrapper.get_shared_instance().set_cached_master_passphrase(passphrase, validated=True)
                return passphrase
            else:
                # Cached passphrase is bad, clear the cache
                KeyringWrapper.get_shared_instance().set_cached_master_passphrase(None)

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

**File:** chia/cmds/start_funcs.py (L77-81)
```python
    try:
        daemon = await create_start_daemon_connection(root_path, config, skip_keyring=skip_keyring)
    except KeychainMaxUnlockAttempts:
        print("Failed to unlock keyring")
        return None
```

**File:** chia/_tests/core/daemon/test_daemon.py (L935-980)
```python
@pytest.mark.anyio
async def test_validate_keyring_passphrase_rpc(daemon_connection_and_temp_keychain):
    ws, keychain = daemon_connection_and_temp_keychain

    # When: the keychain has a master passphrase set
    keychain.set_master_passphrase(
        current_passphrase=DEFAULT_PASSPHRASE_IF_NO_MASTER_PASSPHRASE, new_passphrase="the correct passphrase"
    )

    bad_passphrase_case_response_data = {
        "success": False,
        "error": None,
    }

    missing_passphrase_response_data = {
        "success": False,
        "error": "missing key",
    }

    empty_passphrase_response_data = {
        "success": False,
        "error": None,
    }

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

    # When: not including the passphrase in the payload
    await ws.send_str(create_payload("validate_keyring_passphrase", {}, "test", "daemon"))
    # Expect: validation failure
    assert_response(await ws.receive(), missing_passphrase_response_data)

    # When: including an empty passphrase in the payload
    await ws.send_str(create_payload("validate_keyring_passphrase", {"key": ""}, "test", "daemon"))
    # Expect: validation failure
    assert_response(await ws.receive(), empty_passphrase_response_data)
```
