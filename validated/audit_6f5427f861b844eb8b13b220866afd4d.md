### Title
Self-pooled rewards left unclaimed before `join_pool` get redirected to the new pool's payout address instead of the user - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet.join_pool()` lets a self-pooling user switch to `FARMING_TO_POOL` without first requiring that unclaimed reward coins sitting on the `p2_singleton_puzzle_hash` be swept to the user's own wallet. Because `claim_pool_rewards()` later pays out absorbed coins to whatever `current_state.current.target_puzzle_hash` is *at claim time* (not at earn time), any self-pooling reward earned before the switch — but claimed after — is paid to the pool's `target_puzzle_hash` rather than the user's own wallet. This is the Chia analog of the Aloe `Factory.claimRewards`/`enrollCourier` bug: value earned under one role/state becomes unclaimable-by-the-rightful-owner once the account transitions to a new role/state, because the code never forces (or even offers) an explicit claim before the transition.

### Finding Description
The `PoolWallet` docstring explicitly documents the hazard: [1](#0-0) 

> "If this wallet is in SELF_POOLING state, the coin ID associated with the current pool wallet contains the rewards gained while self-farming, so care must be taken to disallow joining a new pool while we still have money on the pooling singleton UTXO."

However, `join_pool()` does not implement any such check. It only verifies there is no pending `target_state`, no unconfirmed transaction, and that the target state is valid — it never inspects unspent `p2_singleton_puzzle_hash` reward coins before allowing the transition: [2](#0-1) 

Separately, `claim_pool_rewards()` builds absorb spends using `current_state.current` (the state *at the time of claiming*, not at the time the reward was farmed) and sends the swept total to `current_state.current.target_puzzle_hash`: [3](#0-2) [4](#0-3) 

`target_puzzle_hash` for `SELF_POOLING` is a wallet-owned puzzle hash, but for `FARMING_TO_POOL` it is the pool's own payout puzzle hash (per the pools context doc: "`target_puzzle_hash` means final payout destination... In self-pooling it is a local wallet puzzle hash; in farming-to-pool it is the pool's target puzzle hash."). Because the p2-singleton puzzle hash (where farming rewards land) is derived purely from `launcher_id`/`delayed_puzhash` and does not change across state transitions, self-pooled reward coins earned *before* joining a pool remain absorbable *after* joining — but `claim_pool_rewards()` has no memory of which state was active when each reward was earned, so it pays the entire absorbed total to the pool's current target puzzle hash. A user who forgets (or is not prompted) to claim self-pooled rewards before calling `join_pool` effectively donates that reward amount to the pool once it is later absorbed, exactly analogous to Aloe's courier losing unclaimed lender fees upon `enrollCourier()`.

### Impact Explanation
Any self-pooling farmer with unswept reward coins on their `p2_singleton_puzzle_hash` who then calls `pw_join_pool` (directly reachable via the wallet RPC `pw_join_pool` endpoint — an ordinary, unprivileged wallet action) permanently loses those specific rewards to the pool the next time `pw_absorb_rewards` is invoked (which itself is a normal, expected part of the pooling flow and can be triggered by anyone, including automatically). This is a concrete, unauthorized/undesired redirection of a user's own funds to a third party (the pool operator), consistent with the "reward redirection" impact class.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: any farmer switching pools (a routine, encouraged action — "the ability to change the farm-to target... giving the user the ability to quickly change pools") who has not just claimed their self-pooled block rewards will hit this. There is no client-side or protocol-side guard preventing `join_pool` while unclaimed self-pool rewards exist, despite the code's own comment stating this should be guarded against.

### Recommendation
Before allowing `join_pool()` to proceed (or before building the target-state travel transaction), check for unspent, absorbable coins on `p2_singleton_puzzle_hash` that were farmed while in `SELF_POOLING`/prior state, and either automatically claim them to the user's wallet first, or block the join with an explicit error until the user claims manually — matching the intent already stated in the class docstring. Alternatively, `claim_pool_rewards()` should record/attribute the payout target based on the state active at the time each reward coin was created rather than the state active at claim time.

### Proof of Concept
1. Create a `PoolWallet` in `SELF_POOLING` state and farm several blocks to its `p2_singleton_puzzle_hash` (as in `chia/_tests/pools/test_pool_rpc.py`'s `test_absorb_self*` tests) — the user now has unclaimed self-pooling reward coins.
2. Without calling `pw_absorb_rewards`, call `pw_join_pool` (`PoolWallet.join_pool`) to switch the singleton to `FARMING_TO_POOL` targeting a pool's `target_puzzlehash` — this succeeds because `join_pool()` performs no unclaimed-balance check (`chia/pools/pool_wallet.py:630-673`).
3. After the travel transaction confirms, call `pw_absorb_rewards` (`PoolWallet.claim_pool_rewards`, `chia/pools/pool_wallet.py:713-806`). The absorb spends sweep the coins that were farmed while self-pooling, but the payout destination used is `current_state.current.target_puzzle_hash`, which is now the pool's puzzle hash (since state is `FARMING_TO_POOL`).
4. The user's pre-join self-pooled rewards land at the pool's puzzle hash instead of the user's own wallet, confirming the redirection.

Note: I was not able to execute this scenario in a live simulator within this session; the conclusion is derived from static analysis of `join_pool`, `claim_pool_rewards`, and `target_puzzle_hash` semantics as documented in `.cursor/context/pools.md` and enforced in `pool_wallet.py`. A background Devin session with simulator access would be needed to run the exact PoC and confirm the on-chain payout puzzle hash empirically.

### Citations

**File:** chia/pools/pool_wallet.py (L89-92)
```python
    If this wallet is in SELF_POOLING state, the coin ID associated with the current
    pool wallet contains the rewards gained while self-farming, so care must be taken
    to disallow joining a new pool while we still have money on the pooling singleton UTXO.

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

**File:** chia/pools/pool_wallet.py (L742-743)
```python
        current_state: PoolWalletInfo = await self.get_current_state()
        last_solution: CoinSpend = history[-1][1]
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
