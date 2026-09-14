### Title
Pool reward claim batches multiple absorb spends into one atomic `SpendBundle`, so a single stale/invalid reward coin blocks claiming of all other legitimately claimable rewards - (`File: chia/pools/pool_wallet.py`)

### Summary
`PoolWallet.claim_pool_rewards()` batches CoinSpends for *all* outstanding p2-singleton farming reward coins into a single `WalletSpendBundle`, chaining singleton absorb spends together. Like the reported `AssetManager.sol#rebalance` pattern - where looping `withdrawAll()` over several money markets means one reverting adapter fails the whole rebalance - this Chia analog loops over multiple reward coins and folds them into one all-or-nothing spend bundle: if any single reward coin's absorb spend is invalid (e.g. the wallet's locally tracked farming-reward coin is stale relative to chain state), the entire batched transaction is rejected, and none of the coins in that batch - including otherwise perfectly valid ones - can be claimed until the user manually intervenes.

### Finding Description
`claim_pool_rewards()` iterates the wallet's locally recorded unspent coin records that match `coin_to_height_farmed` (derived from `WalletTransactionStore.get_farming_rewards()`), and for each one calls `create_absorb_spend()`, chaining `last_solution` forward so successive absorb spends form a single singleton lineage: [1](#0-0) 

All constructed `CoinSpend`s are merged into one `WalletSpendBundle`: [2](#0-1) 

There is no defensive check equivalent to the `getSupply(tokenAddress) > 0` skip recommended in the Solidity report - i.e., no verification that each reward coin is still genuinely unspent/claimable on-chain before it is folded into the batch, and no per-coin exception handling or skip-on-failure logic (contrast this with `ClawbackManager.spend_clawback_coins()`/`auto_claim_coins()`, which wrap each coin in `try/except` and `continue` on failure - `chia/wallet/clawback_manager.py:191-263`). Because all the pool-reward absorb spends are chained into the same singleton lineage and pushed as one atomic `SpendBundle`, a single problematic coin (e.g. one whose local wallet coin-store record is stale due to sync delay/reorg, or one that no longer matches expected chain state for the absorb puzzle) causes mempool/consensus validation to reject the transaction as a whole (an all-or-nothing bundle, same principle demonstrated for singleton spends in `chia/_tests/pools/test_pool_puzzles_lifecycle.py:246-252`, where a bad singleton coinsol makes the whole `SpendBundle` fail with `BadSpendBundleError`).

### Impact Explanation
A user who calls `claim_pool_rewards()` (via `pw_absorb_rewards` RPC) to claim potentially many outstanding farming rewards can have the entire batch rejected due to one bad/stale reward coin, even though the remaining coins in the batch are individually valid and spendable. Since `max_spends_in_tx` only truncates the *count* of spends and provides no mechanism to exclude or skip a specific problematic coin, the user has no way to work around the failure except manual coin-store inspection/repair, effectively halting reward-claiming for the entire pool wallet until resolved.

### Likelihood Explanation
This requires the wallet's own farming-reward bookkeeping (`get_farming_rewards()` / local unspent coin records) to diverge from consensus-confirmed spendability for at least one reward coin among potentially dozens batched together (`DEFAULT_MAX_CLAIM_SPENDS = 100`). This can plausibly occur after reorgs, sync races, or previously-partial claim attempts, and does not require any privileged or malicious third party - it's entirely self-triggered by a normal wallet user's own claim action, which matches the "spend-triggered transaction-processing halt" category.

### Recommendation
Before including a reward coin's absorb spend in the batch, verify (e.g., via a fresh coin-state lookup) that it is genuinely unspent and consistent with the expected singleton lineage, and skip/log any coin that fails this check rather than aborting the whole batch - mirroring the recommended fix of skipping a money market with zero supply instead of unconditionally calling `withdrawAll()` on all of them. Alternatively, wrap the batch to allow partial-batch construction and let `claim_pool_rewards()` continue building spends for the remaining valid coins if the current locally-tracked coin turns out to already be spent/invalid.

### Proof of Concept
1. Farm several self-pooling rewards so multiple p2-singleton reward coins are tracked as unspent/farming rewards in the wallet DB.
2. Trigger a scenario where the wallet's local coin-store/tx-store view of one specific reward coin becomes stale relative to consensus (e.g. simulate by manually invalidating/removing that coin's on-chain spendability while it's still marked unspent locally, similar to the singleton "spend a coin twice" scenario demonstrated in `chia/_tests/pools/test_pool_puzzles_lifecycle.py:239-252`).
3. Call `claim_pool_rewards()` (or `pw_absorb_rewards` RPC) with `max_spends_in_tx` large enough to include both the stale coin and other valid reward coins in the same batch.
4. Observe that `create_absorb_spend()`/mempool validation fails for the whole `WalletSpendBundle`, and none of the valid reward coins in that same batch can be claimed, even though most of them are individually spendable - matching the report's "single moneyMarket.withdrawAll revert reverts rebalancing transaction" pattern.

### Citations

**File:** chia/pools/pool_wallet.py (L752-778)
```python
        first_coin_record = None
        for coin_record in unspent_coin_records:
            if coin_record.coin not in coin_to_height_farmed:
                continue
            if first_coin_record is None:
                first_coin_record = coin_record
            if len(all_spends) >= max_spends_in_tx:
                # Limit the total number of spends, so the SpendBundle fits into the block
                self.log.info(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                print(f"pool wallet truncating absorb to {max_spends_in_tx} spends to fit into block")
                break
            absorb_spend: list[CoinSpend] = create_absorb_spend(
                last_solution,
                current_state.current,
                current_state.launcher_coin,
                coin_to_height_farmed[coin_record.coin],
                self.wallet_state_manager.constants.GENESIS_CHALLENGE,
                delayed_seconds,
                delayed_puzhash,
            )
            last_solution = absorb_spend[0]
            all_spends += absorb_spend
            total_amount += coin_record.coin.amount
            self.log.info(
                f"Farmer coin: {coin_record.coin} {coin_record.coin.name()} {coin_to_height_farmed[coin_record.coin]}"
            )
        if len(all_spends) == 0 or first_coin_record is None:
```

**File:** chia/pools/pool_wallet.py (L781-781)
```python
        claim_spend = WalletSpendBundle(all_spends, G2Element())
```
