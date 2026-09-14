Based on my investigation, I found a direct analog to this bug class in the plotnft/pool module of chia-blockchain.

### Title
Pool rewards accrued during the `LEAVING_POOL` state are forwarded to the old pool instead of the plot-NFT owner, causing permanent loss of pending funds - (File: `chia/pools/plotnft_drivers.py`, `chia/wallet/plotnft_wallet/plotnft_wallet.py`)

### Summary
The plot-NFT/pool singleton design has an analogous "beneficiary loses pending funds on state transition" flaw to the reported SmartEscrow bug. When a `PlotNFT2Wallet` singleton is in `FARMING_TO_POOL`/`LEAVING_POOL` state, pool block rewards accrue to a p2-singleton address tied to the plot-NFT. `claim_rewards()` (the "self" claim path) explicitly refuses to run while still associated with a pool: `plotnft_wallet.py` raises `"Cannot claim rewards while pooling. If you're a pool, try forward_pool_rewards"`, and this same rejection applies during `LEAVING_POOL` [1](#0-0) . The only defined way to spend such a pending reward while in this state is `forward_pool_reward()`, which sends the reward to the pool's target puzzle hash — not to the plot-NFT owner. The project's own test suite documents this outcome under the comment "LOSE REWARDS (while leaving)", confirming the funds are irreversibly sent away from the owner's wallet balance [2](#0-1) .

### Finding Description
`PoolWallet`/`PlotNFT2Wallet` model three singleton states: `SELF_POOLING`, `FARMING_TO_POOL`, and `LEAVING_POOL` [3](#0-2) . Leaving a pool is a documented two-transaction flow: first transition to `LEAVING_POOL`, then after `relative_lock_height` blocks pass, transition to `SELF_POOLING` or a new pool [4](#0-3) .

During this in-between `LEAVING_POOL` period, any pool reward coin farmed to the p2-singleton puzzle hash cannot be claimed by the owner via the "self-pool" claim path — `claim_rewards()` raises `ValueError("Cannot claim rewards while pooling. If you're a pool, try forward_pool_rewards")` [1](#0-0) . The only available spend path for that reward coin is `forward_pool_reward()`, which is designed for use by the pool operator to sweep rewards to the pool's `target_puzzle_hash`, not the plot-NFT owner's wallet. The test explicitly demonstrates that calling `forward_pool_reward()` on a reward accrued during `LEAVING_POOL` deducts `POOL_REWARD_AMOUNT` from the owner's `plotnft` wallet balance permanently, exactly analogous to the Solidity report where funds vested to the beneficiary go to the wrong party once a state-changing action (`terminate()`) is taken before the pending amount is settled [5](#0-4) .

This mirrors the reported root cause pattern precisely: a state-machine transition (`terminate()` in the Solidity contract, `leave_pool()`/`LEAVING_POOL` here) disables the "correct" claim function for the legitimate beneficiary (`release()` in Solidity, `claim_rewards()` here) for pending/already-accrued value, while a different code path continues to be spendable and directs the value elsewhere.

### Impact Explanation
Anyone using the `pw_self_pool`/`leave_pool` RPC on their own plot-NFT can permanently lose pool rewards that were farmed to their singleton's p2-address between initiating a pool switch (`LEAVING_POOL`) and its finalization. Because `forward_pool_reward` is the only spend path available for these coins while in this transitional state, and it is designed to route funds to the (potentially adversarial or simply "wrong") pool's target puzzle hash, an owner effectively donates real, farmed XCH block rewards to the pool they are leaving. This is direct, quantifiable value loss for a legitimate wallet operation reachable by any local RPC caller managing a plot-NFT — no malicious peer or privileged actor is required.

### Likelihood Explanation
This triggers under normal usage: any plot-NFT owner who farms blocks while leaving a pool (a routine, encouraged pooling operation) will hit this state. The relative-lock-height window during `LEAVING_POOL` is bounded but non-zero (minimum enforced lock heights exist), so any block farmed during that window produces an unclaimable-by-owner reward via the normal claim path. The project's own regression test explicitly encodes and accepts this loss as expected behavior rather than treating it as an error, indicating the condition is not just theoretically reachable but observed and unaddressed.

### Recommendation
Before or during the `LEAVING_POOL`→final state transition, sweep/absorb any pending p2-singleton reward coins to the owner's target puzzle hash (as is already done for `SELF_POOLING`/`FARMING_TO_POOL` via `claim_pool_rewards`/`claim_rewards`), rather than restricting the owner to only `forward_pool_reward`, which pays the pool. Concretely, extend `claim_rewards()`/the absorb-spend construction in `plotnft_drivers.py` to permit self-claiming of rewards accrued while `LEAVING_POOL`, or automatically claim outstanding rewards as part of finishing the leave-pool flow, so that a state transition never forfeits already-farmed value to the departing pool.

### Proof of Concept
1. Create a `PlotNFT2Wallet` and join a pool (`FARMING_TO_POOL`).
2. Call `leave_pool()`, transitioning the singleton to `LEAVING_POOL` [6](#0-5) .
3. While in `LEAVING_POOL`, a block reward is farmed to the plot-NFT's p2-singleton puzzle hash.
4. Attempt `claim_rewards()` — it raises `ValueError("Cannot claim rewards while pooling. If you're a pool, try forward_pool_rewards")` [1](#0-0) .
5. The only available spend, `forward_pool_reward(pool_reward)`, is pushed on-chain; the owner's `plotnft` wallet balance decreases by `POOL_REWARD_AMOUNT`, i.e., the reward is sent to the pool rather than the owner [2](#0-1) .

### Citations

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L406-442)
```python
    # LEAVE POOL
    async with env.wallet_state_manager.new_action_scope(wallet_environments.tx_config, push=True) as action_scope:
        leave_fee = uint64(1_000_000)
        finish_leaving_fee = uint64(1_000_000_000)
        await plotnft_wallet.leave_pool(action_scope=action_scope, fee=leave_fee, finish_leaving_fee=finish_leaving_fee)

    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={
                    "xch": {
                        "unconfirmed_wallet_balance": -leave_fee,
                        "<=#spendable_balance": -leave_fee,
                        "<=#max_send_amount": -leave_fee,
                        ">=#pending_change": 0,
                        ">=#pending_coin_removal_count": 1,
                    },
                    "plotnft": {
                        "pending_coin_removal_count": 1,
                    },
                },
                post_block_balance_updates={
                    "xch": {
                        "confirmed_wallet_balance": -leave_fee,
                        ">=#spendable_balance": 1,
                        ">=#max_send_amount": 1,
                        "<=#pending_change": 0,
                        "<=#pending_coin_removal_count": -1,
                        "<=#unspent_coin_count": 0,
                    },
                    "plotnft": {
                        "pending_coin_removal_count": -1,
                    },
                },
            )
        ]
    )
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L444-449)
```python
    async with env.wallet_state_manager.new_action_scope(wallet_environments.tx_config, push=True) as action_scope:
        with pytest.raises(
            ValueError,
            match=re.escape("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`"),
        ):
            await plotnft_wallet.claim_rewards(action_scope=action_scope)
```

**File:** chia/_tests/wallet/plotnft_wallet/test_plotnft_wallet.py (L451-473)
```python
    # LOSE REWARDS (while leaving)
    plotnft = await plotnft_wallet.get_current_plotnft()
    [pool_reward] = await env.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=plotnft_wallet.plotnft_id)
    coin_spends = plotnft.forward_pool_reward(pool_reward)
    await env.rpc_client.push_tx(PushTX(spend_bundle=WalletSpendBundle(coin_spends, G2Element())))

    await wallet_environments.process_pending_states(
        [
            WalletStateTransition(
                pre_block_balance_updates={},
                post_block_balance_updates={
                    "plotnft": {
                        "confirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "unconfirmed_wallet_balance": -POOL_REWARD_AMOUNT,
                        "max_send_amount": -POOL_REWARD_AMOUNT,
                        "spendable_balance": -POOL_REWARD_AMOUNT,
                        "unspent_coin_count": -1,
                    }
                },
            )
        ],
        bundles_to_repush=[WalletSpendBundle(coin_spends, G2Element())],
    )
```

**File:** chia/pools/pool_wallet_info.py (L16-33)
```python
class PoolSingletonState(IntEnum):
    """
    From the user's point of view, a pool group can be in these states:
    `SELF_POOLING`: The singleton exists on the blockchain, and we are farming
        block rewards to a wallet address controlled by the user

    `LEAVING_POOL`: The singleton exists, and we have entered the "escaping" state, which
        means we are waiting for a number of blocks = `relative_lock_height` to pass, so we can leave.

    `FARMING_TO_POOL`: The singleton exists, and it is assigned to a pool.

    `CLAIMING_SELF_POOLED_REWARDS`: We have submitted a transaction to sweep our
        self-pooled funds.
    """

    SELF_POOLING = 1
    LEAVING_POOL = 2
    FARMING_TO_POOL = 3
```

**File:** .cursor/context/pools.md (L44-46)
```markdown
- Switching from `FARMING_TO_POOL` to another final state is a two-transaction flow: first travel to `LEAVING_POOL`, then after `last_transition_height + relative_lock_height` plus a small reorg buffer, `new_peak()` submits the final travel to `SELF_POOLING` or a new pool.
- Switching from `SELF_POOLING` or mature `LEAVING_POOL` to `FARMING_TO_POOL` is one travel transaction. Switching directly between pools charges for two transitions because it first enters `LEAVING_POOL`.
- Claiming self-pooled rewards scans this pool wallet's unspent reward coins, filters to known farming rewards, builds repeated absorb spends that advance the singleton tip while consuming p2-singleton reward coins, optionally adds a standard-wallet fee spend tied by a coin announcement, and records an outgoing transaction paying the absorbed amount to the current target puzzle hash.
```
