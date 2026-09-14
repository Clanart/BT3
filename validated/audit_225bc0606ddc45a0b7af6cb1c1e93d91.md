### Title
`PoolWallet.target_state` is set before the travel transaction is generated, permanently wedging pool-state transitions when `generate_travel_transactions()` raises - ([File: chia/pools/pool_wallet.py])

### Summary
`join_pool()` and `self_pool()` mutate the in-memory guard field `self.target_state` before calling `generate_travel_transactions()`, which performs coin selection and can raise. If it raises, `self.target_state` is left set even though no transaction was created, permanently blocking further pool transitions for that wallet object.

### Finding Description
In `PoolWallet.join_pool()`: [1](#0-0) 
`self.target_state = target_state` is assigned, followed by `await self.generate_travel_transactions(fee, action_scope)`.

The same pattern exists in `PoolWallet.self_pool()`: [2](#0-1) 

`generate_travel_transactions()` builds the outgoing travel spend and, when `fee > 0`, calls `generate_fee_transaction()` → `standard_wallet.generate_signed_transaction()`, which performs coin selection and can raise `ValueError` (e.g. insufficient spendable balance to cover the requested fee, or other coin-selection failures): [3](#0-2) [4](#0-3) 

Both `join_pool()` and `self_pool()` gate re-entry on `self.target_state is not None`: [5](#0-4) [6](#0-5) 

`self.target_state` is only reset to `None` inside `apply_state_transition()`, when a matching on-chain spend for that exact target state is later observed: [7](#0-6) 

Since the field is set optimistically before the operation that can fail (the same root-cause class described in the report: a state variable is updated even though the subsequent action that depends on it can revert, and the update is never rolled back on failure), a failed `generate_travel_transactions()` call leaves `target_state` "dangling": no transaction was produced, so no on-chain spend will ever match it, so it can never be cleared by `apply_state_transition()`.

### Impact Explanation
Once `target_state` gets stuck in this dangling condition, every subsequent call to `join_pool()` or `self_pool()` on that `PoolWallet` instance raises `ValueError("Cannot join a pool while waiting for target state...")` / `ValueError("Cannot self pool when already having target state...")`. This is a spend-triggered transaction-processing halt for that plot-NFT/pool wallet: the wallet user can no longer switch pools or return to self-pooling through the normal RPC/CLI path until the wallet node process is restarted (the field is in-memory only and not persisted, so a fresh `PoolWallet` object created after restart would reset it, but any long-running node keeps the field wedged). This is reachable by any pool participant simply calling `pw_join_pool`/`pw_self_pool` with a fee argument that momentarily can't be satisfied by coin selection (e.g., insufficient available/unlocked balance, coins locked in other pending transactions, or other coin-selection edge cases raised inside `generate_signed_transaction`).

### Likelihood Explanation
The trigger is a normal wallet action (specifying a fee that coin selection cannot currently satisfy, or hitting any other exception path inside `generate_travel_transactions`/`generate_signed_transaction`), not a privileged or adversarial action. Wallets routinely have coins locked in pending transactions or partially spent, making an accidental fee/coin-selection failure plausible during normal operation, especially when users specify custom fees.

### Recommendation
Only set `self.target_state` (and `self.next_transaction_fee`/`self.next_tx_config`) after `generate_travel_transactions()` completes successfully, or wrap the assignment/call in a try/except that resets `self.target_state = None` on any exception raised from `generate_travel_transactions()`.

### Proof of Concept
1. Create a plot NFT / pool wallet in `SELF_POOLING` state.
2. Ensure the wallet's spendable coin set cannot currently cover a requested fee (e.g., all XCH coins locked by another pending transaction, or select a fee larger than the available balance).
3. Call `pw_join_pool` (or `pw_self_pool`) with that fee. `join_pool()`/`self_pool()` sets `self.target_state` first, then `generate_travel_transactions()` → `generate_fee_transaction()` → `standard_wallet.generate_signed_transaction()` raises `ValueError` due to failed coin selection.
4. Call `pw_join_pool`/`pw_self_pool` again after coins become available/unlocked; the call immediately fails with `ValueError("Cannot join a pool while waiting for target state...")` because `self.target_state` was never cleared, and it will remain stuck until the wallet process restarts.

### Citations

**File:** chia/pools/pool_wallet.py (L291-304)
```python
        # If we have reached the target state, resets it to None. Loops back to get current state
        for _, added_spend in reversed(
            await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id)
        ):
            latest_state: PoolState | None = solution_to_pool_state(added_spend)
            if latest_state is not None:
                if self.target_state == latest_state:
                    self.target_state = None
                    self.next_transaction_fee = uint64(0)
                    self.next_tx_config = DEFAULT_TX_CONFIG
                break

        await self.update_pool_config(action_scope)
        return True
```

**File:** chia/pools/pool_wallet.py (L446-461)
```python
    async def generate_fee_transaction(
        self,
        fee: uint64,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        await self.standard_wallet.generate_signed_transaction(
            [],
            [],
            action_scope,
            fee=fee,
            origin_id=None,
            coins=None,
            extra_conditions=extra_conditions,
        )

```

**File:** chia/pools/pool_wallet.py (L462-497)
```python
    async def generate_travel_transactions(self, fee: uint64, action_scope: WalletActionScope) -> None:
        # target_state is contained within pool_wallet_state
        pool_wallet_info: PoolWalletInfo = await self.get_current_state()

        spend_history = await self.get_spend_history()
        last_coin_spend: CoinSpend = spend_history[-1][1]
        delayed_seconds, delayed_puzhash = get_delayed_puz_info_from_launcher_spend(spend_history[0][1])
        assert pool_wallet_info.target is not None
        next_state = pool_wallet_info.target
        if pool_wallet_info.current.state == FARMING_TO_POOL.value:
            next_state = create_pool_state(
                LEAVING_POOL,
                pool_wallet_info.current.target_puzzle_hash,
                pool_wallet_info.current.owner_pubkey,
                pool_wallet_info.current.pool_url,
                pool_wallet_info.current.relative_lock_height,
            )

        new_inner_puzzle = pool_state_to_inner_puzzle(
            next_state,
            pool_wallet_info.launcher_coin.name(),
            self.wallet_state_manager.constants.GENESIS_CHALLENGE,
            delayed_seconds,
            delayed_puzhash,
        )
        new_full_puzzle = create_full_puzzle(new_inner_puzzle, pool_wallet_info.launcher_coin.name()).to_serialized()

        outgoing_coin_spend, inner_puzzle = create_travel_spend(
            last_coin_spend,
            pool_wallet_info.launcher_coin,
            pool_wallet_info.current,
            next_state,
            self.wallet_state_manager.constants.GENESIS_CHALLENGE,
            delayed_seconds,
            delayed_puzhash,
        )
```

**File:** chia/pools/pool_wallet.py (L630-638)
```python
    async def join_pool(self, target_state: PoolState, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if target_state.state != FARMING_TO_POOL.value:
            raise ValueError(f"join_pool must be called with target_state={FARMING_TO_POOL} (FARMING_TO_POOL)")
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot join pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )
```

**File:** chia/pools/pool_wallet.py (L669-673)
```python
        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/pools/pool_wallet.py (L675-686)
```python
    async def self_pool(self, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot self pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )
        pool_wallet_info: PoolWalletInfo = await self.get_current_state()
        if pool_wallet_info.current.state == SELF_POOLING.value:
            raise ValueError("Attempted to self pool when already self pooling")

        if self.target_state is not None:
            raise ValueError(f"Cannot self pool when already having target state: {self.target_state}")

```

**File:** chia/pools/pool_wallet.py (L705-710)
```python
        self.target_state = create_pool_state(
            SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
        )
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
```
