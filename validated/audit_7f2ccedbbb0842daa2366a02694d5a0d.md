## Title
Improper Restriction of Excessive Authentication Attempts on daemon keychain unlock/validation RPCs enables unlimited passphrase brute force - (File: `chia/daemon/server.py`)

### Summary
The Chia daemon's local RPC handlers `unlock_keyring` and `validate_keyring_passphrase` in `chia/daemon/server.py` validate the supplied master passphrase against the keychain with no attempt counter, exponential backoff, or lockout at the RPC layer, allowing any client already connected to the daemon websocket to submit unlimited passphrase guesses.

### Finding Description
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` each simply call `Keychain.master_passphrase_is_valid(key, force_reload=True)` and return a success/failure boolean with no state tracked between calls: [1](#0-0) [2](#0-1) 

The only place in the codebase that enforces a retry limit (`MAX_RETRIES = 3`) and a delay (`FAILED_ATTEMPT_DELAY = 0.5`) between failed passphrase attempts is `obtain_current_passphrase()` in `chia/util/keyring_wrapper.py`, which guards the *interactive CLI prompt* path only: [3](#0-2) [4](#0-3) 

That throttle raises `KeychainMaxUnlockAttempts` and is consumed by `chia/cmds/start_funcs.py` when a human is prompted at the terminal: [5](#0-4) 

It is never invoked from the daemon RPC command dispatch. `WebSocketServer.unlock_keyring`/`validate_keyring_passphrase` bypass `obtain_current_passphrase()` entirely and call the keychain validation directly, so a client that can reach the daemon's TLS websocket (e.g. `KeychainProxy`/`DaemonProxy` clients, or any local process holding a daemon client certificate) can send `unlock_keyring`/`validate_keyring_passphrase` commands back-to-back with no growing delay and no cap on the number of attempts, as confirmed by the test suite which exercises repeated wrong/right/missing/empty passphrase submissions on the same connection without any lockout response: [6](#0-5) 

The daemon's gating mechanism is mutual TLS (client certificate) rather than any authentication-attempt limiter: [7](#0-6) 

This is directly analogous to the reported modoboa-installer issue (CWE-307): an authentication endpoint that performs credential validation with no restriction on the number or rate of attempts.

### Impact Explanation
The master keyring passphrase is the sole secret protecting every private key managed by the local `Keychain` (used for wallet signing, farming, pooling, etc.). Because `unlock_keyring`/`validate_keyring_passphrase` impose no attempt limiting, any actor able to open a websocket session to the daemon (any local process presenting the daemon client cert, including a compromised or malicious local application co-resident with the legitimate chia install) can perform an effectively unlimited, unthrottled online brute-force/dictionary attack against the passphrase. A successful guess yields the cached master passphrase, unlocking the keychain and exposing every private key it protects, enabling unauthorized signing and coin movement — a full compromise of custody, not merely a resource-exhaustion concern.

### Likelihood Explanation
Any local client that already holds (or otherwise obtains) the daemon's client TLS credentials can connect and issue commands; this is the same connection class used routinely by `chia_wallet`, `chia_full_node`, and the GUI. No additional privilege beyond an established daemon session is required to call `unlock_keyring`/`validate_keyring_passphrase` repeatedly — there is no per-connection or per-fingerprint counter anywhere in `WebSocketServer`. Since users are permitted to choose short passphrases (Chia enforces only a minimum length, no complexity requirement), a determined local attacker with sustained websocket access has a realistic path to recovering weak passphrases through sheer volume of attempts.

### Recommendation
Add server-side attempt tracking (per connection and/or per keychain) to `WebSocketServer.unlock_keyring` and `WebSocketServer.validate_keyring_passphrase` in `chia/daemon/server.py`: enforce a maximum number of consecutive failed attempts, apply increasing backoff delays between failures (mirroring the existing `FAILED_ATTEMPT_DELAY`/`MAX_RETRIES` semantics from `keyring_wrapper.py`), and lock out further attempts (or require re-authentication of the connection) once the threshold is exceeded, returning `KeychainMaxUnlockAttempts`-style errors to callers instead of allowing indefinite retries on the same websocket.

### Proof of Concept
1. Start the chia daemon with a master passphrase configured (`Keychain.set_master_passphrase`).
2. Connect to the daemon websocket as any client presenting a valid local daemon client certificate (as any co-located local process/service is expected to do).
3. In a loop, send `create_payload("validate_keyring_passphrase", {"key": guess}, ...)` (or `unlock_keyring`) with successive passphrase guesses over the same connection, as done in `chia/_tests/core/daemon/test_daemon.py` lines 959-980.
4. Observe that every request is processed immediately with no growing delay, no lockout, and no cap — confirming unrestricted authentication attempts against the keyring passphrase.

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

**File:** chia/cmds/start_funcs.py (L46-68)
```python
async def create_start_daemon_connection(
    root_path: Path, config: dict[str, Any], *, skip_keyring: bool
) -> DaemonProxy | None:
    connection = await connect_to_daemon_and_validate(root_path, config)
    if connection is None:
        print("Starting daemon", flush=True)
        # launch a daemon
        launch_start_daemon(root_path)
        connection = await connect_to_daemon_and_validate(root_path, config, wait_for_start=True)
    if connection:
        if skip_keyring:
            print("Skipping to unlock keyring")
        else:
            passphrase = None
            if await connection.is_keyring_locked():
                passphrase = Keychain.get_cached_master_passphrase()
                if passphrase is None or not Keychain.master_passphrase_is_valid(passphrase):
                    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="get_current_passphrase") as executor:
                        passphrase = await asyncio.get_running_loop().run_in_executor(executor, get_current_passphrase)

            if passphrase:
                print("Unlocking daemon keyring")
                await connection.unlock_keyring(passphrase)
```

**File:** chia/_tests/core/daemon/test_daemon.py (L959-980)
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

    # When: not including the passphrase in the payload
    await ws.send_str(create_payload("validate_keyring_passphrase", {}, "test", "daemon"))
    # Expect: validation failure
    assert_response(await ws.receive(), missing_passphrase_response_data)

    # When: including an empty passphrase in the payload
    await ws.send_str(create_payload("validate_keyring_passphrase", {"key": ""}, "test", "daemon"))
    # Expect: validation failure
    assert_response(await ws.receive(), empty_passphrase_response_data)
```

**File:** .cursor/context/daemon.md (L58-61)
```markdown
- There is no peer-style protocol enum, streamable binary framing, node-type map,
  or rate limiter here. The primary gate is mutual TLS using daemon private
  certs plus local config (`self_hostname`, `daemon_port`,
  `daemon_max_message_size`, `daemon_heartbeat`).
```
