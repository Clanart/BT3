### Title
Race condition on `PoolWallet.join_pool` / `self_pool` allows concurrent RPC calls to corrupt pool-transition state - ([File: chia/pools/pool_wallet.py])

### Summary
`PoolWallet.join_pool` and `PoolWallet.self_pool` perform a "check-then-set" on shared, unlocked instance attributes (`self.target_state`, `self.next_transaction_fee`, `self.next_tx_config`) before asynchronously building the actual travel-transaction spend bundle. Because the check and the mutation are not protected against concurrent execution of the same RPC endpoint, an attacker with local wallet-RPC access can fire multiple `pw_join_pool` / `pw_self_pool` calls back-to-back and race the state, exactly analogous to the Pontem Pitaka `signMessage` bug where concurrent calls to a stateful signing function overwrote each other's in-flight state.

### Finding Description
`PoolWallet` stores per-wallet pool-transition intent as plain dataclass fields rather than as data scoped to a single call: [1](#0-0) 

`join_pool` reads `self.target_state`, raises if it is already set, and then — only after that check — writes `self.target_state`, `self.next_transaction_fee`, and `self.next_tx_config` before calling `generate_travel_transactions`, which itself does further async work (`get_current_state`, `get_spend_history`, puzzle construction, `generate_signed_transaction`) before consuming those same instance fields: [2](#0-1) 

`self_pool` has the identical pattern: [3](#0-2) 

`generate_travel_transactions` itself is a multi-`await` coroutine that reads `pool_wallet_info.target` (derived from `self.target_state`) and builds the spend using state read at that point: [4](#0-3) 

None of these methods acquire `wallet_state_manager.lock` or any per-`PoolWallet` mutex around the "check target_state is None → set target_state/fee/tx_config → await generate_travel_transactions" sequence. The RPC layer opens a `WalletActionScope` per call (which only isolates the `WalletSideEffects` object, not the `PoolWallet` instance's own attributes), so two overlapping `pw_join_pool`/`pw_self_pool` calls (or a `pw_join_pool` racing a `pw_self_pool`) against the same `wallet_id` can interleave: call A passes the `self.target_state is None` guard, then before it finishes awaiting downstream I/O, call B also passes the guard (since A hasn't necessarily completed the set+await atomically under the event loop) and overwrites `self.next_transaction_fee`/`self.next_tx_config`/`self.target_state`. The travel-transaction spend that A's coroutine eventually builds can then read the fee/tx_config/target_state that call B wrote, producing a transaction for the wrong target pool state, wrong fee, or wrong `TXConfig` (e.g., wrong `excluded_coin_ids`/`reuse_puzhash` semantics) relative to what the user who issued call A actually requested. This is the same class of bug as the reported `signMessage` race: a stateful operation exposed as an idempotent-looking RPC call actually shares mutable state across concurrent invocations with no serialization, letting a second call silently overwrite/redirect the effect of the first.

### Impact Explanation
An unprivileged local RPC caller (anyone with wallet-RPC access, e.g. through a malicious local dApp/CLI script, matching the "local RPC caller" reachable-actor category) can trigger overlapping `pw_join_pool`/`pw_self_pool` calls to make the wallet build and sign a pool-transition transaction using a fee, `TXConfig`, or target pool state that does not correspond to the call that ultimately gets pushed, causing the plotnft/pool wallet to transition to an unintended pool state or apply an unintended fee — a form of reward-redirection / unauthorized state-transition risk for the wallet owner's plotnft.

### Likelihood Explanation
Exploitation only requires two rapid RPC calls to the same wallet endpoint, similar to the trivial `for` loop used in the original Pontem PoC; no cryptographic or network manipulation is needed, only rapid concurrent invocation of `pw_join_pool`/`pw_self_pool` from a local RPC client.

### Recommendation
Serialize `join_pool`/`self_pool`/`generate_travel_transactions` per `PoolWallet` instance (e.g., an `asyncio.Lock` held across the entire check-set-generate sequence, or moving the fee/target_state/tx_config into the `WalletActionScope` side effects instead of `PoolWallet` instance attributes) so concurrent calls cannot interleave the guard check and the mutation of `next_transaction_fee`/`next_tx_config`/`target_state`.

### Proof of Concept
```python
# Pseudocode against the wallet RPC, analogous to the Pontem exploit:
import asyncio

async def race():
    await asyncio.gather(
        rpc_client.pw_join_pool(wallet_id=1, target_puzzlehash=pool_A, pool_url="A", relative_lock_height=10, fee=1),
        rpc_client.pw_join_pool(wallet_id=1, target_puzzlehash=pool_B, pool_url="B", relative_lock_height=10, fee=1000),
    )
    # Both calls pass `self.target_state is None` before either completes its await chain in
    # PoolWallet.join_pool -> generate_travel_transactions, so the final pushed transaction's
    # fee/target/tx_config can come from whichever call wrote self.target_state/self.next_transaction_fee
    # last, not necessarily the call whose spend is actually built and pushed.
```

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

**File:** chia/pools/pool_wallet.py (L630-673)
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

        current_state: PoolWalletInfo = await self.get_current_state()

        total_fee = fee
        if current_state.current == target_state:
            self.target_state = None
            msg = f"Asked to change to current state. Target = {target_state}"
            self.log.info(msg)
            raise ValueError(msg)
        elif current_state.current.state in {SELF_POOLING.value, LEAVING_POOL.value}:
            total_fee = fee
        elif current_state.current.state == FARMING_TO_POOL.value:
            total_fee = uint64(fee * 2)

        if self.target_state is not None:
            raise ValueError(
                f"Cannot change to state {target_state} when already having target state: {self.target_state}"
            )
        PoolWallet._verify_initial_target_state(target_state)
        if current_state.current.state == LEAVING_POOL.value:
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot join a pool until height {last_height + current_state.current.relative_lock_height}"
                )

        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/pools/pool_wallet.py (L675-711)
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

        # Note the implications of getting owner_puzzlehash from our local wallet right now
        # vs. having pre-arranged the target self-pooling address
        owner_puzzlehash = await action_scope.get_puzzle_hash(self.wallet_state_manager)
        owner_pubkey = pool_wallet_info.current.owner_pubkey
        current_state: PoolWalletInfo = await self.get_current_state()
        total_fee = uint64(fee * 2)

        if current_state.current.state == LEAVING_POOL.value:
            total_fee = fee
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot self pool until height {last_height + current_state.current.relative_lock_height}"
                )
        self.target_state = create_pool_state(
            SELF_POOLING, owner_puzzlehash, owner_pubkey, pool_url=None, relative_lock_height=uint32(0)
        )
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```
