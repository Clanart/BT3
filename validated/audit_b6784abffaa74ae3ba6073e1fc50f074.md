Confirmed: `PlotNFT2Wallet.claim_rewards` in `chia/wallet/plotnft_wallet/plotnft_wallet.py` builds a single unbounded `coin_spends` list from **all** `rewards_to_claim` returned by `plotnft2_store.get_pool_rewards()` with no batching cap, unlike the legacy `PoolWallet.claim_pool_rewards` (`chia/pools/pool_wallet.py`) which explicitly caps `max_spends_in_tx` (`DEFAULT_MAX_CLAIM_SPENDS = 100`) specifically "to fit into block." This is the closest analog in this codebase to the report's "Gas Limit Issues" bug class.

### Title
Unbounded pool-reward batching in `PlotNFT2Wallet.claim_rewards` can produce an oversized spend bundle that never fits in a block, permanently stranding self-pooled rewards - (File: chia/wallet/plotnft_wallet/plotnft_wallet.py)

### Summary
`PlotNFT2Wallet.claim_rewards` gathers every unclaimed pool-reward coin for a plot NFT and packs all of them, plus one `SendMessage`/reward-claim CoinSpend per reward, into a single `WalletSpendBundle`, with no limit on the number of rewards processed per call.

### Finding Description
`claim_rewards` fetches `rewards_to_claim = await self.wallet_state_manager.plotnft2_store.get_pool_rewards(...)` with no cap, then calls `plotnft.claim_pool_rewards(...)` which builds one `CoinSpend` for the singleton plus one additional `CoinSpend` per reward coin. [1](#0-0) [2](#0-1) 
The resulting `coin_spends` list scales linearly (2N puzzle reveals + solutions, N `SendMessage` conditions in the singleton solution) with the number of unspent, unclaimed reward coins the user has accumulated (e.g., from prolonged self-pooling before ever calling `claim_rewards`). All spends are combined into one `WalletSpendBundle` and pushed as a single transaction, with no size/cost cap. [3](#0-2) 

By contrast, the predecessor `PoolWallet.claim_pool_rewards` explicitly truncates the number of absorbed coins per transaction via `max_spends_in_tx` (default `DEFAULT_MAX_CLAIM_SPENDS = 100`), with an explicit comment that this is done "so the SpendBundle fits into the block": [4](#0-3) [5](#0-4) 

`PlotNFT2Wallet.claim_rewards` has no equivalent batching parameter or truncation logic, so a wallet that has accumulated a sufficiently large number of unclaimed self-pooling reward coins will always build one oversized spend bundle when the user calls `claim_rewards`.

### Impact Explanation
If the number of accumulated reward coins is large enough that the resulting spend bundle's CLVM cost exceeds `MAX_BLOCK_COST_CLVM` (or otherwise exceeds mempool/block-inclusion cost limits), the transaction can never be accepted into the mempool or included in any block. Since `claim_rewards` provides no way to claim a subset of rewards, this can make previously-claimable, legitimately-owned self-pooling rewards permanently unclaimable/unspendable for that plot NFT — a spend-triggered transaction-processing halt for the affected wallet's reward claim path. This is a functional regression relative to the batching protection intentionally implemented in `PoolWallet.claim_pool_rewards`.

### Likelihood Explanation
Likelihood depends on user behavior: any plot-NFT owner who self-pools for an extended period without periodically calling `claim_rewards` (e.g., due to preferring to batch claims, being offline, or simply farming successfully for a long time) will accumulate reward coins linearly with blocks farmed, so the condition is reachable through entirely normal, unprivileged wallet usage rather than requiring an adversary.

### Recommendation
Add a batching/limit parameter to `PlotNFT2Wallet.claim_rewards` (mirroring `PoolWallet.claim_pool_rewards`'s `max_spends_in_tx`) that caps the number of reward coins claimed per spend bundle, splitting into multiple transactions when the unclaimed set is large, so the resulting spend bundle's CLVM cost always stays within block/mempool cost limits.

### Proof of Concept
1. Create a self-pooling `PlotNFT2Wallet` and never call `claim_rewards`.
2. Farm enough blocks to `plotnft_wallet.p2_singleton_puzzle_hash` that `plotnft2_store.get_pool_rewards()` accumulates a very large number of unclaimed reward coins (each additional reward coin adds a `SendMessage` condition in the singleton solution and a full extra `CoinSpend`, growing bundle cost roughly linearly).
3. Call `claim_rewards(action_scope=...)`; observe that `coin_spends`/`spend_bundle` size and cost grow without any truncation, unlike `PoolWallet.claim_pool_rewards`'s `max_spends_in_tx` truncation loop at [6](#0-5) .
4. Once the accumulated reward count is large enough to push the bundle's CLVM cost past `MAX_BLOCK_COST_CLVM`, the resulting transaction cannot be accepted by any node's mempool or included in a block, leaving the rewards permanently unclaimable via this code path.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L156-164)
```python
        rewards_to_claim = await self.wallet_state_manager.plotnft2_store.get_pool_rewards(plotnft_id=self.plotnft_id)
        if len(rewards_to_claim) == 0:
            raise ValueError("No rewards to claim")
        total_reward_amount = uint64(sum(reward.coin.amount for reward in rewards_to_claim))
        if fee > total_reward_amount:
            raise ValueError("Fee is greater than the total amount of rewards")

        plotnft = await self.get_current_plotnft()
        coin_spends = plotnft.claim_pool_rewards(
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L198-200)
```python
        )

        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
```

**File:** chia/pools/plotnft_drivers.py (L622-641)
```python
        return [
            self.singleton_action_spend(
                inner_solution=self.puzzle_with_restrictions().solve(
                    member_validator_solutions=[],
                    dpuz_validator_solutions=[],
                    member_solution=self.bls_member.solve(),
                    delegated_puzzle_and_solution=dpuz_and_solution,
                )
            ),
            *(
                make_spend(
                    coin=reward.coin,
                    puzzle_reveal=reward.puzzle(),
                    solution=reward.solve(
                        self.inner_puzzle_hash(),
                        delegated_puzzle_and_solution=dpuz_and_sol,
                    ),
                )
                for reward, dpuz_and_sol in zip(rewards_to_claim, reward_delegated_puzzles_and_solutions)
            ),
```

**File:** chia/pools/pool_wallet.py (L722-726)
```python
        if max_spends_in_tx is None:
            max_spends_in_tx = self.DEFAULT_MAX_CLAIM_SPENDS
        elif max_spends_in_tx <= 0:
            self.log.info(f"Bad max_spends_in_tx value of {max_spends_in_tx}. Set to {self.DEFAULT_MAX_CLAIM_SPENDS}.")
            max_spends_in_tx = self.DEFAULT_MAX_CLAIM_SPENDS
```

**File:** chia/pools/pool_wallet.py (L756-762)
```python
            if first_coin_record is None:
                first_coin_record = coin_record
            if len(all_spends) >= max_spends_in_tx:
                # Limit the total number of spends, so the SpendBundle fits into the block
                self.log.info(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                print(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                break
```
