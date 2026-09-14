## Analog Found: Keychain Cache Key Collision in `KeychainServer.get_keychain_for_request`

### Title
Improper Access Control via Keychain Cache Key Collision - (File: `chia/daemon/keychain_server.py`)

### Summary
`KeychainServer` caches per-user/per-service `Keychain` instances in `_alt_keychains`, keyed by naively concatenating the `kc_user` and `kc_service` fields taken directly from an incoming daemon request. Because the two fields are concatenated without a delimiter, two different `(user, service)` pairs can produce the identical cache key, causing a request for one user/service pair to be served the cached `Keychain` instance that was created for a *different* user/service pair. This mirrors the underlying Django bug class (`LazyUser` in `AuthenticationMiddleware` caching identity incorrectly across requests, letting one authenticated caller act with another's cached identity), except here the object silently shared across mismatched "identities" is a `Keychain` — the private-key/label store for a partitioned keyring namespace.

### Finding Description
`get_keychain_for_request()` builds the cache key like this: [1](#0-0) 

The key is computed as `(user or "unnamed") + (service or "")` with no separator, so `user="alicewallet", service=""` and `user="alice", service="wallet"` both hash to the cache key `"alicewallet"`. Whichever request populates `_alt_keychains["alicewallet"]` first "wins": every subsequent request from the *other* logical user/service pair transparently receives the already-cached `Keychain` object instead of constructing/loading its own keyring namespace.

Every keychain RPC command (`add_key`, `get_keys`, `get_all_private_keys`, `delete_key_by_fingerprint`, `delete_all_keys`, `set_label`, etc.) resolves its `Keychain` through this same function, so the collision affects the entire keychain command surface exposed over the local daemon websocket: [2](#0-1) 

This directly parallels the CVE-2007-0405 bug class: an identity-selection cache keyed insufficiently, allowing an authenticated-but-distinct caller to be handed another identity's cached backing object and therefore its privileges/data.

### Impact Explanation
A caller who can reach the daemon's keychain commands with a colliding `kc_user`/`kc_service` pair is served another partition's `Keychain` instance. Depending on request ordering/timing, this can result in:
- Reading another partition's private keys/mnemonics/labels via `get_all_private_keys`, `get_key`, `get_keys`.
- Writing/deleting keys (`add_key`, `delete_key_by_fingerprint`, `delete_all_keys`) against a keyring namespace the caller did not intend to target, corrupting or destroying another partition's keys.

Since `chia/daemon/` is explicitly the keychain RPC authority (`KeychainServer` maps `kc_user`/`kc_service` pairs to cached `Keychain` instances, per [3](#0-2) ), any code path (GUI, CLI, third-party service, or `KeychainProxy` remote client) that allows a user-influenced `kc_user`/`kc_service` value to reach this API is exposed to cross-partition key confusion — an improper access control condition matching the report's bug class.

### Likelihood Explanation
Exploitability depends on whether `kc_user`/`kc_service` values reaching `KeychainServer` can be influenced by a lower-privileged caller (e.g., a local service registered with the daemon, or any caller of remote keychain operations that can pass a custom `kc_user`/`kc_service`). The daemon is a semi-trusted local/admin surface, but the module's own authority doc singles out keychain user/service partitioning as a designed multi-tenant boundary, so an unauthenticated-relative-to-other-partitions collision is a realistic misuse even under the existing TLS-gated local trust model.

### Recommendation
Use an unambiguous composite key (e.g., a tuple `(user, service)` or a delimiter guaranteed not to appear in either field, such as `f"{user!r}\x00{service!r}"`) instead of naive string concatenation when populating/looking up `_alt_keychains`.

### Proof of Concept
1. Send a daemon keychain command with `kc_user="alice"`, `kc_service="wallet"` and `add_key` a private key/mnemonic — this creates and caches `Keychain(user="alice", service="wallet")` under key `"alicewallet"`.
2. Send a second daemon keychain command with `kc_user="alicewallet"`, `kc_service=""` calling `get_all_private_keys` — `get_keychain_for_request()` computes key `"alicewallet"` again, finds it in `_alt_keychains`, and returns the *first* caller's `Keychain` instance, exposing/operating on the first user's keys/mnemonics under a supposedly distinct `(user, service)` identity. [4](#0-3)

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

**File:** .cursor/context/daemon.md (L23-25)
```markdown
- `KeychainServer` is the remote keychain authority behind daemon commands. It
  maps request `kc_user`/`kc_service` pairs to cached `Keychain` instances and
  normalizes keychain exceptions into daemon JSON errors.
```
