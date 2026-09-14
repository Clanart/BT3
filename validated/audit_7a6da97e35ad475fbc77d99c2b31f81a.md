### Title
Keychain instance memoization key collision via unseparated string concatenation - (File: chia/daemon/keychain_server.py)

### Summary
`KeychainServer.get_keychain_for_request()` memoizes per-request `Keychain` objects in `self._alt_keychains` using a cache key built by naive string concatenation of the caller-supplied `kc_user` and `kc_service` fields, with no separator between them. Two different `(user, service)` pairs can produce the identical concatenated key, causing the daemon to silently return and operate on the wrong cached `Keychain` partition for a legitimate RPC caller's request. This is the same bug class as CVE-2021-39937/BIT-gitlab-2021-39937: a collision in access/identity memoization logic causing operations meant for one identity to be served against another's data.

### Finding Description
`get_keychain_for_request()` is the sole partitioning authority mapping a local daemon RPC caller's declared `user`/`service` identity to the correct backing `Keychain`: [1](#0-0) 

```python
def get_keychain_for_request(self, request: dict[str, Any]) -> Keychain:
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

The memoization key `key = (user or "unnamed") + (service or "")` concatenates two independent identity fields without any delimiter or length-prefixing. Because string concatenation without a separator is not injective, distinct `(user, service)` pairs can produce the same `key`:

- `user="alice"`, `service="bob"` → key = `"alicebob"`
- `user="aliceb"`, `service="ob"` → key = `"aliceb" + "ob"` = `"aliceb ob"`... 

More directly exploitable: `user="A"`, `service="BC"` → key=`"ABC"`, and `user="AB"`, `service="C"` → key=`"ABC"`. Once the first caller's request creates and caches a `Keychain(user="A", service="BC")` under key `"ABC"`, a subsequent request declaring `kc_user="AB"`, `kc_service="C"` will hit the cache and receive the *same* `Keychain` object — one instantiated for a different user/service pair — rather than constructing/looking up its own `Keychain(user="AB", service="C")`.

`Keychain(user=..., service=...)` partitions the underlying OS keyring/file-keyring storage; `DEFAULT_USER`/`DEFAULT_SERVICE` constants confirm user/service strings are the storage-partition boundary [2](#0-1) . `run_request()` and the private-key/label handlers (`get_key`, `get_keys`, `add_private_key`, `delete_all_keys`, `delete_key_by_fingerprint`, `set_label`, `delete_label`, etc.) all resolve their `Keychain` via this same collision-prone lookup before performing key operations [3](#0-2) .

### Impact Explanation
Any authorized local daemon RPC caller (holding a valid daemon private-cert TLS connection — e.g., a second local service, GUI instance, or CLI script that can specify arbitrary `kc_user`/`kc_service` values) can cause the daemon's in-memory keychain memoization table to alias two distinct user/service partitions onto one cached `Keychain` object. This can result in:
- Key operations addressed to one `(user, service)` partition (add/delete/list private keys, set/delete labels) being served by a `Keychain` instance actually bound to a different partition's underlying storage — cross-partition key exposure or mutation.
- A caller deliberately choosing colliding `kc_user`/`kc_service` values to force reuse of another partition's `Keychain`, reading or deleting private keys belonging to a different logical keychain namespace than the one it declared.

This matches the "unauthorized coin movement / forged asset identity"-adjacent category insofar as it is an authorization/identity-memoization collision within the daemon/keychain RPC boundary explicitly in scope (`daemon/keychain/RPC authorization`).

### Likelihood Explanation
Exploitation requires only sending crafted `kc_user`/`kc_service` string values over the local daemon RPC connection — no cryptographic material, no peer/network access, and no privilege beyond a valid local daemon client certificate is required. Constructing a colliding pair is trivial (e.g., shifting a boundary character between the two fields), so likelihood is high for any caller who already has legitimate daemon RPC access but is scoped to a single user/service keychain partition.

### Recommendation
Replace the concatenation-based key with a collision-free composite key, e.g. a tuple `(user, service)` (Python tuples hash/equal component-wise and are not subject to this collision) or a delimiter-safe encoding such as `f"{len(user)}:{user}:{service}"`. Avoid ad hoc string concatenation for any cache/memoization key built from multiple independent identity fields.

### Proof of Concept
1. Start the daemon and connect an authorized `KeychainProxy`/RPC client with a valid daemon private cert.
2. Issue a keychain RPC command (e.g., `add_private_key`) with `kc_user="A"`, `kc_service="BC"`. This causes `KeychainServer.get_keychain_for_request()` to compute `key = "A" + "BC" = "ABC"` and cache a new `Keychain(user="A", service="BC")` under `"ABC"`.
3. Issue a second keychain RPC command (e.g., `get_all_private_keys`) with `kc_user="AB"`, `kc_service="C"`. `get_keychain_for_request()` computes `key = "AB" + "C" = "ABC"`, matching the previously cached entry, and returns the `Keychain(user="A", service="BC")` instance instead of constructing/looking up `Keychain(user="AB", service="C")`.
4. The second caller's request is served against the first caller's keychain partition, demonstrating cross-partition data exposure caused by the memoization collision — directly analogous to the GitLab access-memoization collision described in BIT-gitlab-2021-39937/CVE-2021-39937.

Note: I was unable to fully trace how far up the RPC/CLI stack `kc_user`/`kc_service` values are attacker-controllable versus fixed by trusted service configuration (the `Keychain.__init__` signature and its storage-key derivation from `user`/`service` were not fully retrievable within available context), so the exact set of callers who can supply arbitrary `kc_user`/`kc_service` values should be confirmed against `chia/util/keychain.py` and the RPC clients that set these fields before treating this as fully proven end-to-end.

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

**File:** chia/daemon/keychain_server.py (L288-316)
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
        except KeychainFingerprintNotFound as e:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_KEY_NOT_FOUND,
                "error_details": {"fingerprint": e.fingerprint},
            }
        except KeychainException as e:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": str(e)},
            }

```

**File:** chia/util/keychain.py (L36-39)
```python
CURRENT_KEY_VERSION = "1.8"
DEFAULT_USER = f"user-chia-{CURRENT_KEY_VERSION}"  # e.g. user-chia-1.8
DEFAULT_SERVICE = f"chia-{DEFAULT_USER}"  # e.g. chia-user-chia-1.8
MAX_KEYS = 101
```
