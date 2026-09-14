### Title
Plot-NFT owner can front-run pool reward absorption to redirect a pool's earned reward to themselves - ([File: chia/pools/pool_wallet.py])

### Summary
The Chia pooling singleton pays farmed block rewards to whatever `target_puzzle_hash` is *currently* active on the plot-NFT singleton at the moment the reward coin is absorbed, rather than the `target_puzzle_hash` that was active when the reward was actually farmed. This mirrors the OngoingBounty pattern where an "owner" (here, the plot-NFT/singleton owner) can change the payout destination (`setPayout`-equivalent: a pool-switch/leave-pool transition) and front-run a pending, already-earned payout, redirecting funds away from the party that was supposed to receive them (the pool, which stands in for the "contributor" whose work produced the reward).

### Finding Description
`PoolWallet.claim_pool_rewards()` fetches the pool singleton's *current* state right before building the absorb spends, and pays the absorbed total to whatever `target_puzzle_hash` is in that current state: [1](#0-0) [2](#0-1) 

The actual on-chain enforcement of the payout destination happens in `create_absorb_spend()`, which curries the *current* `PoolState.target_puzzle_hash` into the singleton's inner puzzle used to authorize spending the p2-singleton reward coin — it does not use, or verify against, the pool state that was active at the block height when the reward coin was farmed: [3](#0-2) [4](#0-3) 

Because `target_puzzle_hash` is only "final payout destination" local/mutable intent that can be updated by a travel spend (join/leave pool), and because reward coins sit unspent at the p2-singleton address until absorbed, the plot-NFT owner can:
1. Farm a block while `FARMING_TO_POOL` (target = pool's address).
2. Before absorbing that reward, submit a travel spend changing the singleton's target to their own wallet (self-pooling / different pool), analogous to the bounty owner calling `setPayout`.
3. Absorb the p2-singleton reward coin using the now-current (self) target, since `create_absorb_spend` bakes in whatever `target_puzzle_hash` is active at absorb time.

This is functionally identical to the OngoingBounty bug class: the entity that controls the "payout configuration" (target_puzzle_hash / PoolState) can front-run the finalization of an already-earned reward and redirect it before the contributor-facing entity (the pool, and by extension farmers credited by that pool) can claim/expect it.

### Impact Explanation
If reachable, this allows a plot-NFT owner to redirect on-chain block rewards that were earned while pointed at a pool to their own address instead, effectively stealing rewards that the pooling protocol design intends to go to the pool (and, indirectly, to farmers who are compensated by the pool based on partial credit for that reward). This is a reward-redirection / payout-theft class issue matching the validated impact categories (reward redirection, unauthorized coin movement).

### Likelihood Explanation
Exploiting this requires the plot-NFT owner to control both actions (submit a leave/self-pool travel spend and the absorb spend) and to win the race against the pool's or their own automatic absorb flow — which is entirely within the owner's control since only the owner holds the singleton's private key. Unconfirmed-transaction gates in `PoolWallet` reduce accidental races, but a deliberately malicious owner is not prevented from sequencing travel-then-absorb transactions to redirect a specific reward. I was not able to fully verify, within the available context, whether the `relative_lock_height`/waiting-room escape mechanics impose an additional timelock specifically on the absorb path when leaving a pool (only on the "escape" state transition to a new final pool state), so the exact minimal number of transactions/blocks required to exploit this is uncertain and would need confirmation directly against the `POOL_MEMBER_INNERPUZ`/`POOL_WAITINGROOM_INNERPUZ` CLVM puzzles.

### Recommendation
- Bind the reward-payout `target_puzzle_hash` used in absorb spends to the `PoolState` that was active at the historical block height the reward coin was farmed, not the state active at absorb time, or
- Prevent absorbing p2-singleton reward coins whose height precedes the latest travel/leave-pool spend, forcing owners to absorb previously earned rewards under the old pool state before a pool switch takes effect.

### Proof of Concept
Not independently reproduced against a running simulator in this session; the conclusion is derived from static analysis of `PoolWallet.claim_pool_rewards()` (uses `current_state.current.target_puzzle_hash` fetched fresh via `get_current_state()`) and `create_absorb_spend()`/`pool_state_to_inner_puzzle()` (which curries whichever `target_puzzle_hash` belongs to the `current_state` argument, with no check tying it to the reward's farmed height). A concrete PoC would need to: (1) create a plot-NFT and join a pool, (2) farm a pool reward block, (3) submit a travel spend switching to self-pooling/another pool before absorbing, and (4) call `claim_pool_rewards()` to observe the reward being paid to the new target instead of the original pool's target — this exact sequencing/timelock interaction was not fully confirmed within available tool access and should be validated with a Devin session running `chia/_tests/pools/` simulator tests.

### Citations

**File:** chia/pools/pool_wallet.py (L739-746)
```python
        history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
        assert len(history) > 0
        delayed_seconds, delayed_puzhash = get_delayed_puz_info_from_launcher_spend(history[0][1])
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

**File:** chia/pools/pool_puzzles.py (L252-272)
```python
def create_absorb_spend(
    last_coin_spend: CoinSpend,
    current_state: PoolState,
    launcher_coin: Coin,
    height: uint32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> list[CoinSpend]:
    inner_puzzle: Program = pool_state_to_inner_puzzle(
        current_state, launcher_coin.name(), genesis_challenge, delay_time, delay_ph
    )
    reward_amount: uint64 = calculate_pool_reward(height)
    if is_pool_member_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol: Program = Program.to([reward_amount, height])
    elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol = Program.to([0, reward_amount, height])
    else:
        raise ValueError
```

**File:** chia/pools/pool_puzzles.py (L436-459)
```python
def pool_state_to_inner_puzzle(
    pool_state: PoolState, launcher_id: bytes32, genesis_challenge: bytes32, delay_time: uint64, delay_ph: bytes32
) -> Program:
    escaping_inner_puzzle: Program = create_waiting_room_inner_puzzle(
        pool_state.target_puzzle_hash,
        pool_state.relative_lock_height,
        pool_state.owner_pubkey,
        launcher_id,
        genesis_challenge,
        delay_time,
        delay_ph,
    )
    if pool_state.state in {LEAVING_POOL.value, SELF_POOLING.value}:
        return escaping_inner_puzzle
    else:
        return create_pooling_inner_puzzle(
            pool_state.target_puzzle_hash,
            escaping_inner_puzzle.get_tree_hash(),
            pool_state.owner_pubkey,
            launcher_id,
            genesis_challenge,
            delay_time,
            delay_ph,
        )
```
