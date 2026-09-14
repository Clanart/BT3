Confirmed: `insert()` and `delete_key()` in `chia/data_layer/data_layer_rpc_api.py` call `self.service.batch_update(...)` (which internally calls `batch_insert()` and enforces the "owned by DL Wallet" check via `get_owned_stores()`). However, `submit_all_pending_roots()` in `chia/data_layer/data_layer.py` publishes every locally pending root by calling `dl_update_multiple()` without re-checking `get_owned_stores()` for each `store_id` at publish time.

### Title
Missing ownership re-check in `submit_all_pending_roots` allows publishing stale/non-owned pending roots - (File: chia/data_layer/data_layer.py)

### Summary
Single-record DataLayer mutation paths (`insert`, `delete_key`, `batch_update`) enforce a "singleton owned by this wallet" check inside `batch_insert()` before mutating and staging a root for publication: [1](#0-0) 

But the bulk/"import-all" analogue, `submit_all_pending_roots()`, iterates over *every* locally pending batch root and publishes it via wallet RPC `dl_update_multiple()` with **no** ownership re-check at publish time: [2](#0-1) 

### Finding Description
The per-operation guard against publishing changes for stores not owned by the local DL wallet lives only in `batch_insert()`, gated on a point-in-time call to `get_owned_stores()`: [3](#0-2) 

`submit_all_pending_roots()` is a distinct RPC-exposed bulk endpoint (`/submit_all_pending_roots`) that operates on all pending roots currently in local storage, entirely bypassing that ownership gate: [4](#0-3) [2](#0-1) 

This mirrors the GHSA-j3m6-gvm8-mhvw bug class exactly: a "bulk" operation (there, CSV import; here, "submit all pending roots") omits a permission/ownership check that the equivalent single-record operation performs. If wallet-side ownership of a launcher/singleton changes between the time a batch was staged as `PENDING_BATCH` (via `batch_insert`, which checked ownership) and the time `submit_all_pending_roots` is invoked — e.g., after a wallet rollback/re-sync event such as the one exercised in `test_datalayer_ownership_survives_rollback` — the bulk submit path will still attempt to publish a root update for that store id, because it never re-validates `get_owned_stores()` before calling `dl_update_multiple()`. [5](#0-4) 

### Impact Explanation
If ownership is lost/regained asynchronously (rollback, resync, or race between store creation and submission), `submit_all_pending_roots` can attempt to publish a `LauncherRootPair` update for a store id the local wallet no longer verifiably owns at call time, whereas the individual `insert`/`delete_key`/`batch_update` path would have rejected the same store id with `ValueError(f"Singleton with launcher ID {store_id} is not owned by DL Wallet")`. This is an authorization inconsistency between two RPC paths that should enforce the same invariant, and could let a locally-issued bulk operation submit an update transaction for a singleton whose local ownership state is stale/incorrect, potentially causing an unauthorized on-chain root update attempt for a store the caller should not be permitted to modify via this path.

### Likelihood Explanation
This RPC surface is local/admin (`chia/rpc/rpc_server.py` semi-trusted local API per `.cursor/context/rpc.md`), so it requires local RPC access already; but the underlying bug is a genuine logic gap: ownership is validated once (at `batch_insert` time) and never re-validated at publish time in the bulk path, unlike the single-item path's design intent ("This ownership check is the main guard preventing arbitrary local roots from being published for non-owned singletons" per project docs). The trigger condition (ownership state changing between staging and bulk submission, e.g. via rollback/resync) is a documented, tested scenario in this codebase, making the divergence realistic rather than purely theoretical.

### Recommendation
Add the same ownership check (`get_owned_stores()` / verifying `store_id` against owned launcher IDs) inside `submit_all_pending_roots()` (and `multistore_batch_update`'s publish branch) immediately before constructing `LauncherRootPair` entries and calling `dl_update_multiple()`, mirroring the guard already present in `batch_insert()`. Reject or skip pending roots for store ids that are no longer owned by the DL wallet at submission time.

### Proof of Concept
1. Create and own a DataLayer store; stage a batch update with `submit_on_chain=False` via `/batch_update`, which passes the ownership check inside `batch_insert()` and leaves a `PENDING_BATCH` root in `DataStore`.
2. Cause the wallet to lose local ownership tracking for that launcher id (e.g., via the rollback scenario shown in `test_datalayer_ownership_survives_rollback`, where `get_owned_singletons()` reports the store as not owned after `perform_atomic_rollback`).
3. Call `/submit_all_pending_roots` before ownership is restored. Trace through `DataLayer.submit_all_pending_roots()` — it fetches all `PENDING_BATCH`/pending roots from local storage and publishes them via `dl_update_multiple()` with no call to `get_owned_stores()`/ownership verification, unlike `batch_insert()`, demonstrating the missing-permission-check-on-bulk-path pattern.

### Citations

**File:** chia/data_layer/data_layer.py (L374-387)
```python
    async def submit_all_pending_roots(self, fee: uint64) -> list[TransactionRecord]:
        pending_roots = await self.data_store.get_all_pending_batches_roots()
        updates: list[LauncherRootPair] = []
        if len(pending_roots) == 0:
            raise Exception("No pending roots found to submit")
        for pending_root in pending_roots:
            root_hash = pending_root.node_hash if pending_root.node_hash is not None else self.none_bytes
            updates.append(LauncherRootPair(launcher_id=pending_root.store_id, new_root=root_hash))
            await self.data_store.change_root_status(pending_root, Status.PENDING)
        response = await self.wallet_rpc.dl_update_multiple(
            DLUpdateMultiple(updates=DLUpdateMultipleUpdates(launcher_root_pairs=updates), fee=fee),
            DEFAULT_TX_CONFIG,
        )
        return response.transactions
```

**File:** chia/data_layer/data_layer.py (L389-406)
```python
    async def batch_insert(
        self,
        store_id: bytes32,
        changelist: list[dict[str, Any]],
        status: Status = Status.PENDING,
        enable_batch_autoinsert: bool | None = None,
    ) -> bytes32:
        await self._update_confirmation_status(store_id=store_id)

        async with self.data_store.transaction():
            pending_root: Root | None = await self.data_store.get_pending_root(store_id=store_id)
            if pending_root is not None and pending_root.status == Status.PENDING:
                raise Exception("Already have a pending root waiting for confirmation.")

            # check before any DL changes that this singleton is currently owned by this wallet
            singleton_records: list[SingletonRecord] = await self.get_owned_stores()
            if not any(store_id == singleton.launcher_id for singleton in singleton_records):
                raise ValueError(f"Singleton with launcher ID {store_id} is not owned by DL Wallet")
```

**File:** chia/data_layer/data_layer_rpc_api.py (L296-299)
```python
    async def submit_all_pending_roots(self, request: dict[str, Any]) -> EndpointResult:
        fee = get_fee(self.service.config, request)
        transaction_records = await self.service.submit_all_pending_roots(uint64(fee))
        return {"tx_id": [transaction_record.name for transaction_record in transaction_records]}
```

**File:** chia/_tests/wallet/db_wallet/test_dl_wallet.py (L966-992)
```python
    # --- Simulate a wallet rollback to block 0 (e.g. peer trust flip) ---
    await wallet_node.perform_atomic_rollback(0)

    # The launcher entry should be gone after rollback
    assert await env.wallet_state_manager.dl_store.get_launcher(launcher_id) is None

    # The singleton record should still exist (unconfirmed, generation > 0)
    rec = await env.wallet_state_manager.dl_store.get_latest_singleton(launcher_id)
    assert rec is not None
    assert not rec.confirmed
    assert rec.generation > 0

    # Ownership is lost because the launchers row was deleted
    owned = await dl_wallet.get_owned_singletons()
    assert not any(s.launcher_id == launcher_id for s in owned)

    # --- Re-track the launcher (simulates what the wallet sync does on resync) ---
    peer = wallet_node.get_full_node_peer()
    assert peer is not None
    await dl_wallet.track_new_launcher_id(launcher_id, peer)

    # --- Ownership must be restored ---
    launcher = await env.wallet_state_manager.dl_store.get_launcher(launcher_id)
    assert launcher is not None, "Launcher entry not restored after re-track"

    owned = await dl_wallet.get_owned_singletons()
    assert any(s.launcher_id == launcher_id for s in owned), "Store ownership lost after rollback + re-track"
```
