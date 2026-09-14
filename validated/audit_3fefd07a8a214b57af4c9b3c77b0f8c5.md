### Title
Unauthorized cross-namespace private key disclosure via attacker-controlled `kc_user`/`kc_service` in daemon KeychainServer - ([File: chia/daemon/keychain_server.py])

### Summary
The Jenkins CVE lets a caller with limited permission supply an attacker-chosen credential ID and an attacker-chosen *user* identifier to reach into another user's private per-user credentials store, because the target store is selected purely from caller-supplied identifiers with no ownership check. The chia daemon's `KeychainServer` has the same bug-class shape: any local RPC caller connected to the daemon websocket can request a keychain command while supplying arbitrary `kc_user`/`kc_service` strings, and `KeychainServer.get_keychain_for_request()` uses those caller-supplied strings — with no validation, allowlist, or ownership check — to select or lazily create the `Keychain` namespace that the request will operate against.

### Finding Description
`KeychainProxy.format_request()` injects `kc_user`/`kc_service` into every keychain RPC payload only when the proxy was itself constructed with a `user`/`service` [1](#0-0) , but nothing on the daemon side restricts these fields to values the *caller* actually owns — they are read straight out of the untrusted request dict:

```
def get_keychain_for_request(self, request: dict[str, Any]) -> Keychain:
    user = request.get("kc_user", self._default_keychain.user)
    service = request.get("kc_service", self._default_keychain.service)
    ...
    key = (user or "unnamed") + (service or "")
    if key in self._alt_keychains:
        keychain = self._alt_keychains[key]
    else:
        keychain = Keychain(user=user, service=service)
        self._alt_keychains[key] = keychain
    return keychain
``` [2](#0-1) 

Every keychain command dispatched through `KeychainServer.handle_command()` — `get_key`, `get_keys`, `get_key_for_fingerprint`, `get_all_private_keys`, `get_first_private_key`, `add_key`, `delete_key_by_fingerprint`, etc. — calls `get_keychain_for_request(data)` first and then operates on whatever `Keychain` namespace results [3](#0-2) . `run_request()` follows the same pattern for the newer typed commands [4](#0-3) .

Critically, private-key-returning handlers such as `get_key_for_fingerprint()` and `get_all_private_keys()` return raw public-key hex plus mnemonic entropy hex for whatever `Keychain(user, service)` was resolved from the request, with no check that the requester is entitled to that particular `user`/`service` partition [5](#0-4) [6](#0-5) . `Keychain` itself is built directly from these caller-supplied `user`/`service` strings, which back the OS keyring/file-keyring partitioning [7](#0-6) .

The daemon documentation itself acknowledges the design: "Keychain instances can have user and service strings associated with them... we attempt to maintain a mapping of user/service pairs to their corresponding Keychain," and separately warns that "Adding a keychain RPC requires updating this list... Public-key responses intentionally override to_json_dict() to expose only fingerprint/public_key/label" for the *public*-key path, implying the private-key path is trusted to be scoped correctly by the caller — which it is not [8](#0-7) .

Unlike `start_service`/`launch_service`, which gate service names through `validate_service()` [9](#0-8) , there is no equivalent allowlist or authorization check constraining `kc_user`/`kc_service` values for keychain commands.

### Impact Explanation
Any local process that can open the daemon's mTLS websocket (e.g., any other locally running Chia service, a co-located GUI helper, or another local application presenting a valid daemon client cert used by any Chia component) can send `get_key_for_fingerprint` or `get_all_private_keys` with a different `kc_user`/`kc_service` pair than its own and, if a keyring namespace exists for that pair (e.g., another version's keys, another CHIA_ROOT profile, or a testing/automation keychain co-resident on the same host), retrieve that namespace's private key material (entropy/mnemonic + public key) without any check that the caller is the legitimate owner of that partition. Recovering a wallet's private key directly enables unauthorized signing of spend bundles and full unauthorized movement of any coins controlled by that key — this satisfies the "concrete unsigned or unauthorized coin movement" bar.

### Likelihood Explanation
This requires the ability to talk to the local daemon websocket, which is gated by mutual TLS using daemon-issued certs rather than by OS-user identity [10](#0-9) . Any locally-installed Chia component (farmer, wallet, harvester, or another local service instance sharing the daemon certs) already qualifies as such a caller, and the `kc_user`/`kc_service` values are unauthenticated, free-form strings under caller control with no server-side ownership enforcement, so exploitation only requires knowledge or guessing of another partition's user/service label (e.g., predictable defaults like `testing-1.8.0`/`chia-testing-1.8.0` used in `block_tools.py`) [11](#0-10) .

### Recommendation
Restrict `get_keychain_for_request()` to only the daemon's own default keychain (or to a server-side allowlist of `kc_user`/`kc_service` pairs the operator explicitly configured), rather than trusting caller-supplied strings verbatim. If multi-namespace keychain support must remain, bind each daemon connection/session to a single authorized `kc_user`/`kc_service` pair at connection time (not per-request) and reject any request whose `kc_user`/`kc_service` differs from the pair the connection was authorized for.

### Proof of Concept
1. Start the chia daemon normally (default keychain populated with a real wallet key).
2. From any process holding a valid daemon client certificate, open a websocket to the daemon and send:
```
{"command": "get_key_for_fingerprint",
 "data": {"fingerprint": null, "private": true, "kc_user": "testing-1.8.0", "kc_service": "chia-testing-1.8.0"},
 "origin": "attacker", "destination": "daemon"}
```
3. `get_keychain_for_request()` resolves a `Keychain(user="testing-1.8.0", service="chia-testing-1.8.0")` distinct from the caller's own keychain and returns that partition's private key entropy/public key in the response, per `KeychainServer.get_key_for_fingerprint()` [6](#0-5) , without verifying the requester is entitled to that partition.
4. Repeat with other guessed/enumerated `kc_user`/`kc_service` strings to enumerate additional keyring partitions co-resident on the host.

### Citations

**File:** chia/daemon/keychain_proxy.py (L75-86)
```python
    def format_request(self, command: str, data: dict[str, Any]) -> WsRpcMessage:
        """
        Overrides DaemonProxy.format_request() to add keychain-specific RPC params
        """
        if data is None:
            data = {}

        if self.keychain_user or self.keychain_service:
            data["kc_user"] = self.keychain_user
            data["kc_service"] = self.keychain_service

        return super().format_request(command, data)
```

**File:** chia/daemon/keychain_server.py (L154-172)
```python
    def get_keychain_for_request(self, request: dict[str, Any]) -> Keychain:
        """
        Keychain instances can have user and service strings associated with them.
        The keychain backends ultimately point to the same data stores, but the user
        and service strings are used to partition those data stores. We attempt to
        maintain a mapping of user/service pairs to their corresponding Keychain.
        """
        user = request.get("kc_user", self._default_keychain.user)
        service = request.get("kc_service", self._default_keychain.service)
        if user == self._default_keychain.user and service == self._default_keychain.service:
            keychain = self._default_keychain
        else:
            key = (user or "unnamed") + (service or "")
            if key in self._alt_keychains:
                keychain = self._alt_keychains[key]
            else:
                keychain = Keychain(user=user, service=service)
                self._alt_keychains[key] = keychain
        return keychain
```

**File:** chia/daemon/keychain_server.py (L174-209)
```python
    async def handle_command(self, command: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            if command == "add_private_key":
                data["private"] = True
                data["mnemonic_or_pk"] = data.get("mnemonic_or_pk", data.get("mnemonic", None))
                return await self.add_key(data)
            elif command == "add_key":
                return await self.add_key(data)
            elif command == "check_keys":
                return await self.check_keys(data)
            elif command == "delete_all_keys":
                return await self.delete_all_keys(data)
            elif command == "delete_key_by_fingerprint":
                return await self.delete_key_by_fingerprint(data)
            elif command == "get_all_private_keys":
                return await self.get_all_private_keys(data)
            elif command == "get_first_private_key":
                return await self.get_first_private_key(data)
            elif command == "get_key_for_fingerprint":
                return await self.get_key_for_fingerprint(data)
            elif command == "get_key":
                return await self.run_request(data, GetKeyRequest)
            elif command == "get_keys":
                return await self.run_request(data, GetKeysRequest)
            elif command == "get_public_key":
                return await self.run_request(data, GetPublicKeyRequest)
            elif command == "get_public_keys":
                return await self.run_request(data, GetPublicKeysRequest)
            elif command == "set_label":
                return await self.run_request(data, SetLabelRequest)
            elif command == "delete_label":
                return await self.run_request(data, DeleteLabelRequest)
            return {}
        except Exception as e:
            log.exception(e)
            return {"success": False, "error": str(e), "command": command}
```

**File:** chia/daemon/keychain_server.py (L288-303)
```python
    async def run_request(self, request_dict: dict[str, Any], request_type: type[Any]) -> dict[str, Any]:
        keychain = self.get_keychain_for_request(request_dict)
        if keychain.is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        try:
            request = request_type.from_json_dict(request_dict)
        except Exception as e:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": str(e)},
            }

        try:
            return {"success": True, **request.run(keychain).to_json_dict()}
```

**File:** chia/daemon/keychain_server.py (L317-341)
```python
    async def get_all_private_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        all_keys: list[dict[str, Any]] = []
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        private_keys = self.get_keychain_for_request(request).get_all_private_keys()
        for sk, entropy in private_keys:
            all_keys.append({"pk": bytes(sk.get_g1()).hex(), "entropy": entropy.hex()})

        return {"success": True, "private_keys": all_keys}

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

**File:** chia/util/keychain.py (L36-38)
```python
CURRENT_KEY_VERSION = "1.8"
DEFAULT_USER = f"user-chia-{CURRENT_KEY_VERSION}"  # e.g. user-chia-1.8
DEFAULT_SERVICE = f"chia-{DEFAULT_USER}"  # e.g. chia-user-chia-1.8
```

**File:** .cursor/context/daemon.md (L58-61)
```markdown
- There is no peer-style protocol enum, streamable binary framing, node-type map,
  or rate limiter here. The primary gate is mutual TLS using daemon private
  certs plus local config (`self_hostname`, `daemon_port`,
  `daemon_max_message_size`, `daemon_heartbeat`).
```

**File:** .cursor/context/daemon.md (L81-95)
```markdown
## Keychain And Secret Handling

- Keychain commands are intercepted before normal daemon command dispatch by
  membership in `keychain_commands`. Adding a keychain RPC requires updating this
  list, `KeychainServer.handle_command()`, and usually `KeychainProxy`.
- `KeychainServer.run_request()` uses streamable JSON conversion for newer
  typed request/response classes (`get_key`, `get_keys`, public-key and label
  operations). Older commands are hand-parsed dictionaries and have more varied
  error shapes.
- Public-key responses intentionally override `to_json_dict()` to expose only
  `fingerprint`, `public_key`, and `label`; do not reuse private-key response
  shapes for public-only APIs.
- Remote private-key reads return public key hex plus entropy hex. The proxy
  rebuilds the mnemonic and private key and verifies the derived G1 matches the
  returned public key before handing it to callers.
```

**File:** chia/daemon/server.py (L47-47)
```python
from chia.util.service_groups import validate_service
```

**File:** chia/simulator/block_tools.py (L494-496)
```python
                keychain_proxy = await connect_to_keychain_and_validate(
                    self.root_path, self.log, user="testing-1.8.0", service="chia-testing-1.8.0"
                )
```
