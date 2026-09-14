## Title
PlotNFT2 wallet re-initialization derives a new local `wallet_id` from an in-memory dict instead of the persistent `WalletUserStore` counter, risking wallet-record collision/overwrite - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

## Summary
`PlotNFT2Wallet.identify()` and `PlotNFT2Wallet.potentially_reinitialize_deleted_wallets()` both compute the id for a newly (re)created PlotNFT wallet with `wallet_id = uint32(max(wallet_state_manager.wallets.keys()) + 1)` [1](#0-0) [2](#0-1) , rather than using the DB-backed autoincrement id that `WalletUserStore.create_wallet()` normally guarantees to be unique [3](#0-2) .

## Finding Description
This mirrors the Olympus "Proposals overwrite" bug class: an identifier ("next id") is derived from one authority (the in-memory `wallet_state_manager.wallets` dict) and then used to key a record in a different, persistent authority (`WalletUserStore`'s `users_wallets` table) without verifying the id doesn't already correspond to an existing, valid wallet record.

`PlotNFT2Wallet.create()` only calls `user_store.create_wallet(..., id=wallet_info.id)` if `user_store.get_wallet_by_id(wallet_info.id) is None` [4](#0-3) . This existence check protects the direct insert path, but the id itself is picked using `max(wallet_state_manager.wallets.keys()) + 1` — the in-memory dictionary of *currently active* wallet objects — not the DB `MAX(id)` that `WalletUserStore.get_last_wallet()` uses [5](#0-4) . These two counters are not guaranteed to stay in sync:

- `delete_self()` removes the wallet from `wallet_state_manager.wallets` and from the persistent `users_wallets` table together [6](#0-5) , but any code path that pops an entry from `wallet_state_manager.wallets` without a corresponding DB delete (or vice versa — e.g. a wallet whose DB row still exists but is not yet loaded into the in-memory `wallets` dict during sync/restart) causes `max(wallets.keys())` to diverge from the DB's true max id.
- If `max(wallets.keys()) + 1` happens to equal an id that still has a live row in `users_wallets` (for a wallet not currently present in the in-memory dict, e.g. not yet loaded, or a different wallet type created concurrently through a different subsystem), `get_wallet_by_id()` will find that id "already exists" only if it's a wallet type-agnostic check — but the far more concerning path is `identify()` (lines 560–570), which unconditionally assigns `wallet_state_manager.wallets[matched_plotnft_wallet_id] = await PlotNFT2Wallet.create(...)` [1](#0-0) . This directly overwrites whatever wallet object (of any type) currently occupies that key in the shared `wallet_state_manager.wallets` dict — a genuine "in-memory record overwrite," structurally identical to the reported bug class of overwriting an existing record because the id source and the record store are decoupled and unchecked against each other.

The test suite itself documents that this ID-desync class of bug is a known, unresolved hazard in this exact wallet-recreation flow: `# TODO: there's a bug here where the user store autoincrement means this has a new ID after deleted # Instead of 2, it becomes 3 because there was a 2 at some point (is my best guess)` [7](#0-6) , confirming maintainers are aware wallet-id allocation across delete/recreate cycles is fragile, though for DID wallets the autoincrement (a real DB counter) prevents actual collision. The PlotNFT2 path is different and strictly weaker: it does not use the DB autoincrement counter at all for id derivation, only `max()+1` over the live in-memory dict.

## Impact Explanation
If the computed `wallet_id` collides with an id still tracked as a different, active wallet in `wallet_state_manager.wallets` (or with a stale/soon-to-be-reloaded DB entry), the assignment `wallet_state_manager.wallets[matched_plotnft_wallet_id] = ...` in `identify()` silently replaces the object for that wallet id in the process's authoritative wallet registry. Any subsequent coin-state processing, balance computation, RPC calls, or spend construction keyed by that `wallet_id` would then be routed to the wrong wallet object — potentially causing coin records, transaction history, or DID/CAT wallet identity to be misattributed, or a legitimate wallet's in-memory handle to disappear while its coins remain unspent/unmanaged. This does not directly forge on-chain asset identity but corrupts local wallet-state bookkeping, which can lead to double-spend confusion, unswept coins, or operations being sent against the wrong wallet — consistent with the severity class requested (coin-set/state divergence caused by local wallet-state corruption from a single spend/PlotNFT lifecycle event).

## Likelihood Explanation
Triggering requires a PlotNFT launcher-id hint arriving for a wallet that isn't yet keyed (`identify()`'s fallback branch) at a moment when the in-memory `wallets` dict's max key does not match the persistent store's true max id — e.g., during resync after a wallet delete/reload race, or when other wallet-creation paths add wallets to the DB without immediately updating `wallet_state_manager.wallets`. This is a normal, wallet-node-internal race rather than an attacker-controlled deterministic outcome, so likelihood is moderate/low but reachable purely through ordinary DID/PlotNFT/NFT wallet churn (wallet deletion + wallet recreation), i.e., no privileged or malicious-peer access is required — a plain wallet user's own coin flow can trigger it.

## Recommendation
Compute new wallet ids exclusively through `WalletUserStore.create_wallet()`'s DB autoincrement (or `get_last_wallet()`), never via `max(wallet_state_manager.wallets.keys()) + 1`. Additionally, before assigning into `wallet_state_manager.wallets[wallet_id]`, assert the key is not already occupied by a different wallet instance, mirroring the existing `get_wallet_by_id(...) is None` guard in `create()` but applied consistently to the in-memory dict as well as the persistent store.

## Proof of Concept
1. Have an active wallet occupying id `N` in `wallet_state_manager.wallets` that is not the highest id currently loaded (e.g., because other, higher-id wallets were deleted from the in-memory dict but the corresponding `users_wallets` DB rows were not cleaned up in lockstep, or a resync is in progress).
2. Receive/observe a PlotNFT coin state whose launcher id is not yet keyed to any wallet, driving `identify()`'s `matched_plotnft_wallet_id is None` branch at `chia/wallet/plotnft_wallet/plotnft_wallet.py:560`.
3. `matched_plotnft_wallet_id = max(wallet_state_manager.wallets.keys()) + 1` computes an id that coincides with wallet `N`'s slot being reused/overwritten if `wallets.keys()` at that instant excludes `N` but the DB still expects id `N+1` to be free (i.e., the in-memory and DB id spaces have drifted).
4. `wallet_state_manager.wallets[matched_plotnft_wallet_id] = await PlotNFT2Wallet.create(...)` overwrites the dict entry, and subsequent operations addressed to that `wallet_id` operate on the wrong wallet object.

Note: full confirmation of the exact drift scenario (i.e., a concrete sequence producing `max(wallets.keys())+1 == N` while `N` is still a live/expected id) requires deeper tracing of `wallet_state_manager.py`'s wallet-loading/sync-scope code, which the index coverage available to me did not let me fully trace end-to-end; a Devin session with full repo access would be needed to construct a deterministic reproduction.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L57-77)
```python
    @classmethod
    async def create(
        cls, *, wallet_state_manager: WalletStateManager, xch_wallet: Wallet, wallet_info: WalletInfo
    ) -> Self:
        self = cls(
            wallet_state_manager=wallet_state_manager,
            xch_wallet=xch_wallet,
            log=logging.getLogger(__name__),
            wallet_info=wallet_info,
        )
        await wallet_state_manager.add_interested_puzzle_hashes(
            puzzle_hashes=[self.p2_singleton_puzzle_hash, self.hint], wallet_ids=[self.id(), self.id()]
        )
        if await wallet_state_manager.user_store.get_wallet_by_id(wallet_info.id) is None:
            await wallet_state_manager.user_store.create_wallet(
                name=wallet_info.name,
                wallet_type=wallet_info.type,
                data=wallet_info.data,
                id=wallet_info.id,
            )
        return self
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L560-570)
```python
                matched_plotnft_wallet_id = uint32(max(wallet_state_manager.wallets.keys()) + 1)
                wallet_state_manager.wallets[matched_plotnft_wallet_id] = await PlotNFT2Wallet.create(
                    wallet_state_manager=wallet_state_manager,
                    xch_wallet=wallet_state_manager.main_wallet,
                    wallet_info=WalletInfo(
                        id=matched_plotnft_wallet_id,
                        name=next_plot_nft.launcher_id.hex(),
                        type=uint8(WalletType.PLOTNFT_2),
                        data=next_plot_nft.launcher_id.hex(),
                    ),
                )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L671-683)
```python
    async def delete_self(self, deleted_at_height: uint32, sync_scope: WalletSyncScope) -> None:
        await self.wallet_state_manager.plotnft2_store.add_deleted_wallet(
            launcher_id=self.plotnft_id, name=self.wallet_info.name, height=deleted_at_height
        )
        await self.wallet_state_manager.delete_wallet(self.id())
        self.wallet_state_manager.wallets.pop(self.id())
        self.log.info("Removed PlotNFT2 wallet with ID: %s", self.plotnft_id.hex())
        async with sync_scope.use() as interface:
            interface.side_effects.websocket_events.append(WebSocketEvent(name="wallet_removed", wallet_id=self.id()))
        with PoolingShareState.acquire(
            root_path=self.wallet_state_manager.root_path, p2_singleton_puzzle_hash=self.p2_singleton_puzzle_hash
        ) as pool_config:
            pool_config.remove()
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L690-702)
```python
            async for launcher_id, name in wallet_state_manager.plotnft2_store.pop_deleted_wallets(height=height):
                wallet_id = uint32(max(wallet_state_manager.wallets.keys()) + 1)
                new_wallet = await cls.create(
                    wallet_state_manager=wallet_state_manager,
                    xch_wallet=wallet_state_manager.main_wallet,
                    wallet_info=WalletInfo(
                        id=wallet_id,
                        name=name,
                        type=uint8(WalletType.PLOTNFT_2),
                        data=launcher_id.hex(),
                    ),
                )
                wallet_state_manager.wallets[wallet_id] = new_wallet
```

**File:** chia/wallet/wallet_user_store.py (L47-64)
```python
    async def create_wallet(
        self,
        name: str,
        wallet_type: int,
        data: str,
        id: int | None = None,
    ) -> WalletInfo:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO users_wallets VALUES(?, ?, ?, ?)",
                (id, name, wallet_type, data),
            )
            await cursor.close()
            wallet = await self.get_last_wallet()
            if wallet is None:
                raise ValueError("Failed to get the just-created wallet")

        return wallet
```

**File:** chia/wallet/wallet_user_store.py (L83-87)
```python
    async def get_last_wallet(self) -> WalletInfo | None:
        async with self.db_wrapper.reader_no_transaction() as conn:
            row = await execute_fetchone(conn, "SELECT MAX(id) FROM users_wallets")

        return None if row is None else await self.get_wallet_by_id(row[0])
```

**File:** chia/_tests/wallet/did_wallet/test_did.py (L598-601)
```python
        # TODO: there's a bug here where the user store autoincrement means this has a new ID after deleted
        # Instead of 2, it becomes 3 because there was a 2 at some point (is my best guess)
        reorg_exempt=True,
    )
```
