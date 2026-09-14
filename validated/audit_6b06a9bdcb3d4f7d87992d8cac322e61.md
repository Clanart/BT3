### Title
Missing Rate Limiting on Daemon Keyring Passphrase Verification Allows Unlimited Brute-Force Attempts - ([File: chia/daemon/server.py])

### Summary
The Chia daemon's local RPC surface exposes `unlock_keyring` and `validate_keyring_passphrase` commands that check a submitted passphrase against the encrypted keyring, but neither applies any attempt counter, throttling, or lockout. A caller with a live daemon websocket connection can submit an unbounded number of guesses for the master passphrase that protects all wallet private keys, mirroring the MISP `email_otp()` brute-force class: a secondary secret verification endpoint is guessable without limit because attempts are never tracked or blocked.

### Finding Description
`WebSocketServer.unlock_keyring()` and `WebSocketServer.validate_keyring_passphrase()` both call `Keychain.master_passphrase_is_valid(key, force_reload=True)` and return a simple success/failure boolean with no side-effect state tracking failed attempts: [1](#0-0) [2](#0-1) 

Both handlers are reachable directly through the daemon's command dispatch table with no invocation limit: [3](#0-2) 

The only place `MAX_RETRIES`/lockout logic exists in the codebase is `obtain_current_passphrase()` in `chia/util/keyring_wrapper.py`, which is exclusively used for the interactive CLI prompt path (`chia/cmds/keys_funcs.py`, `chia/cmds/start_funcs.py`) and is never invoked by the daemon's websocket RPC handlers: [4](#0-3) 

Because `KeychainMaxUnlockAttempts` (`chia/util/errors.py`) is only raised from that CLI-only code path, the RPC-reachable `unlock_keyring`/`validate_keyring_passphrase`/`set_keyring_passphrase` commands have no equivalent protection: [5](#0-4) 

This is architecturally the same bug class as CVE-2026-85237: a secondary-secret verification endpoint (OTP there, master keyring passphrase here) is checked per-request against user input with no per-identity/per-connection failed-attempt accounting, so the secret's effective entropy is reduced to whatever an attacker can grind through repeated RPC calls.

### Impact Explanation
Successfully guessing the master keyring passphrase gives full read access to the decrypted keyring contents (`FileKeyring.check_passphrase`/`get_decrypted_data_dict`), i.e., all private keys managed by that node: [6](#0-5) 
Once unlocked, `Keychain.set_cached_master_passphrase(key)` makes those keys available for signing, enabling unauthorized transaction signing/coin movement for every wallet stored in that keyring — this satisfies the "unauthorized coin movement" impact bar. The daemon websocket is protected by mutual TLS (client cert required), so this requires an actor who already holds a valid daemon client certificate but not the passphrase itself (e.g., a compromised local service, a lower-privileged co-tenant process, or a leaked-but-scope-limited daemon cert) — analogous to the "already past primary auth, guessing the secondary factor" scenario in the original report.

### Likelihood Explanation
Exploitation likelihood is bounded by the requirement to hold daemon mTLS credentials, but once that bar is met the attack is trivial: no lockout, no delay, no attempt counting — an attacker can script unlimited `validate_keyring_passphrase` calls (which don't even mutate keyring state) at whatever rate the underlying passphrase decryption cost allows. There's no server-side circuit breaker equivalent to the CLI's `MAX_RETRIES=3` + `FAILED_ATTEMPT_DELAY`.

### Recommendation
Apply the same failed-attempt tracking used for the CLI's `obtain_current_passphrase()` (or Chia's existing brute-force protection idioms) to the daemon's `unlock_keyring`, `validate_keyring_passphrase`, and `set_keyring_passphrase` handlers: track failed attempts per connection/keychain, enforce a max-attempts threshold with increasing delay or lockout, and invalidate/require re-authentication after the threshold is exceeded, consistent with how MISP's patch integrated its existing brute-force protection into the OTP flow.

### Proof of Concept
1. Establish a websocket connection to the daemon using a valid client certificate (`connect_to_daemon`), with a keyring that has a master passphrase set.
2. Repeatedly send `create_payload("validate_keyring_passphrase", {"key": guess}, "test", "daemon")` for arbitrarily many different `guess` values, as exercised (without any imposed limit) in the existing test: [7](#0-6) 
3. Observe that no request is ever rejected for exceeding an attempt count — every call returns a plain `{"success": bool, "error": ...}` regardless of how many prior wrong guesses were sent, confirming the absence of rate limiting.

### Citations

**File:** chia/daemon/server.py (L462-467)
```python
            "is_keyring_locked": self.is_keyring_locked,
            "keyring_status": self.keyring_status_command,
            "unlock_keyring": self.unlock_keyring,
            "validate_keyring_passphrase": self.validate_keyring_passphrase,
            "set_keyring_passphrase": self.set_keyring_passphrase,
            "remove_keyring_passphrase": self.remove_keyring_passphrase,
```

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

**File:** chia/util/errors.py (L269-272)
```python
class KeychainMaxUnlockAttempts(KeychainException):
    def __init__(self) -> None:
        super().__init__("maximum passphrase attempts reached")

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
