### Title
Batched clawback claims fail as a single unit on one already-spent coin, stalling unrelated claims - (File: chia/wallet/clawback_manager.py)

### Summary
`ClawbackManager.spend_clawback_coins` aggregates multiple, independently-spendable clawback coins into a single `WalletSpendBundle` and pushes it as one transaction. If even one coin in that batch has already been spent (e.g. a race between `auto_claim_coins` and a manual `spend_clawback_coins` RPC call, or a state-sync race after a coin was clawed back/claimed through another path), the entire aggregated spend bundle is rejected as `DOUBLE_SPEND` by the mempool, causing every other, still-valid clawback coin in that batch to fail to claim as well — mirroring the "heal()" batch-revert pattern from the external report, where one bad element in a batch reverts the whole operation.

### Finding Description
`spend_clawback_coins` iterates over a dict of clawback coins, builds one `CoinSpend` per coin, and marks each coin's underlying transaction record as pending via `increment_sent` (`sent > 0`) before ever submitting anything on-chain: [1](#0-0) 

All the individually built `CoinSpend`s are then combined into **one** `WalletSpendBundle` and submitted as a single transaction: [2](#0-1) 

When this bundle reaches the mempool, `check_removals()` evaluates every coin in the bundle; if *any* coin is already spent (and not fast-forward-eligible), the function returns `Err.DOUBLE_SPEND` for the **entire** mempool item, not just the offending coin: [3](#0-2) 

Both the RPC entrypoint `spend_clawback_coins` and the periodic `auto_claim_coins` background task build these batches from the same pool of "unspent, not-yet-claimed" clawback coins: [4](#0-3) [5](#0-4) 

The only per-coin race guard present is the `incoming_tx.sent > 0` check inside the *same* call to `spend_clawback_coins` — it does not protect against two concurrent callers (manual RPC call vs. the auto-claim background loop, or two wallet clients sharing a key) each picking up overlapping coin sets and building two different aggregate bundles. Whichever bundle is confirmed/accepted first spends its coins; the second bundle, which may contain 49 perfectly claimable coins plus 1 that overlaps with the first, is rejected wholesale by `check_removals`, not partially.

This is directly analogous to the `heal()` issue: a batch operation whose validity check is evaluated as an all-or-nothing gate (`_assertAgentStatus` in the original report vs. `DOUBLE_SPEND` in `check_removals`), so a single stale/contested element in the batch causes the loss of the entire batch's work.

### Impact Explanation
- Gas/fee loss: the fee paid for the aggregated clawback-claim transaction is wasted when the whole bundle is rejected, even though only one of many coins was the actual conflict.
- Availability/DoS on legitimate claims: dozens of otherwise-claimable clawback coins (up to `auto_claim_batch_size`, default 50) fail to be claimed in that cycle purely because of one unrelated conflicting coin, delaying recovery of funds until the next auto-claim cycle or another manual resubmission.
- The `sent` flag is already incremented to `PENDING` for every coin in the failed batch (line 261) before the aggregate spend is even built, which can cause subsequent auto-claim cycles to skip those coins under `incoming_tx.sent > 0 and not force` (line 226), further delaying recovery until wallet-level TX resend/retry logic re-triggers.

This does not constitute unauthorized fund movement (the puzzle still requires the correct sender/recipient key to sign each individual coin spend, so no external attacker can steal funds), but it is a concrete transaction-processing halt triggered by ordinary concurrent/racing wallet activity on the same key, matching the accepted "spend-triggered transaction halted" impact class.

### Likelihood Explanation
This requires no malicious third party — it can be triggered purely by normal concurrent use of the wallet (e.g. `auto_claim` enabled while a user also manually calls `spend_clawback_coins`/`spend_clawback` RPC, or two wallet instances sharing the same key both auto-claiming). Given `auto_claim` runs periodically and batches can be large (default batch size 50), the window for an overlapping batch race is realistic in normal operation, making likelihood moderate.

### Recommendation
Avoid aggregating unrelated clawback coin claims into a single all-or-nothing `WalletSpendBundle`. Options:
1. Re-validate each coin's spent status against current mempool/coin-store state immediately before building the aggregate bundle, and drop any coin found already spent instead of assuming a stable state throughout the loop.
2. Use a locking mechanism (or at least an in-process mutex) so `auto_claim_coins` and RPC-triggered `spend_clawback_coins` cannot run concurrently over overlapping coin sets.
3. Consider splitting large batches into independently-pushable sub-bundles (or per-coin transactions when a conflict is detected) so a single already-spent coin does not block the other coins from being claimed in the same cycle.

### Proof of Concept
1. Enable `auto_claim` with a batch size > 1 for a wallet holding several claimable clawback coins (`set_auto_claim`, `AutoClaimSettings`).
2. Concurrently, call the `spend_clawback_coins` RPC manually specifying the same (or overlapping) coin IDs while `auto_claim_coins` is also picking up the same unspent clawback coins on its periodic pass.
3. Both flows build a `WalletSpendBundle` aggregating multiple coins via `spend_clawback_coins` in `chia/wallet/clawback_manager.py` lines 216-266.
4. Whichever bundle's coin spends land on-chain first causes the second bundle's shared coin to be reported `spent` by the full node.
5. When the second (larger) bundle is submitted, `check_removals` in `chia/full_node/mempool_manager.py` (lines 243-246) rejects the entire bundle with `Err.DOUBLE_SPEND`, failing to claim all the other, unrelated, still-unspent coins bundled alongside it.

### Citations

**File:** chia/wallet/clawback_manager.py (L177-205)
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

**File:** chia/wallet/clawback_manager.py (L220-261)
```python
        for coin, metadata in clawback_coins.items():
            try:
                self.log.info(f"Claiming clawback coin {coin.name().hex()}")
                # Get incoming tx
                incoming_tx = await self.transaction_store.get_transaction_record(coin.name())
                assert incoming_tx is not None, f"Cannot find incoming tx for clawback coin {coin.name().hex()}"
                if incoming_tx.sent > 0 and not force:
                    self.log.error(
                        f"Clawback coin {coin.name().hex()} is already in a pending spend bundle. {incoming_tx}"
                    )
                    continue

                recipient_puzhash = metadata.recipient_puzzle_hash
                sender_puzhash = metadata.sender_puzzle_hash
                is_recipient: bool = await metadata.is_recipient(self.puzzle_store)
                if is_recipient:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(recipient_puzhash)
                else:
                    derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(sender_puzhash)
                assert derivation_record is not None
                amount = uint64(amount + coin.amount)
                # Remove the clawback hint since it is unnecessary for the XCH coin
                memos: list[bytes] = [] if len(incoming_tx.memos) == 0 else next(iter(incoming_tx.memos.items()))[1][1:]
                inner_puzzle = self.xch_wallet.puzzle_for_pk(derivation_record.pubkey)
                inner_solution = self.xch_wallet.make_solution(
                    primaries=[
                        CreateCoin(
                            derivation_record.puzzle_hash,
                            uint64(coin.amount),
                            memos,  # Forward memo of the first coin
                        )
                    ],
                    conditions=(
                        extra_conditions
                        if len(coin_spends) > 0 or fee == 0
                        else (*extra_conditions, CreateCoinAnnouncement(message))
                    ),
                )
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
```

**File:** chia/wallet/clawback_manager.py (L264-266)
```python
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
```

**File:** chia/full_node/mempool_manager.py (L243-246)
```python
    for coin_id, coin_bcs in bundle_coin_spends.items():
        # 1. Checks if it's been spent already
        if removals[coin_id].spent and not coin_bcs.supports_fast_forward:
            return Err.DOUBLE_SPEND, []
```

**File:** chia/wallet/wallet_rpc_api.py (L1479-1521)
```python
    async def spend_clawback_coins(
        self,
        request: SpendClawbackCoins,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> SpendClawbackCoinsResponse:
        """Spend clawback coins that were sent (to claw them back) or received (to claim them).

        :param coin_ids: list of coin ids to be spent
        :param batch_size: number of coins to spend per bundle
        :param fee: transaction fee in mojos
        :return:
        """
        coin_records = await self.service.wallet_state_manager.coin_store.get_coin_records(
            coin_id_filter=HashFilter.include(request.coin_ids),
            coin_type=CoinType.CLAWBACK,
            wallet_type=WalletType.STANDARD_WALLET,
            spent_range=UInt32Range(stop=uint32(0)),
        )

        batch_size = (
            request.batch_size
            if request.batch_size is not None
            else self.service.wallet_state_manager.clawback_manager.auto_claim_batch_size
        )
        records_list = list(coin_records.coin_id_to_record.values())
        for i in range(0, len(records_list), batch_size):
            try:
                coin_batch = {
                    coin_record.coin: coin_record.parsed_metadata() for coin_record in records_list[i : i + batch_size]
                }
            except WalletCoinRecordMetadataParsingError as e:
                log.error("Failed to spend clawback coin: %s", e)
                continue
            await self.service.wallet_state_manager.clawback_manager.spend_clawback_coins(
                # Semantically, we're guaranteed the right type here, but the typing isn't there
                coin_batch,  # type: ignore[arg-type]
                request.fee,
                action_scope,
                request.force,
                extra_conditions=extra_conditions,
            )

```
