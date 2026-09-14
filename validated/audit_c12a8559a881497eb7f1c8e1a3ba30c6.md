This confirms `p2_singleton_puzzle_hash` is derived purely from `launcher_id, delay_time, delay_ph` (via `launcher_id_to_p2_puzzle_hash()`), independent of the current `target_puzzle_hash`/pool state [1](#0-0) . This means reward coins accrue to the same address regardless of which pool/self-pooling state was active when they were farmed, but are paid out based on whatever state is *current* at claim time.

### Title
Pool reward payout uses current target state instead of the state active when the reward accrued, enabling reward redirection - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet.claim_pool_rewards()` pays out **all** unclaimed p2-singleton reward coins to `current_state.current.target_puzzle_hash`, i.e., the pool-wallet's target address **at the moment the claim is submitted**, rather than the target address that was in effect when each individual reward coin was actually farmed [2](#0-1) [3](#0-2) . Because `p2_singleton_puzzle_hash` (where farmed rewards actually land) is a static function of `launcher_id`/delay parameters and does not depend on pool state [4](#0-3) , rewards farmed under one state (e.g., self-pooling, `target_puzzle_hash` = owner's wallet) can be swept to a different state's target (e.g., a pool's `target_puzzle_hash`) if the owner transitions state (`join_pool()`/`self_pool()`) before calling `claim_pool_rewards()`.

### Finding Description
This mirrors the root cause of the reported Sherlock issue: the protocol distributes accrued value based on the state that is current at the moment the distribution is triggered, not based on which state was actually active during the period the value accrued. In the Super DCA case, `beforeAddLiquidity`/`beforeRemoveLiquidity` donate accumulated rewards to whichever position is in-range at the current tick, ignoring which positions provided liquidity during the accrual window. Here, `PoolWallet.join_pool()` and `PoolWallet.self_pool()` change `target_state`/`current_state.current.target_puzzle_hash` with no check for outstanding unclaimed reward coins at the (state-independent) `p2_singleton_puzzle_hash` [5](#0-4) [6](#0-5) . The only guard present is `have_unconfirmed_transaction()`, which prevents overlapping *singleton-tip spends*, not stale unclaimed reward coins. When `claim_pool_rewards()` is later called, it always uses the then-current `target_puzzle_hash` to build the outgoing transaction for the full claimed amount [7](#0-6) [3](#0-2) , regardless of which state generated each reward coin (`coin_to_height_farmed` only tracks height, not the state/target active at that height) [8](#0-7) .

### Impact Explanation
A user (or a pool operator instructing/tricking a user) who self-farms rewards and then joins a pool before absorbing those rewards will have the self-farmed rewards redirected to the pool's `target_puzzle_hash` instead of their own wallet — a concrete reward-redirection/loss-of-funds outcome for the coin owner, matching the "reward redirection" impact class called out as acceptable. This is a design gap analogous to the audited bug: value generated under one participation state is settled against a different, currently-active state.

### Likelihood Explanation
Requires the wallet owner (or an operator with delegated control) to sequence a `join_pool`/`self_pool` state change ahead of an outstanding `claim_pool_rewards()` call while unclaimed p2-singleton reward coins exist — a realistic sequence for any farmer who switches pools without first sweeping accumulated self-farmed rewards, since nothing in `join_pool()`/`self_pool()` blocks the transition when unclaimed rewards are pending [5](#0-4) .

### Recommendation
Track, per reward coin, the `target_puzzle_hash` (or pool state) active at the height it was farmed, and settle each reward coin to that historical target rather than the current one; alternatively, block `join_pool()`/`self_pool()` transitions while unclaimed reward coins remain at `p2_singleton_puzzle_hash`, forcing a claim first.

### Proof of Concept
1. Owner self-pools (`target_puzzle_hash` = wallet address A); farms N blocks; reward coins land at `p2_singleton_puzzle_hash` (fixed by launcher id).
2. Before calling `claim_pool_rewards()`, owner calls `join_pool()` targeting pool address B; the travel transaction confirms, updating `current_state.current.target_puzzle_hash` to B [9](#0-8) .
3. Owner calls `claim_pool_rewards()`. The function reads `current_state.current.target_puzzle_hash` = B and sends the full previously self-farmed amount there instead of to A [7](#0-6) [3](#0-2) .

### Citations

**File:** chia/pools/pool_puzzles.py (L51-64)
```python
def create_waiting_room_inner_puzzle(
    target_puzzle_hash: bytes32,
    relative_lock_height: uint32,
    owner_pubkey: G1Element,
    launcher_id: bytes32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> Program:
    pool_reward_prefix = bytes32(genesis_challenge[:16] + b"\x00" * 16)
    p2_singleton_puzzle_hash: bytes32 = launcher_id_to_p2_puzzle_hash(launcher_id, delay_time, delay_ph)
    return POOL_WAITING_ROOM_MOD.curry(
        target_puzzle_hash, p2_singleton_puzzle_hash, bytes(owner_pubkey), pool_reward_prefix, relative_lock_height
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

**File:** chia/pools/pool_wallet.py (L669-672)
```python
        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
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

**File:** chia/pools/pool_wallet.py (L731-738)
```python
        farming_rewards: list[TransactionRecord] = await self.wallet_state_manager.tx_store.get_farming_rewards()
        coin_to_height_farmed: dict[Coin, uint32] = {}
        for tx_record in farming_rewards:
            height_farmed: uint32 | None = tx_record.height_farmed(
                self.wallet_state_manager.constants.GENESIS_CHALLENGE
            )
            assert height_farmed is not None
            coin_to_height_farmed[tx_record.additions[0]] = height_farmed
```

**File:** chia/pools/pool_wallet.py (L742-746)
```python
        current_state: PoolWalletInfo = await self.get_current_state()
        last_solution: CoinSpend = history[-1][1]

        all_spends: list[CoinSpend] = []
        total_amount = 0
```

**File:** chia/pools/pool_wallet.py (L793-806)
```python
        # The claim spend, minus the fee amount from the main wallet
        async with action_scope.use() as interface:
            interface.side_effects.transactions.append(
                self.wallet_state_manager.new_outgoing_transaction(
                    wallet_id=uint32(self.wallet_id),
                    puzzle_hash=current_state.current.target_puzzle_hash,
                    amount=uint64(total_amount),
                    fee=fee,
                    spend_bundle=claim_spend,
                    additions=[add for add in claim_spend.additions() if add.amount == last_solution.coin.amount],
                    removals=claim_spend.removals(),
                    name=claim_spend.name(),
                )
            )
```
