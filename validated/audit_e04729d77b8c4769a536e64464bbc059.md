## Analysis: Insecure Offer Cancellation Leaves the Underlying Offer Fully Spendable

### Title
Insecure Trade Cancellation Marks an Offer "CANCELLED" Locally Without Invalidating the On-Chain Coins - (File: chia/wallet/trade_manager.py)

### Summary
`TradeManager.cancel_pending_offers()` supports a `secure` flag. When `secure=False` ("insecure" cancel), the wallet immediately flips the trade's local status to `CANCELLED` and dispatches an `offer_cancelled` websocket event, but it never creates or broadcasts a spend bundle to actually consume the coins that back the offer.

### Finding Description
In the `secure=False` branch, cancellation is purely a local bookkeeping update: [1](#0-0) 

No coins are selected, no spend bundle is built, and nothing is pushed to the mempool in this branch — contrast with the `secure=True` branch a few lines below, which explicitly selects the offer's cancellation coins and calls `wallet.generate_signed_transaction(...)` to actually re-spend them [2](#0-1) .

This means the coins referenced by the original `Offer` (the `TradingOffer`/spend bundle that was handed to a counterparty) remain completely unspent and valid on-chain. The maker's wallet UI/RPC now reports the trade as `CANCELLED`, but that status has no cryptographic or consensus effect — it is purely a row update in `trade_store`. This is functionally the same bug class as CVE-2021-27351: a "terminate/cancel" action that updates the *local* view of state ("session terminated" / "offer cancelled") while the actual live artifact (the session / the signed coin spend) is never invalidated and remains fully usable.

This is confirmed by test behavior: after an insecure cancel, `get_offer` reports `TradeStatus.CANCELLED` for the exact same `offer` bytes that were originally created [3](#0-2) , and a subsequent attempt at a real (`secure=True`) cancel then produces `PENDING_CANCEL` — showing the insecure path did nothing to the chain state.

### Impact Explanation
Any counterparty that already holds the offer file (which is the entire purpose of the offer protocol — offers are shared out-of-band before being taken) can still submit it to a full node and have it accepted, because the maker's coins were never re-spent. A maker who insecurely cancels an offer and believes (based on wallet status) that their coins are safe/free to reuse elsewhere is exposed to a race: if they also spend the same coins elsewhere while the stale offer is still outstanding, or if the counterparty simply takes the "cancelled" offer, unauthorized/unintended coin movement occurs from the maker's perspective, since their coins move according to a spend bundle that the wallet UI told them was void.

### Likelihood Explanation
This path is reachable purely through a normal wallet RPC/CLI call (`cancel_offer`/`cancel_offers` with `secure=False`, exposed as the "insecure" cancel option), requiring no special privilege beyond the ability to create/cancel one's own offer. The precondition — the maker sharing the offer file with a counterparty and later insecurely cancelling it — is the standard, documented offer-exchange workflow, making this readily triggerable, not a contrived edge case.

### Recommendation
Either remove/deprecate the insecure cancellation path, or ensure the reported trade status distinguishes "locally hidden, but potentially still on-chain valid" from a true `CANCELLED` state, and surface a strong warning to RPC/CLI callers that insecure cancellation does not invalidate the offer coins and that the offer remains executable by anyone still holding the offer file.

### Proof of Concept
1. Maker creates an offer via `create_offer_for_ids` and shares the resulting `Offer` bytes with a counterparty.
2. Maker calls `cancel_offer`/`cancel_pending_offers` with `secure=False` (`insecure=True` in `CancelOfferCMD`). The trade status becomes `CANCELLED` in the maker's wallet [4](#0-3) .
3. Counterparty (who still holds the original offer bytes) calls `take_offer`/`respond_to_offer` on their own wallet using the untouched offer — since the underlying coins were never re-spent, the take succeeds and the coins move, despite the maker's wallet reporting the offer as cancelled.

Note: I could not verify the exact CLI help text warning language for the `insecure`/`--insecure` flag in `chia/cmds/wallet.py` within the available index; a Devin session with full repository access would be needed to confirm whether/how strongly this risk is currently disclosed to users in command help text.

### Citations

**File:** chia/wallet/trade_manager.py (L295-301)
```python
        for trade, cancellation_coins in zip(trade_records, all_cancellation_coins):
            self.log.info(f"Secure-Cancel pending offer with id trade_id {trade.trade_id.hex()}")

            if not secure:
                action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_cancelled"))
                await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
                continue
```

**File:** chia/wallet/trade_manager.py (L303-355)
```python
            cancellation_additions: list[Coin] = []
            valid_times: ConditionValidTimes = parse_timelock_info(extra_conditions)
            trades_to_cancel: list[TradeRecord] = []
            for coin in cancellation_coins:
                wallet = await self.wallet_state_manager.get_wallet_for_coin(coin.name())

                if wallet is None:
                    self.log.error(f"Cannot find wallet for offer {trade.trade_id}, skip cancellation.")
                    continue

                new_ph = await action_scope.get_puzzle_hash(self.wallet_state_manager)

                if len(trade_records) > 1 or len(cancellation_coins) > 1:
                    announcement_conditions: tuple[Condition, ...] = (
                        announcement_creations.popleft(),
                        announcement_assertions.popleft(),
                    )
                else:
                    announcement_conditions = tuple()
                async with action_scope.use() as interface:
                    interface.side_effects.selected_coins.append(coin)
                # This should probably not switch on whether or not we're spending a XCH but it has to for now
                if wallet.type() == WalletType.STANDARD_WALLET:
                    assert isinstance(wallet, Wallet)
                    if fee_to_pay > coin.amount:
                        selected_coins: set[Coin] = await wallet.select_coins(
                            uint64(fee_to_pay - coin.amount),
                            action_scope,
                        )
                    else:
                        selected_coins = set()
                    selected_coins.add(coin)
                    amount_to_pay = uint64(sum(c.amount for c in selected_coins) - fee_to_pay)
                else:
                    selected_coins = {coin}
                    amount_to_pay = coin.amount

                # ATTENTION: new_wallets
                assert isinstance(wallet, (Wallet, CATWallet, DataLayerWallet, NFTWallet))
                async with self.wallet_state_manager.new_action_scope(
                    action_scope.config.tx_config.override(
                        excluded_coin_ids=[],
                    ),
                    push=False,
                ) as inner_action_scope:
                    await wallet.generate_signed_transaction(
                        [amount_to_pay],
                        [new_ph],
                        inner_action_scope,
                        fee=fee_to_pay,
                        coins=selected_coins,
                        extra_conditions=(*extra_conditions, *announcement_conditions),
                    )
```

**File:** chia/_tests/wallet/rpc/test_wallet_rpc.py (L1824-1836)
```python
    await CancelOfferCMD(
        **{
            **wallet_environments.cmd_tx_endpoint_args(env_1),
            "offer_id": offer.name(),
            "insecure": True,
            "fee": uint64(0),
            "push": True,
        }
    ).run()

    trade_record = (await env_1.rpc_client.get_offer(GetOffer(trade_id=offer.name(), file_contents=True))).trade_record
    assert trade_record.offer == bytes(offer)
    assert TradeStatus(trade_record.status) == TradeStatus.CANCELLED
```
