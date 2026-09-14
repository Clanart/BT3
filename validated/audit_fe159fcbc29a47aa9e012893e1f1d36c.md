### Title
Unbounded clawback-dust flood causes `auto_claim_coins` to loop indefinitely, halting wallet's synced transaction processing - (File: chia/wallet/clawback_manager.py)

### Summary
`ClawbackManager.auto_claim_coins` fetches **all** unspent clawback coins for the wallet via `coin_store.get_coin_records(...)` with no size cap (`GetCoinRecords.limit` defaults to `uint32.MAXIMUM`), then iterates over every one of them in an unbounded Python `for` loop, doing per-coin metadata parsing and async timestamp lookups before batching a spend. Any counterparty can cheaply create an arbitrarily large number of tiny clawback outputs to a victim's puzzle hash (a clawback send just requires a `CREATE_COIN` condition using the clawback puzzle in a single spend bundle, so hundreds/thousands of dust clawback coins can be created in one or a handful of transactions). Because `auto_claim_coins` is invoked automatically by the wallet node (from `wallet_node.py`) as new blocks sync in, this attacker-controlled, ever-growing unspent-clawback-coin set is re-scanned in full on every trigger, similarly to the reported `CrabNetting.netAtPrice` bug where a malicious actor pads an array with cheap zero-value entries that a privileged function must then iterate over in full, unboundedly.

### Finding Description
`auto_claim_coins` (chia/wallet/clawback_manager.py:177-205) queries: [1](#0-0) 
with no `limit` argument, so `GetCoinRecords.limit` defaults to `uint32.MAXIMUM` (chia/wallet/wallet_coin_store.py:32), returning every unspent clawback coin belonging to the wallet in one unbounded list. The function then walks that entire list one coin at a time: [2](#0-1) 
performing a `parsed_metadata()` call, an `is_recipient(...)` puzzle-store lookup, and a `timestamp_for_height(...)` await *per coin*, batching into `spend_clawback_coins` only once `auto_claim_batch_size` items accumulate as eligible-to-claim. Crucially, coins that are *not yet* claimable (time-lock not elapsed) are simply skipped but still fully iterated and re-scanned every single time the function runs — there is no early exit, no cost bound, and no cap on the number of clawback coins the wallet will accept and store per victim puzzle hash.

An attacker (any wallet user, offer counterparty, or ordinary spend-bundle submitter) can send a large number of clawback-typed outputs to a target's puzzle hash using clawback puzzle reveals, either via many cheap transactions or by packing many `CREATE_COIN` conditions with the clawback puzzle into few transactions. Each such coin becomes a persistent `WalletCoinRecord` of `CoinType.CLAWBACK` in the victim's coin store (chia/wallet/clawback_manager.py:132-144, `identify`), which is never pruned until claimed. Since `auto_claim_coins` is called automatically as part of wallet sync (referenced from `chia/wallet/wallet_node.py`), this attacker-inflated, ever-growing set is rescanned start-to-finish on every trigger, with cost growing linearly (and with wall-clock-latency per coin due to the async DB/puzzle-store lookups) in the number of dust coins the attacker has sent — with no upper bound enforced anywhere in the code path.

### Impact Explanation
This mirrors the reported CrabNetting class of bug: an unprivileged actor cheaply inflates an on-chain-derived array (here, the victim wallet's clawback coin set) that a routine, automatically-triggered function must then process in an unbounded loop. As the attacker keeps growing the set (at negligible relative cost — dust amounts, batched into few transactions), the victim wallet's `auto_claim_coins` invocation takes proportionally longer each time it runs, and because it runs on every new-block sync, the wallet can fall permanently behind, effectively stalling processing of the victim's own subsequent transactions/syncing — a spend-triggered transaction-processing halt confined to the targeted wallet.

### Likelihood Explanation
Likelihood is Medium-to-High: creating clawback outputs to an arbitrary recipient puzzle hash requires no special privilege — it is a standard spend a counterparty can construct unilaterally, and the cost of dust clawback coins is far lower than the ongoing scanning cost imposed on the victim due to the complete lack of any pagination/limit or per-call work bound in `auto_claim_coins`/`get_coin_records`.

### Recommendation
Add a hard cap/limit to the `get_coin_records` call inside `auto_claim_coins` (mirroring the `max_get_coin_records_limit` already enforced in the RPC layer at chia/wallet/wallet_rpc_api.py:2764-2767) and process claimable coins in bounded paginated batches per invocation rather than materializing and iterating the entire unspent-clawback set every sync cycle. Consider also enforcing a minimum clawback coin amount / per-sender rate limiting to prevent dust-coin accumulation in the first place.

### Proof of Concept
1. Attacker crafts a spend bundle whose puzzle reveal is the clawback puzzle, targeting the victim's `recipient_puzzle_hash`, with a small `amount` (e.g., 1 mojo) and a short/irrelevant `time_lock`.
2. Attacker repeats this (or packs many such `CREATE_COIN` conditions into one spend) thousands of times cheaply, each confirmed clawback coin being recorded by the victim's `ClawbackManager.identify` as `CoinType.CLAWBACK` (chia/wallet/clawback_manager.py:132-144).
3. On every new block synced by the victim's wallet node, `auto_claim_coins` (chia/wallet/clawback_manager.py:177) is invoked and must fetch and loop over the entire, attacker-inflated unspent clawback coin list with no limit — degrading linearly with the number of dust coins injected, with no mechanism in the code to bound or short-circuit this scan.

### Citations

**File:** chia/wallet/clawback_manager.py (L177-189)
```python
    async def auto_claim_coins(self, action_scope: WalletActionScope) -> None:
        # Get unspent clawback coin
        current_timestamp = self.blockchain.get_latest_timestamp()
        clawback_coins: dict[Coin, ClawbackMetadata] = {}
        unspent_coins = await self.coin_store.get_coin_records(
            coin_type=CoinType.CLAWBACK,
            wallet_type=WalletType.STANDARD_WALLET,
            spent_range=UInt32Range(stop=uint32(0)),
            amount_range=UInt64Range(
                start=action_scope.config.tx_config.coin_selection_config.min_coin_amount,
                stop=action_scope.config.tx_config.coin_selection_config.max_coin_amount,
            ),
        )
```

**File:** chia/wallet/clawback_manager.py (L191-205)
```python
        for coin in unspent_coins.records:
            try:
                metadata = coin.parsed_metadata()
                assert isinstance(metadata, ClawbackMetadata)
                if await metadata.is_recipient(self.puzzle_store):
                    coin_timestamp = await self.timestamp_for_height(coin.confirmed_block_height)
                    if current_timestamp - coin_timestamp >= metadata.time_lock:
                        clawback_coins[coin.coin] = metadata
                        if len(clawback_coins) >= self.auto_claim_batch_size:
                            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
                            clawback_coins = {}
            except Exception as e:
                self.log.error(f"Failed to claim clawback coin {coin.coin.name().hex()}: %s", e)
        if len(clawback_coins) > 0:
            await self.spend_clawback_coins(clawback_coins, self.auto_claim_tx_fee, action_scope)
```
