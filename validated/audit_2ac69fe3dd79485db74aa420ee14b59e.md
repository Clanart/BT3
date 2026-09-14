### Title
Clawback claim marks the incoming transaction as "sent/pending" before the outgoing spend is ever built or pushed, permanently blocking future claim attempts if a later step in `spend_clawback_coins()` fails - ([File: chia/wallet/clawback_manager.py])

### Summary
`ClawbackManager.spend_clawback_coins()` calls `self.transaction_store.increment_sent(incoming_tx.name, ...)` for each clawback coin as soon as a `CoinSpend` is generated, marking the coin's original incoming transaction as pending/sent. This state mutation happens *before* the resulting spend bundle is aggregated with any fee transaction, wrapped in a `TransactionRecord`, and appended to the action scope's side effects (i.e., before the spend is ever actually pushed to the network). If a later step in the same call fails (e.g. `create_tandem_xch_tx` when `fee > 0`, or aggregation/derivation assertions), the coin's incoming tx is left permanently marked `sent > 0` with no corresponding outgoing transaction ever created or broadcast.

### Finding Description
In `chia/wallet/clawback_manager.py`, `spend_clawback_coins()` loops over the requested clawback coins: [1](#0-0) [2](#0-1) 

For each coin it builds a `coin_spend`, appends it to `coin_spends`, and then immediately calls `increment_sent(...)` to mark the coin's `incoming_tx` as pending. This happens *inside* the per-coin loop, well before:
- the fee-tandem transaction is built via `create_tandem_xch_tx` (only reached after the loop, guarded by `fee > 0`),
- the final `spend_bundle` is aggregated,
- the `TransactionRecord` for the outgoing spend is constructed and appended to `action_scope`'s side effects: [3](#0-2) [4](#0-3) 

Any exception raised after the loop but before the `TransactionRecord` is appended (in `create_tandem_xch_tx`, in `WalletSpendBundle.aggregate`, or in the `assert derivation_record is not None`/`assert incoming_tx is not None` checks for coins processed earlier in the same batch) propagates out of `spend_clawback_coins()` with no exception handling at that level, leaving:
- the incoming tx's `sent` field already incremented (`sent > 0`), and
- no outgoing `TransactionRecord`/spend bundle ever created or pushed.

Subsequent calls to `spend_clawback_coins()` for the same coin then hit the early-exit guard: [5](#0-4) 

`if incoming_tx.sent > 0 and not force: ... continue` — the coin is silently skipped every time, both for manual claims via the RPC (`spend_clawback_coins` endpoint) and for the periodic `auto_claim_coins()` path, since neither passes `force=True` by default: [6](#0-5) [7](#0-6) 

This mirrors the report's bug class exactly: state that represents "this transfer has happened" is committed before the transfer is actually finalized/pushed, so a failure downstream leaves the system in an inconsistent state where the coin looks permanently "in flight" even though no spend was ever produced.

### Impact Explanation
A clawback (sender-revert or recipient-claim) coin whose batch triggers a failure after `increment_sent` is called becomes stuck: the wallet believes a spend is already pending for that coin and will not automatically retry it (auto-claim skips it) and the normal RPC path also skips it, since `force` defaults to `False` in both the RPC handler and `auto_claim_coins`. The user's only recourse is to explicitly pass `force=True` via `spend_clawback_coins` RPC, which the code itself flags as risking a double-spend ("Force to push the spend bundle even it may be a double spend"). Funds are not stolen, but the user loses the normal, safe path to claim/revert their coin and must resort to an explicitly-marked-unsafe override, which is a meaningful degradation of the clawback safety mechanism and a spend-triggered halt on legitimate transaction processing for that coin until manual intervention.

### Likelihood Explanation
This requires a downstream failure in the same call after at least one coin's `increment_sent` has already executed — plausible whenever a fee is requested (`fee > 0`, exercising `create_tandem_xch_tx`, coin selection, or announcement assertions) or when a batch contains multiple coins and any per-coin exception handling masks a partial failure. Since `auto_claim_coins()` batches multiple coins together with a nonzero `auto_claim_tx_fee` by default, and batches are processed unattended, this code path is reachable during normal wallet operation without any attacker involvement — any legitimate coin-selection/fee-transaction failure (e.g., insufficient available balance for the fee, a coin-selection race, or transient exception) is sufficient to trigger it.

### Recommendation
Move the `increment_sent()` call (and any other "spend is now pending" state mutation) to occur only after the full spend bundle has been successfully built, aggregated with the fee transaction, and the corresponding `TransactionRecord` has been appended to the action scope's side effects — i.e., only once the spend is guaranteed to be part of the final pushed transaction. Alternatively, wrap the per-batch logic in a try/except that rolls back (`decrement`/resets) the `sent` marker for any coin whose spend did not make it into the final transaction.

### Proof of Concept
1. Create two clawback coins for a wallet (e.g., via `run_send_cmd` with `clawback_time`), then let their timelocks expire so they're claimable.
2. Call `spend_clawback_coins` RPC with `fee > 0` for both coins in one batch (exercising `auto_claim_batch_size >= 2` or a manual batch call), such that `create_tandem_xch_tx` (invoked once, after the per-coin loop) throws (e.g., by making the fee coin selection fail due to insufficient available/unlocked XCH balance at that exact moment).
3. Observe: the first coin(s) in the loop already had `increment_sent()` called and thus have `incoming_tx.sent > 0` in `WalletTransactionStore`, yet the exception prevents any outgoing `TransactionRecord`/spend bundle from ever being appended or pushed.
4. Re-invoke `spend_clawback_coins` (manual or via `auto_claim_coins()`) for the same coin without `force=True`: the coin is skipped with the log "Clawback coin ... is already in a pending spend bundle," even though no spend bundle was ever actually produced — the coin is stuck until the caller explicitly passes the double-spend-risking `force=True` flag.

### Citations

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

**File:** chia/wallet/clawback_manager.py (L220-230)
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
```

**File:** chia/wallet/clawback_manager.py (L258-263)
```python
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
```

**File:** chia/wallet/clawback_manager.py (L264-320)
```python
        if len(coin_spends) == 0:
            return
        spend_bundle = WalletSpendBundle(coin_spends, G2Element())
        if fee > 0:
            async with self.action_scope_sandbox(action_scope.config.tx_config, False) as inner_action_scope:
                async with action_scope.use() as interface:
                    async with inner_action_scope.use() as inner_interface:
                        inner_interface.side_effects.selected_coins = interface.side_effects.selected_coins
                    await self.xch_wallet.create_tandem_xch_tx(
                        fee,
                        inner_action_scope,
                        extra_conditions=(
                            AssertCoinAnnouncement(asserted_id=coin_spends[0].coin.name(), asserted_msg=message),
                        ),
                    )
                    async with inner_action_scope.use() as inner_interface:
                        # This should not be looked to for best practice.
                        # Ideally, the two spend bundles can exist separately on each tx record until they are pushed.
                        # This is not very supported behavior at the moment
                        # so to avoid any potential backwards compatibility issues,
                        # we're moving the spend bundle from this TX to the main
                        interface.side_effects.transactions.extend(
                            [replace(tx, spend_bundle=None) for tx in inner_interface.side_effects.transactions]
                        )
                        interface.side_effects.selected_coins.extend(inner_interface.side_effects.selected_coins)
            spend_bundle = WalletSpendBundle.aggregate(
                [
                    spend_bundle,
                    *(
                        tx.spend_bundle
                        for tx in inner_action_scope.side_effects.transactions
                        if tx.spend_bundle is not None
                    ),
                ]
            )
        assert derivation_record is not None
        tx_record = TransactionRecord(
            confirmed_at_height=uint32(0),
            created_at_time=uint64(time.time()),
            to_puzzle_hash=derivation_record.puzzle_hash,
            to_address=self.puzzle_hash_encoder(derivation_record.puzzle_hash),
            amount=amount,
            fee_amount=uint64(fee),
            confirmed=False,
            sent=uint32(0),
            spend_bundle=spend_bundle,
            additions=spend_bundle.additions(),
            removals=spend_bundle.removals(),
            wallet_id=uint32(1),
            sent_to=[],
            trade_id=None,
            type=uint32(TransactionType.OUTGOING_CLAWBACK),
            name=spend_bundle.name(),
            memos=compute_memos(spend_bundle),
            valid_times=parse_timelock_info(extra_conditions),
        )
        async with action_scope.use() as interface:
```

**File:** chia/wallet/wallet_rpc_api.py (L1513-1520)
```python
            await self.service.wallet_state_manager.clawback_manager.spend_clawback_coins(
                # Semantically, we're guaranteed the right type here, but the typing isn't there
                coin_batch,  # type: ignore[arg-type]
                request.fee,
                action_scope,
                request.force,
                extra_conditions=extra_conditions,
            )
```
