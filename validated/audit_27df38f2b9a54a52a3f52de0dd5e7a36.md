## Analog Found: Cross-Fingerprint Private Key Disclosure via Wallet RPC `get_private_key`

### Title
Wallet RPC `get_private_key` endpoint discloses private keys for any fingerprint present in the keychain, not just the currently logged-in wallet identity - ([File: chia/wallet/wallet_rpc_api.py])

### Summary
CVE-2019-13005 describes GitLab's GraphQL service performing insufficient object-level authorization, allowing callers to fetch restricted metadata belonging to users/groups they are not authorized to view. The analogous pattern in this codebase is `WalletRpcApi.get_private_key()`, which resolves and returns full private-key material (mnemonic seed, `sk`, farmer/pool keys) for *any* fingerprint that exists in the shared local keychain, without verifying that the requested fingerprint corresponds to the wallet session that is actually logged in.

### Finding Description
`WalletRpcApi.get_private_key()` takes a caller-supplied `fingerprint` and calls `_get_private_key()`, which scans **all** private keys returned by `self.service.keychain_proxy.get_all_private_keys()` and returns whichever one matches the requested fingerprint: [1](#0-0) 

There is no check anywhere in this path that `request.fingerprint == self.service.logged_in_fingerprint`. The `log_in()` endpoint switches which wallet database/session is *active*, but it does not gate which keys `get_private_key` is allowed to disclose: [2](#0-1) 

Because `Keychain.get_all_private_keys()` returns every private key stored under the keychain's user/service partition (i.e. every fingerprint ever added via `add_key`), a caller who reaches the wallet RPC surface while wallet session A is logged in can still request and receive the full mnemonic/private key of a completely different, currently-inactive fingerprint B that happens to share the same local keychain: [3](#0-2) 

This mirrors the GitLab issue's root cause: the endpoint enforces reachability/session validity but not object-level ownership of the specific credential being requested.

### Impact Explanation
Successful exploitation discloses another identity's full private key (24-word mnemonic equivalent) to a caller who only has legitimate access to a different, currently logged-in wallet fingerprint on the same machine/daemon. This is a confidentiality violation of key material — sufficient to move funds, sign transactions, or derive farmer/pool keys for the disclosed fingerprint — going well beyond simple metadata leakage; it is a "restricted secret disclosed to unauthorized caller" scenario at least as severe as the CVE's restricted-metadata disclosure.

### Likelihood Explanation
Any local caller capable of reaching the standard wallet RPC HTTP endpoint (the same trust boundary assumed for all wallet RPC calls) can trigger this by simply supplying a different fingerprint than the one currently logged in, provided that fingerprint's key was previously added to the same keychain (e.g., multi-user machines, shared daemon setups, or GUI-managed multi-key installations where several fingerprints coexist under one keychain/service namespace). No additional privilege beyond normal wallet-RPC reachability is required.

### Recommendation
Restrict `get_private_key()` (and any similar keychain-wide accessor reachable from the wallet RPC) to only return key material for the fingerprint matching `self.service.logged_in_fingerprint`, or otherwise require explicit re-authentication/authorization when a request targets a fingerprint outside the active session. Consider auditing other keychain-wide read paths (`get_all_private_keys`, daemon `get_key_for_fingerprint`) for the same missing ownership check.

### Proof of Concept
1. Add two keys to the local keychain: fingerprint A and fingerprint B (e.g., via `add_key`).
2. Log in with fingerprint A (`log_in(LogIn(fingerprint=A))`).
3. While session A is active, call `get_private_key(GetPrivateKey(fingerprint=B))` on the same wallet RPC connection.
4. Observe the response contains B's full `sk`, `pk`, `farmer_pk`, `pool_pk`, and mnemonic `seed` — despite B never being the active/logged-in identity for this RPC session. [1](#0-0)

### Citations

**File:** chia/wallet/wallet_rpc_api.py (L644-657)
```python
    async def log_in(self, request: LogIn) -> LogInResponse:
        """
        Logs in the wallet with a specific key.
        """

        if self.service.logged_in_fingerprint == request.fingerprint:
            return LogInResponse(fingerprint=request.fingerprint)

        await self._stop_wallet()
        started = await self.service._start_with_fingerprint(request.fingerprint)
        if started is True:
            return LogInResponse(fingerprint=request.fingerprint)

        raise ValueError(f"fingerprint {request.fingerprint} not found in keychain or keychain is empty")
```

**File:** chia/wallet/wallet_rpc_api.py (L675-700)
```python
    async def _get_private_key(self, fingerprint: int) -> tuple[PrivateKey | None, bytes | None]:
        try:
            all_keys = await self.service.keychain_proxy.get_all_private_keys()
            for sk, seed in all_keys:
                if sk.get_g1().get_fingerprint() == fingerprint:
                    return sk, seed
        except Exception as e:
            log.error(f"Failed to get private key by fingerprint: {e}")
        return None, None

    async def get_private_key(self, request: GetPrivateKey) -> GetPrivateKeyResponse:
        sk, seed = await self._get_private_key(request.fingerprint)
        if sk is not None:
            s = bytes_to_mnemonic(seed) if seed is not None else None
            return GetPrivateKeyResponse(
                private_key=GetPrivateKeyFormat(
                    fingerprint=request.fingerprint,
                    sk=sk,
                    pk=sk.get_g1(),
                    farmer_pk=master_sk_to_farmer_sk(sk).get_g1(),
                    pool_pk=master_sk_to_pool_sk(sk).get_g1(),
                    seed=s,
                )
            )

        raise ValueError(f"Could not get a private key for fingerprint {request.fingerprint}")
```

**File:** chia/util/keychain.py (L454-462)
```python
    def get_all_private_keys(self) -> list[tuple[PrivateKey, bytes]]:
        """
        Returns all private keys which can be retrieved, with the given passphrases.
        A tuple of key, and entropy bytes (i.e. mnemonic) is returned for each key.
        """
        all_keys: list[tuple[PrivateKey, bytes]] = []
        for key_data in self._iterate_through_key_datas(skip_public_only=True):
            all_keys.append((key_data.private_key, key_data.entropy))
        return all_keys
```
