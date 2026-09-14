## Analog Found

### Title
Keychain user/service identifier collision in `KeychainServer.get_keychain_for_request()` allows local RPC callers to read another user's stored keys - (File: `chia/daemon/keychain_server.py`)

### Summary
`chia/daemon/keychain_server.py`'s `KeychainServer.get_keychain_for_request()` derives the cache key for a per-tenant `Keychain` instance by naively string-concatenating the caller-supplied `kc_user` and `kc_service` fields with no separator or delimiter, and using that same concatenation to select/instantiate the underlying `Keychain(user=user, service=service)` storage-backend object.

### Finding Description
Any local process holding a daemon TLS client cert can open a websocket to the daemon and send a keychain command (`get_key`, `get_keys`, `get_key_for_fingerprint`, `get_all_private_keys`, etc.) carrying arbitrary `kc_user`/`kc_service` strings, which `KeychainProxy.format_request()` injects verbatim into every request when a proxy has non-default user/service set [1](#0-0) .

On the server side, `get_keychain_for_request()` builds the lookup/creation key as:
```
key = (user or "unnamed") + (service or "")
```
and caches or looks up a `Keychain(user=user, service=service)` instance under that plain concatenation [2](#0-1) . Because there is no delimiter between `user` and `service`, distinct `(kc_user, kc_service)` pairs can produce an identical concatenated key — e.g. `("Alice", "X")` and `("AliceX", "")` both resolve to `"AliceX"`, and `("", "AliceX")` and `("Alice", "X")` land in the same collision space with `"unnamed"` substitution rules. Since `Keychain(user=..., service=...)` is the OS-keyring/file-keyring partition identifier used by `chia/util/keychain.py` (`DEFAULT_USER`/`DEFAULT_SERVICE` constants) [3](#0-2) , a caller who can predict or brute-force another tenant's `(user, service)` string pair can construct a different pair that collides in the cache and is served the *other* tenant's already-instantiated `Keychain`, or (on first creation) the same underlying storage keyring entry.

Any of the keychain RPC handlers that operate on the resolved keychain — `get_all_private_keys` (returns raw private-key/entropy pairs) [4](#0-3) , `get_key_for_fingerprint` (returns pk + entropy, from which the mnemonic/private key is reconstructed by `KeychainProxy.get_key_for_fingerprint()`) [5](#0-4) [6](#0-5) , or `get_keys`/`get_key` — will then hand back another tenant's private key material to the colliding caller.

### Impact Explanation
This is a direct analog to the Nextcloud CVE-2023-35928 bug class: a user-supplied storage-partition identifier is mixed insecurely, letting one authenticated-but-lower-trust party read another party's stored credentials. Here, the credentials are Chia mnemonic-derived private keys — full custody of any coins/CATs/NFTs owned by that identity. This is a High severity local-authorization boundary bypass; the impact is complete key/credential disclosure across the daemon's keychain-partitioning boundary, which is the exact mechanism (`kc_user`/`kc_service`) added specifically to keep multiple keychains isolated from each other.

### Likelihood Explanation
Exploitation requires only a local peer that already holds a valid daemon TLS client certificate (any locally co-installed chia service, CLI, or third-party integration using `KeychainProxy`/`DaemonProxy`), which is the same trust tier the rules classify as an in-scope "local RPC caller." No wallet unlock, private key, or elevated OS privilege is needed beyond that — the caller only needs to guess or already know another tenant's `kc_user`/`kc_service` strings (which are often derived from predictable service names such as `f"chia-{DEFAULT_USER}"`), then craft a colliding pair.

### Recommendation
Change `get_keychain_for_request()` to build the cache key from a proper tuple `(user, service)` (or a delimiter that cannot appear in either field, e.g. `f"{user!r}\x00{service!r}"`) instead of naive string concatenation, so distinct user/service pairs can never alias to the same `Keychain` instance or underlying OS keyring entry.

### Proof of Concept
1. Daemon has previously served a keychain request from a legitimate service with `kc_user="wallet"`, `kc_service="user-svc"`, which creates/caches `Keychain(user="wallet", service="user-svc")` under key `"walletuser-svc"`.
2. A separate local client connects to the daemon (has a valid daemon cert but does not itself possess the target's keys) and sends a `get_all_private_keys` command with `kc_user="walletuser-svc"`, `kc_service=""`.
3. `get_keychain_for_request()` computes `key = "walletuser-svc" + "" = "walletuser-svc"`, matching the existing cache entry, and returns the same `Keychain` object created for the first tenant [7](#0-6) .
4. `get_all_private_keys()` runs against that keychain and returns the first tenant's `pk`/`entropy` pairs to the second, unrelated caller [4](#0-3) .

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

**File:** chia/daemon/keychain_proxy.py (L369-396)
```python
        else:
            response, success = await self.get_response_for_request(
                "get_key_for_fingerprint", {"fingerprint": fingerprint, "private": private}
            )
            if success:
                pk = response["data"].get("pk", None)
                ent = response["data"].get("entropy", None)
                if pk is None or (private and ent is None):
                    err = f"Missing pk and/or ent in {response.get('command')} response"
                    self.log.error(f"{err}")
                    raise KeychainMalformedResponse(f"{err}")
                elif private:
                    if ent is None:
                        err = f"Missing ent in {response.get('command')} response"
                        self.log.error(f"{err}")
                        raise KeychainMalformedResponse(f"{err}")

                    mnemonic = bytes_to_mnemonic(bytes.fromhex(ent))
                    seed = mnemonic_to_seed(mnemonic)
                    private_key = AugSchemeMPL.key_gen(seed)
                    if bytes(private_key.get_g1()).hex() == pk:
                        key = private_key
                    else:
                        err = "G1Elements don't match"
                        self.log.error(f"{err}")
                else:
                    key = G1Element.from_bytes(bytes.fromhex(pk))
            else:
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

**File:** chia/util/keychain.py (L36-39)
```python
CURRENT_KEY_VERSION = "1.8"
DEFAULT_USER = f"user-chia-{CURRENT_KEY_VERSION}"  # e.g. user-chia-1.8
DEFAULT_SERVICE = f"chia-{DEFAULT_USER}"  # e.g. chia-user-chia-1.8
MAX_KEYS = 101
```
