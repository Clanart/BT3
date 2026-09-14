## Analysis: Analog Found

The Hubble `processWithdrawals` bug class — a batch-processing loop that marks an item as "handled/pending" *before* the actual transfer is confirmed, then silently skips it forever on any later failure with no automatic retry — has a concrete structural analog in Chia's clawback auto-claim flow.

### Title
Clawback auto-claim marks coins as "pending" before spend confirmation, permanently stalling reclaim on any downstream failure - (File: `chia/wallet/clawback_manager.py`)

### Summary
`ClawbackManager.spend_clawback_coins` increments a coin's transaction `sent` counter to `PENDING` speculatively, before the aggregated spend bundle is actually built, signed, and pushed to the mempool. If any step after that point fails, the coin is left marked as "already pending" with no automatic mechanism to reset it, so the periodic auto-claim job silently and permanently skips it going forward.

### Finding Description
`auto_claim_coins` is invoked automatically on every new peak when the `auto_claim` config flag is enabled, with no `force` override: [1](#0-0) 

Inside `spend_clawback_coins`, for each candidate coin the code first checks `incoming_tx.sent > 0 and not force` and `continue`s (permanently skipping) if so: [2](#0-1) 

Critically, later in the *same* per-coin loop iteration, before the aggregated `spend_bundle` is even assembled, before the fee-tandem transaction is created, and before the final `TransactionRecord` is appended to the action-scope side effects, the code calls: [3](#0-2) 

This `increment_sent(..., MempoolInclusionStatus.PENDING, None)` call happens speculatively, "to prevent double spend and mark it as pending." However, everything that follows — building `spend_bundle = WalletSpendBundle(coin_spends, ...)`, the optional fee-tandem transaction creation block (which is *not* wrapped in a try/except), and finally appending the `tx_record` to `action_scope.side_effects.transactions` — can still fail or simply never get pushed to the network: [4](#0-3) 

If that later code raises (e.g. insufficient funds for the fee in `create_tandem_xch_tx`, or any other exception), it propagates out of `spend_clawback_coins`/`auto_claim_coins` uncaught, and the never-actually-broadcast `TransactionRecord` never gets created or pushed. Yet the coin's underlying `incoming_tx.sent` was already bumped to `PENDING`. On every subsequent `new_peak_wallet` cycle, `auto_claim_coins` will re-select this same coin and hit the `incoming_tx.sent > 0 and not force` guard, permanently `continue`-skipping it — exactly the same failure-then-permanent-skip pattern as Hubble's `processWithdrawals`.

### Impact Explanation
The affected coin (a clawback merkle coin holding real value) becomes unreachable via the normal wallet flow. The automatic auto-claim scheduler (`wallet_node.py`, invoked every new peak) never passes `force=True`, so it can never recover the coin on its own. The only recovery path is a user manually calling the `spend_clawback_coins` RPC with `force=True` on that specific coin ID — something an ordinary wallet user has no visibility into (the `sent`/pending flag is an internal wallet-DB state, not something surfaced by wallet UX). This matches the report's core impact: silent processing failure combined with no automatic re-processing path leads to funds being effectively locked from the user's perspective.

### Likelihood Explanation
This requires no malicious actor — any transient condition that causes the fee-tandem transaction construction or later steps to fail (e.g., insufficient spendable XCH for the auto-claim fee at that moment, a coin selection race, or any other exception in the unguarded block at lines 264-298) after `increment_sent` has already fired at line 261 is sufficient to trigger the permanent-skip state for that coin. Since this is on the always-running default auto-claim path (when enabled) touching a wallet's own funds, likelihood of at least one occurrence over time is not negligible for active clawback users.

### Recommendation
Move the `increment_sent(... , PENDING, ...)` call to occur only after the full spend bundle (including any fee-tandem transaction) has been successfully constructed and the `TransactionRecord` has been committed to `action_scope.side_effects.transactions` — i.e., only mark a coin "pending" once its spend is guaranteed to actually be attempted/pushed. Additionally, wrap the fee-tandem construction and final `tx_record` assembly in a try/except that rolls back the `sent` state (or resets it) for any coin whose `increment_sent` was already applied if a later step fails, so the coin remains eligible for retry on the next auto-claim cycle instead of being silently and permanently skipped.

### Proof of Concept
1. Enable `auto_claim` in wallet config for a wallet holding a claimable clawback coin.
2. Ensure the wallet's spendable XCH balance is such that `create_tandem_xch_tx` (invoked when `fee > 0`, e.g. via non-zero `auto_claim_tx_fee`) fails partway (e.g., due to a coin-selection race with a concurrent unrelated spend, or insufficient coins for the fee at that instant) — this happens after `increment_sent(..., PENDING, ...)` has already executed for the coin(s) in that batch: [5](#0-4) 
3. The exception propagates out of `auto_claim_coins`/`spend_clawback_coins`; no `TransactionRecord` is ever pushed for that coin.
4. On the next `new_peak_wallet` cycle, `auto_claim_coins` re-selects the same unspent clawback coin, calls `spend_clawback_coins`, and hits `incoming_tx.sent > 0 and not force` → `continue`, permanently skipping the coin: [6](#0-5) 
5. The coin remains stuck indefinitely unless the user manually discovers and invokes `spend_clawback_coins` with `force=True` via RPC.

### Citations

**File:** chia/wallet/wallet_node.py (L1380-1385)
```python
            # Check if any coin needs auto spending
            if self.config.get("auto_claim", {}).get("enabled", False):
                async with self.wallet_state_manager.new_action_scope(
                    self.wallet_state_manager.tx_config, push=True
                ) as action_scope:
                    await self.wallet_state_manager.clawback_manager.auto_claim_coins(action_scope)
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

**File:** chia/wallet/clawback_manager.py (L258-298)
```python
                coin_spend: CoinSpend = generate_clawback_spend_bundle(coin, metadata, inner_puzzle, inner_solution)
                coin_spends.append(coin_spend)
                # Update incoming tx to prevent double spend and mark it is pending
                await self.transaction_store.increment_sent(incoming_tx.name, "", MempoolInclusionStatus.PENDING, None)
            except Exception as e:
                self.log.error(f"Failed to create clawback spend bundle for {coin.name().hex()}: {e}")
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
```
