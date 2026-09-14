### Title
Unrate-limited keyring master-passphrase brute force via daemon RPC `unlock_keyring`/`validate_keyring_passphrase` - (File: chia/daemon/server.py)

### Summary
The JumpServer CVE-2023-43650 bug class is "a low-entropy secret used to gate access to sensitive account/key material can be brute-forced because the verification endpoint has no attempt limiting." The Chia daemon's local RPC/keychain boundary has the same shape: `unlock_keyring` and `validate_keyring_passphrase` accept a passphrase guess and return success/failure with no throttling, counter, or lockout, and the default minimum passphrase length is only 8 characters and is optional.

### Finding Description
`chia/daemon/server.py`'s `unlock_keyring()` and `validate_keyring_passphrase()` handlers each take a `key` string from the request and call `Keychain.master_passphrase_is_valid(key, force_reload=True)` [1](#0-0) , [2](#0-1) . Neither handler tracks failed attempts, applies a delay, or locks out the caller after repeated wrong guesses — every call simply re-checks the passphrase against the keyring payload via `KeyringWrapper.master_passphrase_is_valid()` → `FileKeyring.check_passphrase()` [3](#0-2) .

By contrast, the CLI-only path in `chia/util/keyring_wrapper.py` does enforce a bounded retry count (`MAX_RETRIES = 3`) with a fixed delay (`FAILED_ATTEMPT_DELAY = 0.5`) before raising `KeychainMaxUnlockAttempts` [4](#0-3) , but this throttling only applies to `obtain_current_passphrase()` (interactive terminal prompt), not to the daemon websocket RPC path that `unlock_keyring`/`validate_keyring_passphrase` expose. Any client that can open the daemon websocket (a local process holding/using the daemon's private TLS client cert, as documented as the "semi-trusted local/admin surface" in `.cursor/context/rpc.md`) can issue an unbounded number of passphrase guesses over that connection with no server-side limiting.

Because `passphrase_requirements()` allows an optional passphrase with only an 8-character minimum (`min_length: 8`, `is_optional: True` as returned by `keyring_status()` [5](#0-4) ), user-selected passphrases are frequently short/low-entropy, making them tractable to brute force once rate limiting is absent — directly analogous to the fixed 6-digit (1,000,000-value) JumpServer reset code combined with no throttling.

Until the keyring is unlocked, keychain operations that expose secret key material are gated by `is_keyring_locked()` checks scattered through `KeychainServer` methods (e.g., `check_keys`, `delete_all_keys`, `delete_key_by_fingerprint` all return `KEYCHAIN_ERR_LOCKED` when locked) [6](#0-5) . Successfully brute-forcing the passphrase via the unthrottled `unlock_keyring`/`validate_keyring_passphrase` RPCs removes this gate and lets the caller subsequently pull private keys/mnemonics through `get_key`/`get_all_private_keys`-style RPCs (`keychain_commands`) [7](#0-6) .

### Impact Explanation
An attacker with reach to the daemon's local websocket (any process able to establish the mTLS connection, e.g. a compromised local user/service on the same host that is not the wallet owner) can brute-force the master keyring passphrase offline-speed-limited only by RPC round-trip time, with no backoff or attempt cap on the server side. Successful guesses unlock the keyring and expose all managed private keys/mnemonics, enabling unauthorized signing of transactions and full compromise of every key stored in that keyring — i.e., unauthorized coin movement / fund theft, matching the "concrete unsigned or unauthorized coin movement" impact bar.

### Likelihood Explanation
Likelihood depends on passphrase strength and on how easily an attacker can reach the daemon websocket. The passphrase is optional and, when set, only enforces an 8-character minimum with no other complexity checks, so many real-world passphrases are within brute-forceable search space once no throttling exists. Reaching the local daemon requires possessing the daemon's client cert/local access, which lowers likelihood versus a purely remote bug, but this is explicitly documented in-repo as a "semi-trusted local/admin surface," not equivalent to full unlocked-keyring access — the passphrase is a meaningful additional control that this bug defeats.

### Recommendation
Add server-side rate limiting/lockout to `unlock_keyring` and `validate_keyring_passphrase` in `chia/daemon/server.py` (e.g., an attempt counter with exponential backoff or a hard cap similar to `KeychainMaxUnlockAttempts`, persisted per daemon session), and/or increase enforced minimum passphrase entropy so it is not optional/8-character-minimum by default.

### Proof of Concept
1. Set the keyring master passphrase to a low-entropy value (allowed since `passphrase_requirements()` reports `min_length: 8`, `is_optional: True`).
2. Open a websocket connection to the daemon using valid local TLS client certs (satisfying the daemon's mTLS gate, as any local RPC caller can).
3. Repeatedly send `validate_keyring_passphrase` (or `unlock_keyring`) requests with `key` guesses in a loop — as shown by `chia/_tests/core/daemon/test_daemon.py` RouteCase tests exercising these exact routes with wrong/correct passphrases [8](#0-7)  — with no delay or lockout triggered by the server across thousands of attempts.
4. Once a match is found, call `unlock_keyring` with the discovered passphrase to unlock the keyring, then call keychain RPCs (`get_key`, `get_all_private_keys`) to exfiltrate private keys/mnemonics.

### Citations

**File:** chia/daemon/server.py (L499-517)
```python
    async def keyring_status(self) -> dict[str, Any]:
        can_save_passphrase: bool = supports_os_passphrase_storage()
        user_passphrase_is_set: bool = Keychain.has_master_passphrase() and not using_default_passphrase()
        locked: bool = Keychain.is_keyring_locked()
        can_set_passphrase_hint: bool = True
        passphrase_hint: str = Keychain.get_master_passphrase_hint() or ""
        requirements: dict[str, Any] = passphrase_requirements()
        response: dict[str, Any] = {
            "success": True,
            "is_keyring_locked": locked,
            "can_save_passphrase": can_save_passphrase,
            "user_passphrase_is_set": user_passphrase_is_set,
            "can_set_passphrase_hint": can_set_passphrase_hint,
            "passphrase_hint": passphrase_hint,
            "passphrase_requirements": requirements,
        }
        # Help diagnose GUI launch issues
        self.log.debug(f"Keyring status: {response}")
        return response
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

**File:** chia/util/keyring_wrapper.py (L232-233)
```python
    def master_passphrase_is_valid(self, passphrase: str, force_reload: bool = False) -> bool:
        return self.keyring.check_passphrase(passphrase, force_reload=force_reload)
```

**File:** chia/daemon/keychain_server.py (L17-32)
```python
keychain_commands = [
    "add_private_key",
    "add_key",
    "check_keys",
    "delete_all_keys",
    "delete_key_by_fingerprint",
    "get_all_private_keys",
    "get_first_private_key",
    "get_key_for_fingerprint",
    "get_key",
    "get_keys",
    "get_public_key",
    "get_public_keys",
    "set_label",
    "delete_label",
]
```

**File:** chia/daemon/keychain_server.py (L248-286)
```python
    async def check_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        root_path = request.get("root_path", None)
        if root_path is None:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": "missing root_path"},
            }

        check_keys(Path(root_path))

        return {"success": True}

    async def delete_all_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        self.get_keychain_for_request(request).delete_all_keys()

        return {"success": True}

    async def delete_key_by_fingerprint(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        fingerprint = request.get("fingerprint", None)
        if fingerprint is None:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": "missing fingerprint"},
            }

        self.get_keychain_for_request(request).delete_key_by_fingerprint(fingerprint)

        return {"success": True}
```

**File:** chia/_tests/core/daemon/test_daemon.py (L1489-1537)
```python
@datacases(
    RouteCase(
        route="remove_keyring_passphrase",
        description="wrong current passphrase",
        request={"current_passphrase": "wrong passphrase"},
        response={"success": False, "error": "current passphrase is invalid"},
    ),
    RouteCase(
        route="remove_keyring_passphrase",
        description="incorrect type",
        request={"current_passphrase": True},
        response={"success": False, "error": "missing current_passphrase"},
    ),
    RouteCase(
        route="remove_keyring_passphrase",
        description="missing current passphrase",
        request={},
        response={"success": False, "error": "missing current_passphrase"},
    ),
    RouteCase(
        route="remove_keyring_passphrase",
        description="correct",
        request={"current_passphrase": "this is a passphrase"},
        response={"success": True, "error": None},
    ),
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
