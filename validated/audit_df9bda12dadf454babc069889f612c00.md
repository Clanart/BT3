### Title
Silent Swallowing of CR-CAT Sync Errors Causes Wallet-Side Loss of Received CR-CAT Coins - ([File: chia/wallet/vc_wallet/cr_cat_wallet.py])

### Summary
`CRCATWallet.coin_added` and `CRCATWallet.add_crcat_coin` wrap all coin-processing logic in a bare `except Exception` that only logs at `debug`/`error` level and returns, without re-raising or recording the failure anywhere the wallet can retry or the user can see. Unlike the analogous "involuntary decrease" pattern accepted in the referenced report — where a similar silent-failure path was mitigated by emitting a dedicated event that a caller can observe — this code path has no such compensating control: a coin that fails to parse is simply dropped, permanently, with no retry and no visible signal to the wallet owner. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`coin_added` is invoked by `WalletStateManager` whenever a coin belonging to a CR-CAT wallet is observed on chain (e.g. as the result of an offer settlement, a direct CAT transfer, or a CR-CAT approval flow). It fetches the parent coin's spend and calls `add_crcat_coin`: [4](#0-3) 

`add_crcat_coin` parses the CR-CAT layer from the coin spend, builds a `hint_dict` from computed hints, and looks up `cr_cat` via `next(filter(...))`: [2](#0-1) 

Several of these operations can raise for reasons unrelated to a genuinely malicious/invalid parent (e.g. `hint_dict[coin.name()]` raising `KeyError` when a memo/hint is missing or unusual, `next()` raising `StopIteration` if `CRCAT.get_next_from_coin_spend` doesn't yield a matching coin, or transient failures in `get_timestamp_for_height`/`fetch_coin_spend_for_coin_state`). All of these are caught by the same outer `except Exception` at line 300, which only logs an error and attempts a best-effort cleanup of any already-inserted child records — it never re-raises, and the outer `coin_added` try/except at line 224 catches whatever escapes and logs it at `debug` level only: [5](#0-4) 

This differs materially from how `WalletStateManager.add_coin_state` handles equivalent failures for other coin types: there, an exception is caught at a layer that explicitly re-queues the coin state for retry (via `retry_store.add_state`) when the error is a `PeerRequestException`/`aiosqlite.Error`, and otherwise removes the queued retry state deliberately: [6](#0-5) 

Because `CRCATWallet.coin_added`/`add_crcat_coin` fully absorb the exception internally before it can reach that retry machinery, a CR-CAT coin that fails to parse for any transient or edge-case reason is never recorded as a `WalletCoinRecord` and never retried — the wallet permanently loses track of a coin that legitimately belongs to it and is spendable on-chain.

### Impact Explanation
A CR-CAT (Credential-Restricted CAT, tied to VC/DID authorized-provider gating) coin sent to a wallet — e.g., as an offer settlement payout or a normal CAT transfer — can silently fail to be added to the recipient's local coin store. The wallet's balance will not reflect the coin, and the user has no error, event, or retry mechanism to recover it (only a `debug`-level log line, which is off by default). This is a wallet-state-manager/coin-store correctness bug: the coin exists on-chain and is fully spendable by the owner's keys, but the wallet believes it does not have it, leading to inconsistent local coin-set state, incorrect balance reporting, and potential inability to spend the coin through normal wallet flows until a manual resync. Because the failure path is entered by processing an externally-supplied `coin_spend` (attacker/counterparty-controlled data in an offer or coin transfer), a counterparty in an offer settlement or a coin sender could plausibly construct a valid but atypically-formed CR-CAT coin spend (e.g., omitting the expected memo/hint) to reliably trigger this silent-drop path against a victim wallet.

### Likelihood Explanation
Reaching this code requires only that a coin be created that belongs to a CR-CAT wallet the victim already has (e.g., from accepting/offering a CR-CAT trade, or receiving a CR-CAT transfer) and that the `coin_spend`/hint data trigger one of the exception paths (missing hint entry, no matching `CRCAT` from `get_next_from_coin_spend`, or transient node/RPC issues in fetching timestamp/coin state). This is directly reachable by any wallet user engaging in CR-CAT/offer flows and does not require any privileged or malicious-peer/node position — a counterparty crafting an unusual but valid CR-CAT spend, or ordinary transient errors during sync, are sufficient.

### Recommendation
- Do not swallow generic `Exception` silently in `coin_added`/`add_crcat_coin`. Distinguish between (a) genuinely invalid/non-CAT parent spends (where scrubbing children is correct) and (b) local/transient failures (missing hint, RPC/timeout errors), and for the latter, re-raise or otherwise surface the failure so it reaches `WalletStateManager`'s existing retry (`retry_store`) mechanism instead of being absorbed here.
- Emit an explicit event/log at `WARNING`/`ERROR` level (visible by default) whenever a CR-CAT coin fails to be added, so wallet UIs/users can detect and manually resync/retry, mirroring the mitigation ("emit an event to track involuntary/failed state changes") used for the analogous accepted-risk finding in the referenced report.
- Use `hint_dict.get(coin.name())` with an explicit check/error instead of direct indexing, to avoid an unhandled `KeyError` masquerading as "not a CAT."

### Proof of Concept
1. A counterparty (offer taker/maker or a direct sender) constructs a CR-CAT coin spend that creates a coin intended for the victim's CR-CAT wallet, but with hint data such that `hint_dict[coin.name()]` at line 247/260/294 raises `KeyError` (e.g., by omitting the expected memo/hint for that specific child coin while still creating a puzzle hash matching a pending-approval state check path), or such that `next(filter(...))` at line 235 fails to find the coin among `CRCAT.get_next_from_coin_spend(coin_spend)` results due to an edge case in parsing.
2. The spend is pushed to the mempool and confirmed normally (it is a valid spend from the sender's perspective).
3. The victim's wallet observes the new coin via `WalletStateManager._add_coin_state` → `CRCATWallet.coin_added` (chia/wallet/vc_wallet/cr_cat_wallet.py:215-225), which calls `add_crcat_coin` (lines 227-311).
4. The `KeyError`/`StopIteration` is caught by the inner `except Exception` (line 300), which logs an error and attempts to scrub non-existent child records, then returns normally; the outer `except Exception` in `coin_added` (line 224) additionally would catch anything escaping, logging only at `debug`.
5. No `WalletCoinRecord` is ever inserted for the coin, and no retry state is queued (unlike the `add_coin_state`/`retry_store` path used elsewhere in `wallet_state_manager.py:1586-1594`). The victim's wallet balance never reflects the received coin, though it is spendable on-chain by the victim's keys.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L215-225)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        """Notification from wallet state manager that wallet has been received."""
        self.log.info(f"CR-CAT wallet has been notified that {coin.name().hex()} was added")
        try:
            coin_state = await self.wallet_state_manager.wallet_node.get_coin_state([coin.parent_coin_info], peer=peer)
            coin_spend = await fetch_coin_spend_for_coin_state(coin_state[0], peer)
            await self.add_crcat_coin(coin_spend, coin, height)
        except Exception as e:
            self.log.debug(f"Exception: {e}, traceback: {traceback.format_exc()}")
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L227-235)
```python
    async def add_crcat_coin(self, coin_spend: CoinSpend, coin: Coin, height: uint32) -> None:
        try:
            new_cr_cats: list[CRCAT] = CRCAT.get_next_from_coin_spend(coin_spend)
            hint_dict = {
                id: hc.hint
                for id, hc in compute_spend_hints_and_additions(coin_spend)[0].items()
                if hc.hint is not None
            }
            cr_cat: CRCAT = next(filter(lambda c: c.coin.name() == coin.name(), new_cr_cats))
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L300-311)
```python
        except Exception:
            # The parent is not a CAT which means we need to scrub all of its children from our DB
            self.log.error(f"Cannot add CRCAT coin: {traceback.format_exc()}")
            child_coin_records = await self.wallet_state_manager.coin_store.get_coin_records_by_parent_id(
                coin_spend.coin.name()
            )
            if len(child_coin_records) > 0:
                for record in child_coin_records:
                    if record.wallet_id == self.id():  # pragma: no cover
                        await self.wallet_state_manager.coin_store.delete_coin_record(record.coin.name())
                        # We also need to make sure there's no record of the transaction
                        await self.wallet_state_manager.tx_store.delete_transaction_record(record.coin.name())
```

**File:** chia/wallet/wallet_state_manager.py (L1586-1594)
```python
        except Exception as e:
            self.log.exception(f"Failed to add coin_state: {coin_state}, error: {e}")
            if rollback_wallets is not None:
                self.wallets = rollback_wallets  # Restore since DB will be rolled back by writer
            if isinstance(e, (PeerRequestException, aiosqlite.Error)):
                await self.retry_store.add_state(coin_state, peer.peer_node_id, fork_height)
            else:
                await self.retry_store.remove_state(coin_state)
        return used_up_to
```
