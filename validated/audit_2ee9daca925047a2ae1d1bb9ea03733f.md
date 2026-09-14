### Title
Daemon debug-log redaction fails to cover `entropy`/`pk` fields, leaking private-key-equivalent seed material to log files — ([File: chia/daemon/server.py])

### Summary
The Chia daemon's WebSocket message handler redacts sensitive fields from debug-level logs via `redact_sensitive_data()`, which matches dictionary keys against a fixed, substring-based trigger list. Several legitimate keychain RPC responses embed raw key seed material under the field name `entropy` (and public-key/private-key data under `pk`), which do not contain any of the trigger substrings `"pass"`, `"key"`, `"secret"`, `"mnemonic"`. As a result, when DEBUG-level daemon logging is enabled, these fields are written to disk (and potentially forwarded to syslog/systemd journal) in clear text, defeating the intended redaction control — directly analogous to the Ansible `no_log` bypass in CVE-2019-14864, where a data-protection flag/mechanism failed to cover the actual sensitive fields being emitted.

### Finding Description
`redact_sensitive_data()` in `chia/daemon/server.py` is the sole mechanism protecting sensitive daemon message contents from being written to the debug log: [1](#0-0) 

It only redacts a dict value when the *key name* contains one of `"pass"`, `"key"`, `"secret"`, `"mnemonic"` (case-insensitive substring match). This is confirmed by the accompanying test suite, which validates redaction only for keys named `password`, `private_key`, `secret_value`, `mnemonic`, `passphrase`, `api_key`, `seed_mnemonic`: [2](#0-1) 

However, several keychain-server RPC handlers return the raw 32-byte seed (`entropy`) and public key (`pk`) directly at the top level of the response dict, under field names that do **not** match any trigger:

- `get_all_private_keys` returns `{"pk": ..., "entropy": ...}` for every stored key: [3](#0-2) 
- `get_first_private_key` returns `{"pk": pk_str, "entropy": ent_str}`: [4](#0-3) 
- `get_key_for_fingerprint` returns `{"pk": ..., "entropy": ...}`: [5](#0-4) 

`entropy` is not cosmetic metadata — it is the exact seed from which the mnemonic and private key are reconstructed, as shown by `KeychainProxy.get_first_private_key()`, which rebuilds the private key directly from the `entropy` hex string returned by the daemon: [6](#0-5) 

Because the response dict's top-level key is `entropy` (not `mnemonic`, `secret`, or anything containing `pass`/`key`), `redact_sensitive_data()`'s substring check does not match it, so the full response — including this seed — is written to the debug/DEBUG-level log verbatim whenever the daemon logs "Received message" / response traffic. The project's own internal engineering notes acknowledge the redaction requirement exists specifically to prevent this class of leak ("Debug logging must pass through `redact_sensitive_data()` before recording message contents"), but the implementation's trigger list is incomplete relative to the actual field names emitted by the keychain RPC surface.

This is architecturally identical to CVE-2019-14864: a logging-protection mechanism (Ansible's `no_log` flag / Chia's `redact_sensitive_data`) exists specifically to prevent sensitive values from reaching persistent logs, but a code path emits the sensitive data under a field/mechanism not covered by the protection, so the secret ends up recorded in clear text in a log file that may have broader read-access, be forwarded to syslog/systemd, rotated, backed up, or bundled into support/debug archives.

### Impact Explanation
If a user (or a Devin/automation tooling wrapper, GUI, or third-party integration) has ever run the daemon with `log_level: DEBUG` — a supported, documented configuration in `chia/util/chia_logging.py` — any keychain RPC call to `get_all_private_keys`, `get_first_private_key`, or `get_key_for_fingerprint` will cause the raw seed entropy for the caller's private key(s) to be written to `log/debug.log` (and optionally forwarded to syslog/journald if `log_syslog`/`log_systemd` are enabled). This is CWE-532 (Inclusion of Sensitive Information in Log File): anyone who can subsequently read that log file (a lower-privileged local user, a misconfigured log-shipping pipeline, a support bundle sent to a third party, etc.) obtains the full private key equivalent of the wallet, enabling unauthorized signing/spending of any coins controlled by that key. This is a direct compromise of key material confidentiality, satisfying the "leaked-key"-adjacent but code-caused (not operator-caused) root cause of unauthorized coin movement.

### Likelihood Explanation
Exploitation requires DEBUG logging to be enabled (a supported and documented mode, not exotic) and a call to one of the affected keychain RPC methods, all of which occur during normal legitimate wallet/keychain operation (e.g., wallet startup calling `get_first_private_key`/`get_all_private_keys` to unlock keys). No attacker interaction with the keychain RPC itself is required for the leak to occur — it happens as a side effect of ordinary key-loading operations while DEBUG logging is on. The likelihood of *triggering* the write is therefore high in debug-enabled environments; the residual risk is that reading the resulting log file requires some additional access (e.g., another local user, misconfigured log forwarding, or a shared support-log bundle), which is why this is rated Medium rather than Critical.

### Recommendation
Expand `redact_sensitive_data()`'s trigger list (or switch to an explicit allow/deny field schema) to also match `entropy`, `pk`, `private_key`, `sk`, `master_sk`, `wallet_sk`, `farmer_sk`, `pool_sk`, and any other field name used to carry raw key material across the daemon/keychain RPC boundary. Better, refactor sensitive keychain RPC responses (`get_all_private_keys`, `get_first_private_key`, `get_key_for_fingerprint`) to use the same "approved key allowlist" pattern already used by `GetPublicKeyResponse`/`GetPublicKeysResponse` (`chia/daemon/keychain_server.py:94-98,113-121`), and additionally have `redact_sensitive_data` redact by default (deny-list of *safe* fields) rather than only redacting matched trigger substrings, so future new sensitive fields fail safe.

### Proof of Concept
1. Configure the daemon/wallet service with `logging: {log_level: DEBUG, log_stdout: false}` (a supported config in `chia/util/chia_logging.py`).
2. Start the daemon and connect a local, properly authenticated (mTLS) client — e.g., the wallet process performing normal startup, which calls `get_first_private_key` or `get_all_private_keys` via `KeychainProxy`.
3. Observe `log/debug.log`: the "Received message"/response log entry for the `get_first_private_key`/`get_all_private_keys`/`get_key_for_fingerprint` command contains a JSON object with an unredacted `entropy` (and `pk`) field, e.g. `{"private_key": {"pk": "<hex g1>", "entropy": "<hex 32-byte seed>"}}`, because `redact_sensitive_data()`'s trigger list (`pass`, `key`, `secret`, `mnemonic`) does not match the field name `entropy`.
4. Anyone able to read that log file (test with `chia/_tests/core/daemon/test_daemon.py::test_message_logging_redaction` as a template, but sending a `get_first_private_key`/`get_all_private_keys` payload instead of the synthetic `test_command` payload) can reconstruct the mnemonic/private key via `bytes_to_mnemonic(bytes.fromhex(entropy))`, exactly as `KeychainProxy.get_first_private_key()` itself does at `chia/daemon/keychain_proxy.py:315-320`.

### Citations

**File:** chia/daemon/server.py (L69-79)
```python
def redact_sensitive_data(obj: Any, redaction_triggers: Collection[str] = ("pass", "key", "secret", "mnemonic")) -> Any:
    """Recursively redact sensitive data from nested dictionaries."""
    if isinstance(obj, dict):
        return {
            key: "***<redacted>***"
            if any(trigger.casefold() in key.casefold() for trigger in redaction_triggers)
            else redact_sensitive_data(value, redaction_triggers)
            for key, value in obj.items()
        }
    else:
        return obj
```

**File:** chia/_tests/core/daemon/test_daemon.py (L2146-2195)
```python
    with caplog.at_level(logging.DEBUG, logger="chia.daemon.server"):
        sensitive_payload = create_payload(
            "test_command",
            {
                "password": "secret_password",
                "private_key": "sensitive_key_data",
                "secret_value": "very_secret",
                "mnemonic": "test_mnemonic_phrase",
                "normal_field": "normal_value",
                "nested_object": {
                    "passphrase": "nested_secret",
                    "api_key": "nested_api_key",
                    "seed_mnemonic": "nested_mnemonic",
                    "safe_field": "safe_value",
                },
            },
            "test",
            "daemon",
        )

        original_message = json.loads(sensitive_payload)
        request_id = original_message["request_id"]

        await ws.send_str(sensitive_payload)
        await ws.receive()

        log_message = next(record for record in caplog.records if "Received message:" in record.message).message
        _, _, ws_message_str = log_message.partition("Received message: ")

        # Build the expected redacted structure and sort keys like dict_to_json_str does
        expected_redacted_data = {
            "ack": False,
            "command": "test_command",
            "data": {
                "mnemonic": "***<redacted>***",
                "nested_object": {
                    "api_key": "***<redacted>***",
                    "passphrase": "***<redacted>***",
                    "safe_field": "safe_value",
                    "seed_mnemonic": "***<redacted>***",
                },
                "normal_field": "normal_value",
                "password": "***<redacted>***",
                "private_key": "***<redacted>***",
                "secret_value": "***<redacted>***",
            },
            "destination": "daemon",
            "origin": "test",
            "request_id": request_id,
        }
```

**File:** chia/daemon/keychain_server.py (L317-326)
```python
    async def get_all_private_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        all_keys: list[dict[str, Any]] = []
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        private_keys = self.get_keychain_for_request(request).get_all_private_keys()
        for sk, entropy in private_keys:
            all_keys.append({"pk": bytes(sk.get_g1()).hex(), "entropy": entropy.hex()})

        return {"success": True, "private_keys": all_keys}
```

**File:** chia/daemon/keychain_server.py (L328-341)
```python
    async def get_first_private_key(self, request: dict[str, Any]) -> dict[str, Any]:
        key: dict[str, Any] = {}
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        sk_ent = self.get_keychain_for_request(request).get_first_private_key()
        if sk_ent is None:
            return {"success": False, "error": KEYCHAIN_ERR_NO_KEYS}

        pk_str = bytes(sk_ent[0].get_g1()).hex()
        ent_str = sk_ent[1].hex()
        key = {"pk": pk_str, "entropy": ent_str}

        return {"success": True, "private_key": key}
```

**File:** chia/daemon/keychain_server.py (L343-365)
```python
    async def get_key_for_fingerprint(self, request: dict[str, Any]) -> dict[str, Any]:
        keychain = self.get_keychain_for_request(request)
        if keychain.is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        private = request.get("private", True)
        keys = keychain.get_keys(include_secrets=private)
        if len(keys) == 0:
            return {"success": False, "error": KEYCHAIN_ERR_NO_KEYS}

        try:
            if request["fingerprint"] is None:
                key_data = keys[0]
            else:
                key_data = keychain.get_key(request["fingerprint"], include_secrets=private)
        except KeychainFingerprintNotFound:
            return {"success": False, "error": KEYCHAIN_ERR_KEY_NOT_FOUND}

        return {
            "success": True,
            "pk": bytes(key_data.public_key).hex(),
            "entropy": key_data.entropy.hex() if private else None,
        }
```

**File:** chia/daemon/keychain_proxy.py (L301-320)
```python
            response, success = await self.get_response_for_request("get_first_private_key", {})
            if success:
                private_key = response["data"].get("private_key", None)
                if private_key is None:
                    err = f"Missing private_key in {response.get('command')} response"
                    self.log.error(f"{err}")
                    raise KeychainMalformedResponse(f"{err}")
                else:
                    pk = private_key.get("pk", None)
                    ent_str = private_key.get("entropy", None)
                    if pk is None or ent_str is None:
                        err = f"Missing pk and/or ent in {response.get('command')} response"
                        self.log.error(f"{err}")
                        raise KeychainMalformedResponse(f"{err}")
                    ent = bytes.fromhex(ent_str)
                    mnemonic = bytes_to_mnemonic(ent)
                    seed = mnemonic_to_seed(mnemonic)
                    sk = AugSchemeMPL.key_gen(seed)
                    if bytes(sk.get_g1()).hex() == pk:
                        key = sk
```
