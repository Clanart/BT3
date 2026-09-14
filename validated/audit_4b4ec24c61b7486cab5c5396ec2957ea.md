Confirmed: `PoolWallet.create_from_db()` at [1](#0-0)  instantiates `PoolWallet` purely from `wallet_info`, using the dataclass default `target_state: PoolState | None = None` [2](#0-1) . `target_state` is never persisted to `WalletPoolStore` or reloaded from the singleton spend history on restart — it only lives in the in-memory `PoolWallet` instance and is set by `join_pool()`/`self_pool()` RPC calls, or explicitly cleared by `delete_unconfirmed_transactions` [3](#0-2) .

### Title
Incomplete state-machine coverage causes `PoolWallet` to strand in `LEAVING_POOL` after wallet restart - (File: chia/pools/pool_wallet.py)

### Summary
This is analogous to the `TunnlTwitterOffers.performUpkeep()` bug: a state-transition driver only acts on items present in an in-memory "to-check" set, but that set silently omits items whose transition should still be pending, so the transition never fires. Here, `PoolWallet.new_peak()` is the function that automatically finishes the second-stage pool travel transaction (`LEAVING_POOL` → `SELF_POOLING`/`FARMING_TO_POOL`), but it only acts when `self.target_state is not None` [4](#0-3) . `target_state` is a purely in-memory field that is never reconstructed from durable on-chain/DB state when the wallet reloads a `PoolWallet` from the database via `create_from_db()`.

### Finding Description
`new_peak()` is invoked on every new peak from `WalletStateManager` and is the sole automatic driver of the second leave-pool travel spend [5](#0-4) :
```
if self.target_state is None:
    return
if self.target_state == pool_wallet_info.current:
    ...
if (
    self.target_state.state in {FARMING_TO_POOL.value, SELF_POOLING.value}
    and pool_wallet_info.current.state == LEAVING_POOL.value
):
    ... generate_travel_transactions(...)
```
`target_state` is set when the user calls `join_pool()`/`self_pool()` (via wallet RPC), and is reset to `None` once the target on-chain state is observed, or explicitly by `delete_unconfirmed_transactions` [3](#0-2) . Crucially, it is not stored in `WalletPoolStore` and not derived from the durable `PoolState` history maintained there — `get_current_state()` only derives `current` from spend history, and passes through whatever `self.target_state` happens to be in memory [6](#0-5) . When the wallet daemon restarts (crash, upgrade, resync) while a pool wallet's on-chain state is `LEAVING_POOL` (i.e., the first travel transaction confirmed but the second has not been submitted/confirmed), `create_from_db()` recreates the `PoolWallet` object with `target_state=None` [1](#0-0) , exactly like the `s_offersToUpkeep` set in the original report failing to include `Pending` offers. `new_peak()` will then immediately `return` on every subsequent peak, and the automatic completion of the leave-pool flow (submitting the second travel spend once `relative_lock_height` has passed) never occurs.

### Impact Explanation
This does not directly cause unauthorized coin movement, but it produces a stuck/incorrect wallet state: a pool singleton confirmed as `LEAVING_POOL` on chain can remain indefinitely un-advanced to `SELF_POOLING`/`FARMING_TO_POOL` after any wallet restart during the lock-height wait window, unless the user manually re-issues a `pw_join_pool`/`pw_self_pool` RPC call to re-populate `target_state`. This mirrors the reported bug's medium-severity classification: it is a functional/state-machine correctness defect (a documented pooling lifecycle transition silently fails to execute) rather than a fund-loss or consensus-divergence bug, since the singleton funds remain safely locked in the waiting-room puzzle and no signature or coin-identity forgery is involved.

### Likelihood Explanation
Any wallet restart, crash, or resync while a plot NFT is in the `LEAVING_POOL` window (which lasts `relative_lock_height` blocks, often a substantial number) will reproduce this: the operator's daemon is fully in control of restart timing, so hitting this window is a normal operational occurrence rather than a rare edge case, making the likelihood at least moderate for any farmer who restarts nodes during pool switches.

### Recommendation
Persist `target_state` (or reconstruct pending-transition intent) from durable pool-store data on `create_from_db()`, or have `new_peak()` independently detect a matured `LEAVING_POOL` state (based on `relative_lock_height` and stored config/target puzzle hash) even when `target_state` is `None` in memory, so the second travel transaction is not silently dropped after a restart.

### Proof of Concept
Not directly exploitable by an external attacker; this is a self-triggered wallet-lifecycle defect reproducible by: (1) create a plot NFT and join a pool, (2) call `pw_self_pool`/`pw_join_pool` so `target_state` is set and the first travel transaction transitions the singleton to `LEAVING_POOL` on chain, (3) restart the wallet process before `relative_lock_height` elapses, (4) farm blocks past `relative_lock_height` — `new_peak()` will keep returning early at line 815 because `create_from_db()` never restored `target_state`, and the singleton stays stuck in `LEAVING_POOL` until the user manually resubmits the join/self-pool request.

### Citations

**File:** chia/pools/pool_wallet.py (L74-81)
```python
    wallet_state_manager: WalletStateManager
    log: logging.Logger
    wallet_info: WalletInfo
    standard_wallet: Wallet
    wallet_id: int
    next_transaction_fee: uint64 = uint64(0)
    next_tx_config: TXConfig = DEFAULT_TX_CONFIG
    target_state: PoolState | None = None
```

**File:** chia/pools/pool_wallet.py (L192-224)
```python
    async def get_current_state(self) -> PoolWalletInfo:
        history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
        all_spends: list[CoinSpend] = [cs for _, cs in history]

        # We must have at least the launcher spend
        assert len(all_spends) >= 1

        launcher_coin: Coin = all_spends[0].coin
        delayed_seconds, delayed_puzhash = get_delayed_puz_info_from_launcher_spend(all_spends[0])
        tip_singleton_coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(all_spends[-1])
        launcher_id: bytes32 = launcher_coin.name()
        p2_singleton_puzzle_hash = launcher_id_to_p2_puzzle_hash(launcher_id, delayed_seconds, delayed_puzhash)
        assert tip_singleton_coin is not None

        curr_spend_i = len(all_spends) - 1
        pool_state: PoolState | None = None
        last_singleton_spend_height = uint32(0)
        while pool_state is None:
            full_spend: CoinSpend = all_spends[curr_spend_i]
            pool_state = solution_to_pool_state(full_spend)
            last_singleton_spend_height = uint32(history[curr_spend_i][0])
            curr_spend_i -= 1

        assert pool_state is not None
        return PoolWalletInfo(
            pool_state,
            self.target_state,
            launcher_coin,
            launcher_id,
            p2_singleton_puzzle_hash,
            tip_singleton_coin.name(),
            last_singleton_spend_height,
        )
```

**File:** chia/pools/pool_wallet.py (L370-389)
```python
    @classmethod
    async def create_from_db(
        cls,
        wallet_state_manager: Any,
        wallet: Wallet,
        wallet_info: WalletInfo,
        name: str | None = None,
    ) -> PoolWallet:
        """
        This creates a PoolWallet from DB. However, all data is already handled by WalletPoolStore, so we don't need
        to do anything here.
        """
        pool_wallet = cls(
            wallet_state_manager=wallet_state_manager,
            log=logging.getLogger(name if name else __name__),
            wallet_info=wallet_info,
            wallet_id=wallet_info.id,
            standard_wallet=wallet,
        )
        return pool_wallet
```

**File:** chia/pools/pool_wallet.py (L808-852)
```python
    async def new_peak(self, peak_height: uint32) -> None:
        # This gets called from the WalletStateManager whenever there is a new peak

        pool_wallet_info: PoolWalletInfo = await self.get_current_state()
        tip_height, tip_spend = await self.get_tip()

        if self.target_state is None:
            return
        if self.target_state == pool_wallet_info.current:
            self.target_state = None
            raise ValueError(f"Internal error. Pool wallet {self.wallet_id} state: {pool_wallet_info.current}")

        if (
            self.target_state.state in {FARMING_TO_POOL.value, SELF_POOLING.value}
            and pool_wallet_info.current.state == LEAVING_POOL.value
        ):
            leave_height = tip_height + pool_wallet_info.current.relative_lock_height

            # Add some buffer (+2) to reduce chances of a reorg
            if peak_height > leave_height + 2:
                unconfirmed: list[
                    TransactionRecord
                ] = await self.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(self.wallet_id)
                next_tip: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
                assert next_tip is not None

                if any([rem.name() == next_tip.name() for tx_rec in unconfirmed for rem in tx_rec.removals]):
                    self.log.info("Already submitted second transaction, will not resubmit.")
                    return

                self.log.info(f"Attempting to leave from\n{pool_wallet_info.current}\nto\n{self.target_state}")
                assert self.target_state.version == POOL_PROTOCOL_VERSION
                assert pool_wallet_info.current.state == LEAVING_POOL.value
                assert self.target_state.target_puzzle_hash is not None

                if self.target_state.state == SELF_POOLING.value:
                    assert self.target_state.relative_lock_height == 0
                    assert self.target_state.pool_url is None
                elif self.target_state.state == FARMING_TO_POOL.value:
                    assert self.target_state.relative_lock_height >= self.MINIMUM_RELATIVE_LOCK_HEIGHT
                    assert self.target_state.pool_url is not None

                async with self.wallet_state_manager.new_action_scope(self.next_tx_config, push=True) as action_scope:
                    await self.generate_travel_transactions(self.next_transaction_fee, action_scope)

```

**File:** chia/wallet/wallet_rpc_api.py (L1533-1536)
```python
            wallet = self.service.wallet_state_manager.wallets[request.wallet_id]
            if wallet.type() == WalletType.POOLING_WALLET.value:
                assert isinstance(wallet, PoolWallet)
                wallet.target_state = None
```
