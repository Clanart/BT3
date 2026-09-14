## Analog Vulnerability Found

### Title
Wallet RPC Treats the Client-Supplied `fingerprint` Parameter as Sufficient Authorization to Access or Activate Any Keychain Identity - ([File: chia/wallet/wallet_rpc_api.py])

### Summary
The Headroom CVE describes a proxy that derives "who is calling" purely from a client-supplied header (`x-headroom-user-id`) with no cryptographic binding to the actual caller, letting one client name another user's identity and read/write that identity's private data. `WalletRpcApi` has the same shape of bug: several endpoints accept a client-supplied `fingerprint` and use it directly to select which locally-stored key's secrets to return or which key becomes the wallet's active identity, without ever checking that the caller is already authenticated as that specific fingerprint.

### Finding Description
`WalletRpcApi._get_private_key` / `get_private_key` iterate **every** private key in the local keychain and return the one matching whatever `fingerprint` the caller supplied in the request body — not the fingerprint of the wallet's currently logged-in identity (`self.service.logged_in_fingerprint`): [1](#0-0) 

There is no check anywhere in this path that `request.fingerprint == self.service.logged_in_fingerprint`. Any caller reaching this RPC route can retrieve the mnemonic/private key material for **any** fingerprint present in the keychain, including keys that were never activated in the current session.

Compounding this, `log_in` lets a caller switch the wallet service's entire active identity to an arbitrary fingerprint on demand: [2](#0-1) 

This is exercised directly by the test suite, which shows a caller changing the "active fingerprint" of a running wallet service simply by naming a different fingerprint in the request — exactly mirroring the CVE's "client names another user's identifier" pattern: [3](#0-2) 

`delete_key` and `check_delete_key` follow the same pattern — they act on whatever fingerprint is supplied, independent of the logged-in identity: [4](#0-3) [5](#0-4) 

The root cause is identical to the CVE's: an identity value that travels with the request (`fingerprint`) is treated as self-authorizing rather than being bound to a previously-established, authenticated session for that specific identity.

### Impact Explanation
Any caller that can reach the wallet RPC surface can:
1. Enumerate all fingerprints via `get_public_keys` (`chia/wallet/wallet_rpc_api.py:662-673`).
2. Exfiltrate the mnemonic/private key for **any** of those fingerprints via `get_private_key`, even ones that were never the actively logged-in identity.
3. Use `log_in` to pivot the wallet service into operating as that other identity and move its funds, or `delete_key` to destroy another identity's key material.

This yields concrete unauthorized coin movement and secret-key disclosure across identity boundaries stored in a single keychain — the same class of impact the CVE flags (CVSS 7.5, VC:H/VI:H).

### Likelihood Explanation
Exploitation requires only RPC reachability plus knowledge of a fingerprint (which is itself obtainable from the unauthenticated-fingerprint `get_public_keys` call). No signature, password, or proof of key ownership is required at any step, so once the RPC endpoint is reachable, the attack path is entirely parameter-driven — mirroring how the CVE's PoC simply supplied a different `x-headroom-user-id`.

### Recommendation
Bind privileged fingerprint-scoped operations (`get_private_key`, `delete_key`, `check_delete_key`) to the wallet service's `logged_in_fingerprint`, or require an explicit re-authentication/ownership proof (e.g., re-derive and check against the currently unlocked key set with per-operation confirmation) before returning secret material or mutating state for a fingerprint that is not the caller's already-authenticated identity. Introduce a single identity-resolution seam (analogous to the CVE fix's `resolve_memory_identity`) that all fingerprint-taking endpoints funnel through, rather than trusting the request body directly.

### Proof of Concept
1. Start `chia_wallet` RPC with two keys already imported into the local keychain (fingerprints `A` and `B`), with `A` currently logged in.
2. Call `get_public_keys` to learn fingerprint `B`.
3. Call `get_private_key` with `fingerprint=B` — the service returns `B`'s mnemonic/private key even though `B` was never the active/logged-in identity, as shown by `_get_private_key` scanning `get_all_private_keys()` for a match with no session check [6](#0-5) .
4. Alternatively call `log_in` with `fingerprint=B` to make the wallet service operate as `B` and drain its coins, as demonstrated by `test_wallet_log_in_changes_active_fingerprint` [7](#0-6) .

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

**File:** chia/wallet/wallet_rpc_api.py (L726-740)
```python
    async def delete_key(self, request: DeleteKey) -> Empty:
        await self._stop_wallet()
        try:
            await self.service.keychain_proxy.delete_key_by_fingerprint(request.fingerprint)
        except Exception as e:
            log.error(f"Failed to delete key by fingerprint: {e}")
            raise e
        path = get_wallet_db_path(
            self.service.root_path,
            self.service.config,
            str(request.fingerprint),
        )
        if path.exists():
            path.unlink()
        return Empty()
```

**File:** chia/wallet/wallet_rpc_api.py (L783-801)
```python
    async def check_delete_key(self, request: CheckDeleteKey) -> CheckDeleteKeyResponse:
        """Check the key use prior to possible deletion
        checks whether key is used for either farm or pool rewards
        checks if any wallets have a non-zero balance
        """
        used_for_farmer: bool = False
        used_for_pool: bool = False
        wallet_balance: bool = False

        sk, _ = await self._get_private_key(request.fingerprint)
        if sk is not None:
            used_for_farmer, used_for_pool = await self._check_key_used_for_rewards(
                self.service.root_path, sk, request.max_ph_to_search
            )

            if self.service.logged_in_fingerprint != request.fingerprint:
                await self._stop_wallet()
                await self.service._start_with_fingerprint(fingerprint=request.fingerprint)

```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2707-2727)
```python
async def test_wallet_log_in_changes_active_fingerprint(
    self_hostname: str,
    one_wallet_and_one_simulator_services: SimulatorsAndWalletsServices,
    layer: InterfaceLayer,
) -> None:
    wallet_rpc_api, _full_node_api, wallet_rpc_port, _ph, bt = await init_wallet_and_node(
        self_hostname, one_wallet_and_one_simulator_services
    )
    primary_fingerprint = (await wallet_rpc_api.get_logged_in_fingerprint(Empty())).fingerprint
    assert primary_fingerprint is not None

    mnemonic = create_mnemonic()
    assert wallet_rpc_api.service.local_keychain is not None
    private_key = wallet_rpc_api.service.local_keychain.add_key(mnemonic_or_pk=mnemonic)
    secondary_fingerprint: int = private_key.get_g1().get_fingerprint()

    await wallet_rpc_api.log_in(LogIn(fingerprint=primary_fingerprint))

    active_fingerprint = (await wallet_rpc_api.get_logged_in_fingerprint(Empty())).fingerprint
    assert active_fingerprint == primary_fingerprint

```
