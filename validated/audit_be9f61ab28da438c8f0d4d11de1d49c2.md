### Title
Unauthorized wallet-identity switch via `check_delete_key` RPC without re-authentication - (File: `chia/wallet/wallet_rpc_api.py`)

### Summary
`WalletRpcApi.check_delete_key` silently stops the currently logged-in wallet and starts a *different* fingerprint's wallet as a side effect of what is documented and named as a read-only "check" operation, and never restores the original identity afterward.

### Finding Description
The Yii advisory’s root cause is that an identity-switch operation (`switchIdentity`) failed to properly re-establish trust/state boundaries (the CSRF token) when moving from one authenticated identity to another, letting stale trust carry across the identity boundary.

The same class of bug — a privileged identity transition happening as an unguarded side effect, leaving stale trust/authorization straddling two different identities — exists in `check_delete_key`: [1](#0-0) 

```python
async def check_delete_key(self, request: CheckDeleteKey) -> CheckDeleteKeyResponse:
    ...
    sk, _ = await self._get_private_key(request.fingerprint)
    if sk is not None:
        used_for_farmer, used_for_pool = await self._check_key_used_for_rewards(...)
        if self.service.logged_in_fingerprint != request.fingerprint:
            await self._stop_wallet()
            await self.service._start_with_fingerprint(fingerprint=request.fingerprint)
        ...
```

Calling this endpoint with a `fingerprint` that differs from `self.service.logged_in_fingerprint` unconditionally tears down the currently-active `WalletStateManager` and starts a new one bound to the requested fingerprint via `WalletNode._start_with_fingerprint()` [2](#0-1) , and `WalletNode.log_in()` then persists this as the new "last used fingerprint" [3](#0-2) . Nothing in `check_delete_key` restores the previously active identity once the check is done, and no explicit user-facing `LogIn` RPC/consent is required — the identity switch is a side effect of a nominally read-only "is this key safe to delete" query.

By contrast, the intended, explicit identity-switch entry point (`log_in`) treats a fingerprint change as an authorized, atomic operation with a clear API contract [4](#0-3) . `check_delete_key` bypasses that contract and mutates the active session's identity as a side effect.

### Impact Explanation
Any subsequent RPC (or GUI/automation) that references `wallet_id`s under the assumption that the previously logged-in fingerprint is still active will now silently operate against a different key's wallet database (different fingerprint, different derived keys, different coin set). This is a session/identity confusion: a local RPC caller that has access to `check_delete_key` (which requires only a fingerprint value, obtainable via `get_public_keys`) can pivot the active wallet identity for the whole `WalletNode` service without going through the standard `log_in` authorization path, and other components (queued transactions, automated resync flows, other RPC clients hitting the same daemon) continue to run believing the old identity is still active. This maps to the "coin identity / wallet state and key derivation" and "daemon/keychain/RPC authorization" reachable categories, and can manifest as transactions or balance queries executing against the wrong key's wallet.

### Likelihood Explanation
This requires access to the wallet RPC/daemon (a "local RPC caller"), which is the exact threat category called out for this class of finding (semi-trusted local/admin surface). No special privileges beyond calling an existing "check" RPC are needed to trigger the identity switch, and the fingerprint value is trivially obtainable from `get_public_keys`.

### Recommendation
`check_delete_key` should not mutate global wallet-node login state. Perform the balance/reward-usage check against the target fingerprint's key material without calling `_stop_wallet()`/`_start_with_fingerprint()`, or, if a temporary switch is unavoidable, always restore the previously logged-in fingerprint (via `_stop_wallet()` + `_start_with_fingerprint(original_fingerprint)`) before returning, inside a `try/finally`. Any code path that changes `self.service.logged_in_fingerprint` should be treated the same way `log_in()` is: an explicit, restorable, single-purpose operation.

### Proof of Concept
1. Import two keys into the same local keychain (e.g. via `add_key`), giving fingerprints `A` (currently logged in) and `B`.
2. As a local RPC client, call `check_delete_key` with `fingerprint=B`.
3. Observe (as in the existing test flow) that `self.service.logged_in_fingerprint` — and the entire `WalletStateManager` — is now bound to `B` [5](#0-4) , with no call to `log_in` having occurred and no restoration of `A`.
4. Any subsequent `send_transaction`/`get_wallets` call issued by another client or automation still assuming identity `A` is active now operates on wallet key `B`'s data instead.

Note: I could not fully trace every downstream consumer that assumes fingerprint continuity across RPC calls (e.g. GUI session state, `cmds_util.get_wallet` prompt flow) within the available index; a full audit of concurrent RPC-client assumptions would benefit from broader access to the GUI/CLI call sites.

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

**File:** chia/wallet/wallet_node.py (L429-499)
```python
    async def _start_with_fingerprint(
        self,
        fingerprint: int | None = None,
    ) -> bool:
        # Makes sure the coin_state_updates get higher priority than new_peak messages.
        # Delayed instantiation until here to avoid errors.
        #   got Future <Future pending> attached to a different loop
        self._new_peak_queue = NewPeakQueue(inner_queue=asyncio.PriorityQueue())
        if not fingerprint:
            fingerprint = self.get_last_used_fingerprint()
        multiprocessing_start_method = process_config_start_method(config=self.config, log=self.log)
        multiprocessing_context = multiprocessing.get_context(method=multiprocessing_start_method)
        self._weight_proof_handler = WalletWeightProofHandler(self.constants, multiprocessing_context)
        self.synced_peers = set()
        public_key = None
        private_key = await self.get_key(fingerprint, private=True, find_a_default=False)
        if private_key is None:
            public_key = await self.get_key(fingerprint, private=False, find_a_default=False)
        else:
            assert isinstance(private_key, PrivateKey)
            public_key = private_key.get_g1()

        if public_key is None:
            private_key = await self.get_key(None, private=True, find_a_default=True)
            if private_key is not None:
                assert isinstance(private_key, PrivateKey)
                public_key = private_key.get_g1()
            else:
                self.log_out()
                return False
        assert isinstance(public_key, G1Element)
        # override with private key fetched in case it's different from what was passed
        if fingerprint is None:
            fingerprint = public_key.get_fingerprint()
        if self.config.get("enable_profiler", False):
            if sys.getprofile() is not None:
                self.log.warning("not enabling profiler, getprofile() is already set")
            else:
                create_referenced_task(profile_task(self.root_path, "wallet", self.log), known_unreferenced=True)

        if self.config.get("enable_memory_profiler", False):
            create_referenced_task(mem_profile_task(self.root_path, "wallet", self.log), known_unreferenced=True)

        path: Path = get_wallet_db_path(self.root_path, self.config, str(fingerprint))
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.config.get("reset_sync_for_fingerprint") == fingerprint:
            await self.reset_sync_db(path, fingerprint)

        assert private_key is None or isinstance(private_key, PrivateKey)
        self._wallet_state_manager = await WalletStateManager.create(
            private_key,
            self.config,
            path,
            self.constants,
            self.root_path,
            self,
            public_key,
        )

        if self.state_changed_callback is not None:
            self.wallet_state_manager.set_callback(self.state_changed_callback)

        self.last_wallet_tx_resend_time = int(time.time())
        self.wallet_tx_resend_timeout_secs = self.config.get("tx_resend_timeout_secs", 60 * 60)
        self.wallet_state_manager.set_pending_callback(self._pending_tx_handler)
        self._shut_down = False
        self._process_new_subscriptions_task = create_referenced_task(self._process_new_subscriptions())
        self._retry_failed_states_task = create_referenced_task(self._retry_failed_states())

        self.sync_event = asyncio.Event()
        self.log_in(fingerprint)
```

**File:** chia/wallet/wallet_node.py (L774-793)
```python
    def log_in(self, fingerprint: int) -> None:
        self.logged_in_fingerprint = fingerprint
        self.logged_in = True
        self.log.info(f"Wallet is logged in using key with fingerprint: {self.logged_in_fingerprint}")
        try:
            self.update_last_used_fingerprint()
        except Exception:
            self.log.exception("Non-fatal: Unable to update last used fingerprint.")

    def log_out(self) -> None:
        self.logged_in_fingerprint = None
        self.logged_in = False

    def update_last_used_fingerprint(self) -> None:
        fingerprint = self.logged_in_fingerprint
        assert fingerprint is not None
        path = self.get_last_used_fingerprint_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(fingerprint))
        self.log.info(f"Updated last used fingerprint: {fingerprint}")
```

**File:** chia/_tests/wallet/rpc/test_wallet_rpc.py (L2906-2915)
```python
    await client.delete_key(DeleteKey(fingerprint=pks[0]))
    assert not get_wallet_db_path(wallet_node.root_path, wallet_node.config, str(pks[0])).exists()
    await client.log_in(LogIn(fingerprint=uint32(pks[1])))
    assert len((await client.get_public_keys()).pk_fingerprints) == 1

    assert not (await client.get_sync_status()).synced

    wallets = (await client.get_wallets(GetWallets())).wallets
    assert len(wallets) == 1
    assert await get_unconfirmed_balance(client, int(wallets[0].id)) == 0
```
