## Finding

The Tokemak report flags emitting success events before the external calls that could still fail, creating misleading state. The closest analog in this Chia codebase is in the wallet's offer-cancellation path, where a "cancelled" notification is dispatched before the local cancellation state is actually persisted.

### Title
Wallet dispatches "offer_cancelled" websocket event before the trade status is actually persisted, allowing a misleading notification if persistence fails - (File: chia/wallet/trade_manager.py)

### Summary
In `TradeManager.cancel_pending_offers`, the non-secure (local-only) cancellation branch dispatches the `offer_cancelled` websocket event *before* calling `self.trade_store.set_status(...)` to actually mark the trade as cancelled in the database.

### Finding Description [1](#0-0) 

```python
if not secure:
    action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_cancelled"))
    await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
    continue
```

`dispatch_websocket_event` immediately invokes `WalletStateManager._dispatch_websocket_event`, which synchronously notifies any registered UI/RPC state-changed callback: [2](#0-1) [3](#0-2) 

This is the opposite ordering used elsewhere in the same file, e.g. `save_trade` only dispatches `offer_added` after the DB write succeeds: [4](#0-3) 

If `await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)` raises (DB error, disk failure, or any exception), the wallet UI/RPC subscriber has already been told the offer is cancelled, while `TradeStore` still reflects the trade's prior status (e.g. `PENDING_ACCEPT`). This is the same root-cause pattern in the referenced report: notifying observers of a completed action before the operation that performs it has actually succeeded.

### Impact Explanation
Because the underlying persistence write can fail independently of the event dispatch, a caller (wallet GUI, another wallet-RPC client, or automation relying on the `offer_cancelled` websocket event) can incorrectly believe the offer is void while it remains active in `TradeStore` with status `PENDING_ACCEPT`/`PENDING_CONFIRM`. Since a non-secure cancellation never invalidates the offer on-chain (no cancellation spend is submitted for this branch), the original offer's coins remain spendable by whichever party presents the offer first, so this does not itself enable double-spending, but it can drive a user to take actions (e.g., re-offering the same coins) based on a false belief that the previous offer no longer exists, creating a race/confusion between the "cancelled" offer and any subsequent offer to trade the same coins.

### Likelihood Explanation
This is reachable by any wallet user calling the local-cancel (`secure=False`) trade-cancellation path via the wallet RPC, with likelihood tied to any transient store/DB failure occurring on that specific `set_status` call, which is not otherwise unusual (I/O errors, disk pressure, concurrent writer contention).

### Recommendation
Move the `dispatch_websocket_event` call in `TradeManager.cancel_pending_offers` (and audit `fail_pending_offer`'s analogous pattern) to occur only after `self.trade_store.set_status(...)` completes successfully, matching the ordering already used in `save_trade`.

### Proof of Concept
1. Call the wallet-RPC `cancel_offers` with `secure=False` for a pending trade.
2. Inject/force a failure in `TradeStore.set_status` (e.g. simulate a DB write error) immediately after the websocket event is dispatched but before the DB commit.
3. Observe: the `offer_cancelled` websocket event has already fired to subscribers, but `TradeStore.get_trade_record` still reports the trade with its previous (non-cancelled) status — the notification does not match persisted state. [5](#0-4)

### Citations

**File:** chia/wallet/trade_manager.py (L248-251)
```python
    async def fail_pending_offer(self, trade_id: bytes32, sync_scope: WalletSyncScope) -> None:
        await self.trade_store.set_status(trade_id, TradeStatus.FAILED)
        async with sync_scope.use() as interface:
            interface.side_effects.websocket_events.append(WebSocketEvent(name="offer_failed"))
```

**File:** chia/wallet/trade_manager.py (L296-301)
```python
            self.log.info(f"Secure-Cancel pending offer with id trade_id {trade.trade_id.hex()}")

            if not secure:
                action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_cancelled"))
                await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
                continue
```

**File:** chia/wallet/trade_manager.py (L417-429)
```python
    async def save_trade(self, trade: TradeRecord, offer: Offer, action_scope: WalletActionScope) -> None:
        offer_name: bytes32 = offer.name()
        await self.trade_store.add_trade_record(trade, offer_name)

        # We want to subscribe to the coin IDs of all coins that are not the ephemeral offer coins
        offered_coins: set[Coin] = {value for values in offer.get_offered_coins().values() for value in values}
        non_offer_additions: set[Coin] = set(offer.additions()) ^ offered_coins
        non_offer_removals: set[Coin] = set(offer.removals()) ^ offered_coins
        await self.wallet_state_manager.add_interested_coin_ids(
            [coin.name() for coin in (*non_offer_removals, *non_offer_additions)]
        )

        action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_added"))
```

**File:** chia/wallet/wallet_action_scope.py (L159-161)
```python
    # TODO: this should be part of the side effects like everything else
    def dispatch_websocket_event(self, wallet_state_manager: WalletStateManager, event: WebSocketEvent) -> None:
        wallet_state_manager._dispatch_websocket_event(event)
```

**File:** chia/wallet/wallet_state_manager.py (L716-723)
```python
    def _dispatch_websocket_event(self, event: WebSocketEvent) -> None:
        if self.state_changed_callback is not None:
            change_data: dict[str, Any] = {"state": event.name}
            if event.wallet_id is not None:
                change_data["wallet_id"] = event.wallet_id
            if event.data is not None:
                change_data["additional_data"] = event.data
            self.state_changed_callback(event.name, change_data)
```
