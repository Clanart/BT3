### Title
Daemon WebSocket Keyring-Unlock RPCs Have No Brute-Force Rate Limiting, Unlike the CLI Path - (File: `chia/daemon/server.py`)

### Summary
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` in `chia/daemon/server.py` accept an arbitrary number of passphrase-guessing attempts over the daemon websocket with no throttling, counter, or lockout of any kind. This contrasts with the interactive CLI path (`obtain_current_passphrase()` in `chia/util/keyring_wrapper.py`), which caps guesses at `MAX_RETRIES = 3` (with a `FAILED_ATTEMPT_DELAY` between tries) and raises `KeychainMaxUnlockAttempts` on failure. Any local caller that can open a websocket to the daemon can bypass the throttling that the "official" CLI unlock flow enforces and brute-force a weak keyring master passphrase at wire speed.

### Finding Description
The daemon dispatch table registers `unlock_keyring` and `validate_keyring_passphrase` directly against `Keychain.master_passphrase_is_valid()`: [1](#0-0) [2](#0-1) 

Neither handler tracks failed attempts, applies a delay, or locks the keyring after repeated failures — each request is evaluated independently and the loop that dispatches daemon commands (`handle_message()`) simply calls the mapped coroutine per message with no additional gating: [3](#0-2) 

This is the only enforcement point for keyring passphrase verification reachable over the daemon's local RPC surface; the daemon module context explicitly documents that "There is no ... rate limiter" at this boundary — TLS client-cert possession is the only gate: [4](#0-3) 

By contrast, the CLI's interactive unlock path deliberately limits guesses: [5](#0-4) 

So the exact same underlying check (`master_passphrase_is_valid`) is protected by a 3-attempt lockout when reached through the CLI helper, but is completely unthrottled when reached directly through the daemon websocket RPC command — the daemon RPC command effectively acts as an alternate "auth token" path that bypasses the shared rate-limiting logic intended to protect the master passphrase, mirroring the reported bug class of an alternate auth-token path circumventing shared rate limiting.

### Impact Explanation
If the keyring master passphrase is weak or has been set to something guessable (a real-world scenario for users who don't rely on the default random OS-store passphrase), a local process with daemon-cert access (e.g., another local user, a compromised local service, or a CLI/GUI client library bug) can hammer `unlock_keyring`/`validate_keyring_passphrase` without delay or lockout, defeating any password-strength assumption that depends on throttled guessing. Successful brute force yields the master passphrase, unlocking the keyring and exposing all private keys managed by the node (wallet keys, farmer/pool authentication keys). This is a CWE-307/CWE-799-style rate-limiting bypass consistent with the reported bug class.

### Likelihood Explanation
Reaching the daemon still requires a valid daemon-issued TLS client certificate (mutual TLS), so this is not exploitable by a fully external, zero-privilege attacker over the network. However, any local caller who can already open a daemon connection (any installed chia client, CLI, GUI, or another local process with access to the daemon certs under `CHIA_ROOT`) gets unlimited, undelayed guesses — a materially weaker guarantee than what the CLI path intentionally provides. The mismatch between the two paths is a straightforward, easily verified code-level asymmetry regardless of the passphrase's actual strength.

### Recommendation
Add attempt tracking/lockout (mirroring `MAX_RETRIES`/`FAILED_ATTEMPT_DELAY`/`KeychainMaxUnlockAttempts`) to `unlock_keyring()` and `validate_keyring_passphrase()` in `WebSocketServer`, scoped per connection or globally per daemon process, and reject further guesses (or apply exponential backoff) once the threshold is exceeded, so the daemon RPC surface enforces the same throttling guarantee as the CLI.

### Proof of Concept
1. Start the daemon with a master passphrase set (`chia passphrase set`).
2. Using any daemon-cert-holding client, open the daemon websocket and repeatedly send:
   `create_payload("validate_keyring_passphrase", {"key": "<guess>"}, "attacker", "daemon")`
3. Observe unlimited responses of `{"success": False, "error": None}` with no delay, lockout, or ban, as shown by the existing test exercising this exact RPC without any throttling assertions: [6](#0-5) 
4. Repeat until the correct passphrase is found; compare against the CLI path (`obtain_current_passphrase`) which would have raised `KeychainMaxUnlockAttempts` after 3 tries.

### Citations

**File:** chia/daemon/server.py (L408-449)
```python
    async def handle_message(
        self, websocket: WebSocketResponse, message: WsRpcMessage
    ) -> tuple[str, set[WebSocketResponse]] | None:
        """
        This function gets called when new message is received via websocket.
        """

        command = message["command"]
        destination = message["destination"]
        if destination != "daemon":
            if destination in self.connections:
                sockets = self.connections[destination]
                return dict_to_json_str(message), sockets

            return None

        data = message["data"]
        commands_with_data = [
            "start_service",
            "start_plotting",
            "stop_plotting",
            "stop_service",
            "is_running",
            "register_service",
        ]
        if len(data) == 0 and command in commands_with_data:
            response = {"success": False, "error": f'{command} requires "data"'}
        # Keychain commands should be handled by KeychainServer
        elif command in keychain_commands:
            response = await self.keychain_server.handle_command(command, data)
        elif command == "ping":
            response = await ping()
        else:
            command_mapping = self.get_command_mapping()
            if command in command_mapping:
                response = await command_mapping[command](websocket=websocket, request=data)
            else:
                self.log.error(f"UK>> {message}")
                response = {"success": False, "error": f"unknown_command {command}"}

        full_response = format_response(message, response)
        return full_response, {websocket}
```

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

**File:** .cursor/context/daemon.md (L58-61)
```markdown
- There is no peer-style protocol enum, streamable binary framing, node-type map,
  or rate limiter here. The primary gate is mutual TLS using daemon private
  certs plus local config (`self_hostname`, `daemon_port`,
  `daemon_max_message_size`, `daemon_heartbeat`).
```

**File:** chia/util/keyring_wrapper.py (L61-114)
```python
DEFAULT_PASSPHRASE_PROMPT = (
    colorama.Fore.YELLOW + colorama.Style.BRIGHT + "(Unlock Keyring)" + colorama.Style.RESET_ALL + " Passphrase: "
)
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
