### Title
`spend_clawback_coins()` marks the incoming clawback transaction as "sent" before the outgoing claim spend is actually assembled or pushed, permanently blocking re-claim on failure - (File: `chia/wallet/clawback_manager.py`)

### Summary
`ClawbackManager.spend_clawback_coins()` calls `self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)` for each clawback coin inside the per-coin `try` block, marking the coin's `incoming_tx` as "sent" before the aggregate outgoing spend bundle is finished being built, before any fee transaction is generated, and before anything is pushed to the mempool. If a later step in the same call fails (most notably `create_tandem_xch_tx()` when `fee > 0`), the function raises and the outgoing claim `tx_record` (with the real `spend_bundle`) is never appended to `action_scope`, so nothing is ever broadcast — yet the coin's `incoming_tx.sent` is already non-zero.

### Finding Description [1](#0-0) 

For each targeted clawback coin, the loop builds the `CoinSpend`, appends it to `coin_spends`, and immediately calls `increment_sent(...)` to mark the coin's `incoming_tx` as pending/sent — this happens per-coin, independent of whether the overall claim operation later succeeds:

```python
coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
coin_spends.append(coin_spend)
# Update incoming tx to prevent double spend and mark it is pending
await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
```

After the loop, if `fee > 0`, the code calls `self.xch_wallet.create_tandem_xch_tx(...)` inside a nested action scope to add a fee spend: [2](#0-1) 

`create_tandem_xch_tx()` performs coin selection for the fee and can raise (e.g. `ValueError` for insufficient balance/coins, similar to standard wallet spend-construction failures elsewhere in the codebase). If it raises here, the exception propagates out of `spend_clawback_coins()` before the final outgoing `tx_record` (containing the actual `spend_bundle`) is ever created or appended to `action_scope.side_effects.transactions`: [3](#0-2) 

Because the exception occurs after `increment_sent(...)` already ran for every coin in `clawback_coins`, every one of those coins' `incoming_tx.sent` is now `> 0`, even though no spend bundle was constructed for them and nothing was submitted anywhere.

On the next attempt to claim (either via `auto_claim_coins()` or an explicit user-initiated claim that reaches `spend_clawback_coins()` without `force=True`), the gate at the top of the per-coin loop skips these coins entirely: [4](#0-3) 

```python
if incoming_tx.sent > 0 and not force:
    self.log.error(
        f"Clawback coin {coin.name().hex()} is already in a pending spend bundle. {incoming_tx}"
    )
    continue
```

Since `auto_claim_coins()` never passes `force=True`, automatic clawback claiming becomes permanently stuck for these coins after a single transient failure (e.g., a temporary lack of fee coins, an action-scope error, or any other exception raised by `create_tandem_xch_tx`), unless a wallet user manually notices and re-invokes the claim path with `force=True`.

This is directly analogous to the reported Sherlock issue: state that represents "this claim has happened" (`accountRewardDebt` there, `incoming_tx.sent` here) is updated unconditionally on the attempt path rather than being gated on the attempt's actual success, so a failed/incomplete operation silently consumes the "claim" bookkeeping and the affected assets become unreachable through the normal path.

### Impact Explanation
Users lose the ability to reclaim their own clawback coins through the standard, non-`force` path after a single failed claim attempt (e.g., a transient fee-coin-selection failure), because the coin's `incoming_tx.sent` marker is set unconditionally before the outgoing spend bundle is confirmed to be constructible and pushable. Auto-claim (`auto_claim_coins`), which never uses `force=True`, will silently and permanently skip these coins going forward. This is a fund-availability/lock issue for the coin owner, not just a UX inconvenience, since the wallet's normal reclaim workflow no longer surfaces or acts on the coin unless the user manually discovers and re-runs the flow with the `force` flag.

### Likelihood Explanation
Any exception raised after the per-coin loop but before the final `tx_record` append — most plausibly `create_tandem_xch_tx()` failing due to insufficient/locked fee coins, which is a routine, non-adversarial condition — will trigger this. This makes the bug reachable through ordinary wallet operation (a wallet owner claiming their own clawback coins with `fee > 0`), not just an edge case requiring an attacker.

### Recommendation
Only call `increment_sent(...)` after the full outgoing spend bundle (including any fee tandem spend) has been successfully constructed and the corresponding `tx_record` has been appended to `action_scope`'s side effects — i.e., move the "mark as sent" bookkeeping to occur atomically with, or after, the point where the transaction is guaranteed to be recorded/pushed, not per-coin inside the earlier build loop. Alternatively, wrap the whole per-coin loop and the fee-tx construction in a single `try`/`except` so that any failure after the loop rolls back (or never applies) the `increment_sent` calls made for the coins in that batch.

### Proof of Concept
1. Accumulate one or more clawback coins eligible for claim (recipient-side, time lock elapsed) so `auto_claim_coins()`/`spend_clawback_coins()` is invoked with `fee > 0`.
2. Cause `create_tandem_xch_tx()` to fail during the fee-tx construction step (e.g., no eligible XCH coins available for the fee, or any other exception in that call) while `clawback_coins` is non-empty and the per-coin loop has already succeeded and called `increment_sent(...)` for those coins.
3. Observe: the exception propagates out of `spend_clawback_coins()`, no outgoing `tx_record`/`spend_bundle` is appended to `action_scope`, and nothing is pushed to the mempool — but each affected coin's `incoming_tx.sent` is now `> 0`.
4. Re-run the claim (e.g., via the next `auto_claim_coins()` cycle, without `force=True`): the affected coins are skipped by the `incoming_tx.sent > 0 and not force` check and are never retried, leaving the coin owner unable to claim through the normal flow.

### Citations

**File:** chia/wallet/clawback_manager.py (L220-263)
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
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
```

**File:** chia/wallet/clawback_manager.py (L266-298)
```python
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
```

**File:** chia/wallet/clawback_manager.py (L299-321)
```python
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
            interface.side_effects.transactions.append(tx_record)
```
