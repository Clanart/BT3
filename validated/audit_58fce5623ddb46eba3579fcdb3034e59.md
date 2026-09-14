## Title
Batched offer cancellation halts entirely if any single trade's cancellation coin was already consumed by a counterparty, stranding unrelated pending offers - (File: chia/wallet/trade_manager.py)

## Summary
`TradeManager.cancel_pending_offers()` lets a caller cancel several pending offers in one RPC call by chaining their cancellation spends together with coin announcements so they land in one spend bundle. The function assumes every `cancellation_coins` entry returned by `Offer.get_cancellation_coins()` is still unspent and spendable for its full original amount. If one of the batched offers was already taken (or its coin otherwise consumed) by an unprivileged counterparty before the cancel call runs — an event the `TradeManager` has no way to know about at call time — building the signed transaction for that trade fails mid-loop, aborting the whole batched cancellation and leaving the other, unrelated pending offers in the batch un-cancelled and effectively stuck (their coins remain marked selected/pending without ever producing a broadcastable spend).

## Finding Description
`cancel_pending_offers` first computes `cancellation_coins` for every `trade_id` passed in via `Offer.get_cancellation_coins()` [1](#0-0) , then, for `secure=True`, iterates those coins and calls `wallet.generate_signed_transaction(...)` using the coin's on-record amount (`coin.amount`) to compute `amount_to_pay` [2](#0-1) . Crucially, multiple trades passed in the same call are linked together via a rotating chain of `CreateCoinAnnouncement`/`AssertCoinAnnouncement` conditions so that each cancellation coin's spend asserts an announcement created by the previous coin's spend [3](#0-2) , and the resulting per-trade transactions are later aggregated into a single spend bundle [4](#0-3) .

This design assumes the coins backing every offer in the batch are still present and spendable at the recorded amount. In practice, offers are settled by unprivileged counterparties independently of `TradeManager`'s local state — exactly the "liquidation outside the contract's own accounting" pattern in the referenced report. If one offer in a batch cancel request was already accepted/settled by a taker (so its cancellation coin is already spent or no longer selectable by the wallet), `wallet.generate_signed_transaction` for that trade will fail (e.g. unable to select the already-spent coin), raising before the loop can proceed to the remaining trades. Because the loop processes `trade_records`/`all_cancellation_coins` sequentially and only assembles/pushes the final aggregated bundle at the end [5](#0-4) , an exception partway through the batch:
- Aborts cancellation for every subsequent trade_id in the same call, even though those offers were never touched by anyone else.
- Leaves already-processed earlier trades in the batch with coins already appended to `interface.side_effects.selected_coins` [6](#0-5)  and `trade_store.set_status(..., PENDING_CANCEL)` [7](#0-6)  already applied for some entries via `await self.trade_store.set_status(...)` inside the same loop iteration, without a final pushed transaction to reconcile that state, since the aggregation and push happens only after the loop completes fully [8](#0-7) .

This mirrors the reported bug class precisely: an external, unprivileged actor's ordinary action (accepting one of the offers) — analogous to a liquidator repaying/seizing collateral on Compound — invalidates the closing/cancelling function's assumption that the full original amount/coin is still under its control, and because closing several "positions" (offers) is processed as a linked batch, the failure of one disrupts management of the others, exactly as raised in the original finding about mismanagement of unrelated positions.

## Impact Explanation
A wallet user who batches several offer cancellations in one `cancel_offers` RPC call can have the entire operation fail because an unrelated offer in the same batch was independently settled by a counterparty. This can leave otherwise-cancellable offers' coins in a locked/pending selection state without producing the cancellation transaction, preventing the user from reclaiming or reusing those coins through the normal cancel flow and requiring manual recovery (clearing pending transaction records) — the same class of "possible but disruptive/manual" fund recovery called out as the core impact in the original report.

## Likelihood Explanation
This requires no privileged access — any wallet user submitting a batched cancel request, combined with any unprivileged offer counterparty independently taking one of the batched offers at essentially any time before the cancel completes, is sufficient to trigger the condition. Batch cancellation across multiple trades in one call is a supported, user-reachable code path (`cancel_pending_offers` accepts `trade_ids: list[bytes32]`), so the precondition is readily reachable during normal wallet usage, particularly under concurrent offer activity.

## Recommendation
Validate the on-chain state (unspent-ness) of each trade's cancellation coins independently before mutating any shared/batched state, and process/report failures per-trade rather than aborting the whole batch. Consider decoupling the chained-announcement optimization from correctness: either fall back to canceling each offer with an isolated spend bundle when a batch member's coins are found to be already spent, or pre-filter the trade_id list against `check_offer_validity`/current coin state prior to constructing the joint announcement chain, and only roll back/re-mark `PENDING_CANCEL` status for the trades that successfully produced a spend.

## Proof of Concept
Note: I was not able to execute a live scenario in this ask-only session; the following is a code-level reconstruction based on the cited logic, not an observed runtime trace.
1. Maker creates two independent offers, A and B, resulting in two `TradeRecord`s.
2. A counterparty takes/settles offer A independently (spending its cancellation coin), while offer B remains untouched.
3. Maker calls `cancel_pending_offers([A.trade_id, B.trade_id], ..., secure=True)`.
4. Inside the loop over `trade_records`/`all_cancellation_coins` [9](#0-8) , when processing A's already-spent cancellation coin, `wallet.generate_signed_transaction` fails to select/spend that coin and raises.
5. The exception propagates out of `cancel_pending_offers` before B's cancellation transaction is ever generated or the aggregated bundle in lines 400-416 is built/pushed, so B's offer is not cancelled in this call despite never having been touched by anyone else, and any state already mutated for A's status transition remains without a corresponding pushed spend.

### Citations

**File:** chia/wallet/trade_manager.py (L264-291)
```python
        # Need to do some pre-figuring of announcements that will be need to be made
        announcement_nonce: bytes32 = std_hash(b"".join(trade_ids))
        trade_records: list[TradeRecord] = []
        all_cancellation_coins: list[list[Coin]] = []
        announcement_creations: deque[CreateCoinAnnouncement] = deque()
        announcement_assertions: deque[AssertCoinAnnouncement] = deque()
        for trade_id in trade_ids:
            if trade_id in trade_cache:
                trade = trade_cache[trade_id]
            else:
                potential_trade = await self.trade_store.get_trade_record(trade_id)
                if potential_trade is None:
                    self.log.error(f"Cannot find offer {trade_id.hex()}, skip cancellation.")
                    continue
                else:
                    trade = potential_trade

            cancellation_coins = Offer.from_bytes(trade.offer).get_cancellation_coins()
            for coin in cancellation_coins:
                creation = CreateCoinAnnouncement(msg=announcement_nonce, coin_id=coin.name())
                announcement_creations.append(creation)
                announcement_assertions.append(creation.corresponding_assertion())

            trade_records.append(trade)
            all_cancellation_coins.append(cancellation_coins)

        # Make every coin assert the announcement from the one before them
        announcement_assertions.rotate(1)
```

**File:** chia/wallet/trade_manager.py (L293-416)
```python
        all_txs: list[TransactionRecord] = []
        fee_to_pay: uint64 = fee
        for trade, cancellation_coins in zip(trade_records, all_cancellation_coins):
            self.log.info(f"Secure-Cancel pending offer with id trade_id {trade.trade_id.hex()}")

            if not secure:
                action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_cancelled"))
                await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
                continue

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

                cancellation_additions.extend(
                    [
                        add
                        for tx in inner_action_scope.side_effects.transactions
                        if tx.spend_bundle is not None
                        for add in tx.spend_bundle.additions()
                    ]
                )
                all_txs.extend(inner_action_scope.side_effects.transactions)
                fee_to_pay = uint64(0)
                extra_conditions = tuple()

                incoming_tx = TransactionRecord(
                    confirmed_at_height=uint32(0),
                    created_at_time=uint64(time.time()),
                    to_puzzle_hash=new_ph,
                    to_address=self.wallet_state_manager.encode_puzzle_hash(new_ph),
                    amount=uint64(coin.amount),
                    fee_amount=fee,
                    confirmed=False,
                    sent=uint32(10),
                    spend_bundle=None,
                    additions=[],
                    removals=[coin],
                    wallet_id=wallet.id(),
                    sent_to=[],
                    trade_id=None,
                    type=uint32(TransactionType.INCOMING_TX.value),
                    name=cancellation_additions[0].name(),
                    memos={},
                    valid_times=valid_times,
                )
                all_txs.append(incoming_tx)

                # The statuses of trades which offer cancellation coin needs to be set to `PENDING_CANCEL`
                trades_to_cancel.extend(await self.get_trades_by_coin(coin))

            await self.trade_store.set_status(trade.trade_id, TradeStatus.PENDING_CANCEL)
            self.log.info(f"Cancelling trade: {trade.trade_id}")
            for t in trades_to_cancel:
                await self.trade_store.set_status(t.trade_id, TradeStatus.PENDING_CANCEL)
                self.log.info(f"Cancelling trade: {t.trade_id} along with {trade.trade_id}")

        if secure:
            async with action_scope.use() as interface:
                # We have to combine the spend bundle for these since they are tied with announcements
                all_tx_names = [tx.name for tx in all_txs]
                interface.side_effects.transactions = [
                    tx for tx in interface.side_effects.transactions if tx.name not in all_tx_names
                ]
                final_spend_bundle = WalletSpendBundle.aggregate(
                    [tx.spend_bundle for tx in all_txs if tx.spend_bundle is not None]
                )
                interface.side_effects.transactions.append(
                    dataclasses.replace(all_txs[0], spend_bundle=final_spend_bundle, name=final_spend_bundle.name())
                )
                interface.side_effects.transactions.extend(
                    [dataclasses.replace(tx, spend_bundle=None, fee_amount=fee) for tx in all_txs[1:]]
                )

```
