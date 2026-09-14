Found a directly analogous naming-collision issue in `chia/daemon/keychain_server.py`'s `KeychainServer.get_keychain_for_request()`.

### Title
Local RPC caller can collide with the default keychain via crafted `kc_user`/`kc_service` values, granting cross-context key access - ([File: chia/daemon/keychain_server.py])

### Summary
`KeychainServer.get_keychain_for_request()` partitions keychain backends purely by concatenating attacker/client-supplied `kc_user` and `kc_service` strings from the request payload, and maps them to cached `Keychain` instances in `self._alt_keychains`. There is no validation that these strings don't collide with the daemon's own default keychain identity or with another legitimate caller's identity, mirroring the himmelblau bug class where an attacker-controlled name string is used to resolve into a privileged/other-owner's identity.

### Finding Description
`get_keychain_for_request()` builds a naive collision-prone lookup key: [1](#0-0) 

The function reads `kc_user`/`kc_service` straight out of the untrusted request dict, and if they exactly equal the *default* keychain's `user`/`service` fields, it silently returns the shared default `Keychain` instance instead of a per-request one. Otherwise it builds a cache key by naive string concatenation of `(user or "unnamed") + (service or "")`, with no separator — so `user="chia-testing"` / `service="1.8.0"` and `user="chia-testing1"` / `service="8.0"` collide into the same cache key/keychain, and any caller who can guess or replicate the default `user`/`service` values (or another caller's alt-keychain identity strings) is transparently routed to that same keychain instance, and thus that identity's private keys.

This is the same root-cause shape as the himmelblau CVE: an attacker-controlled name field is used, without namespacing or ownership checks, to resolve into another (potentially privileged) identity's backing store.

### Impact Explanation
Any local caller of the daemon's keychain RPC surface (`add_private_key`, `get_all_private_keys`, `get_first_private_key`, `get_key`, `delete_key_by_fingerprint`, etc., all dispatched through `handle_command()`) that can submit a request with matching `kc_user`/`kc_service` values gets full read/write access to the target keychain — including private key material (`get_all_private_keys` returns `pk`/`entropy` pairs) — of a different logical identity than the one it should be scoped to. Since this daemon path is explicitly the local privilege concentrator for keychain secrets, a collision here can expose or let an unprivileged local client tamper with another wallet identity's private keys (unsigned coin movement enablement), which is high severity if reached. [2](#0-1) 

### Likelihood Explanation
Reaching this requires a local daemon RPC/websocket caller (per `.cursor/context/daemon.md`, keychain commands are gated by `keychain_commands` membership before normal daemon dispatch), so it is not remotely exploitable over the P2P network. However, within the local RPC/daemon trust boundary — which the report class explicitly includes ("daemon/keychain/RPC authorization") — the collision requires no privilege beyond being able to open an authenticated local daemon connection, and no cryptographic guessing is needed if default/alt identity strings are predictable (e.g., default `user`/`service` are stable constants used across all daemon-managed services). [3](#0-2) 

### Recommendation
Namespace the alt-keychain cache key unambiguously (e.g., a delimiter or tuple key instead of raw string concatenation) and validate that client-supplied `kc_user`/`kc_service` cannot be crafted to alias the default keychain's identity or another caller's already-registered alt-keychain identity unless the caller is authorized for that identity.

### Proof of Concept
1. A local process obtains a daemon RPC connection (already authorized for basic daemon access).
2. It issues a `get_all_private_keys` (or `get_key_for_fingerprint`) request with `kc_user`/`kc_service` values chosen to exactly match the default keychain's `user`/`service` (or to string-concatenate to the same cache key as another already-instantiated alt keychain), per the collision path in `get_keychain_for_request()`. [3](#0-2) 
3. `KeychainServer` returns the colliding shared `Keychain` instance, and `get_all_private_keys()` / `get_first_private_key()` return that identity's private key entropy to the caller. [4](#0-3)

### Citations

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
