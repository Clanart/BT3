## Analysis

The reported bug class — "commit a cancel, but a rival transaction with a higher fee/priority gets mined first, forcing the unwanted state transition through" — maps directly onto Chia's **offer cancellation** flow.

### Title
Offer cancellation can be front-run by a competing take-offer spend with a higher mempool fee - (File: `chia/wallet/trade_manager.py`)

### Summary
When a maker wants to withdraw an offer they previously published (e.g., because they no longer trust the counterparty or the price moved), they call `TradeManager.cancel_pending_offers` with `secure=True`. This builds a *new* spend of the still-unspent offered coin(s) to a fresh puzzle hash, which is only effective once it is mined. Until that cancellation spend confirms, anyone holding the original offer file can submit a competing spend bundle that "takes" the offer, consuming the same coin(s) via the settlement puzzle. Because both transactions spend the identical coin, this is a mempool-level double-spend race governed by `can_replace()`/replace-by-fee rules, so whichever spend reaches the mempool/block with sufficient fee wins — exactly analogous to the front-run window between `cancel_recovery` and `finish_recovery` described in the report.

### Finding Description
`cancel_pending_offers` gathers the coins from `Offer.get_cancellation_coins()` [1](#0-0)  and, for each one, generates a **new spend** of that same coin to a new address via `wallet.generate_signed_transaction(...)`, only marking the trade `PENDING_CANCEL` locally [2](#0-1) . The actual cancellation only becomes binding once this spend bundle is confirmed on-chain [3](#0-2) .

Meanwhile, the offer coin is still spendable by anyone holding the offer file, because a Chia offer is simply an unsigned/partially-signed spend bundle attached to specific coins — nothing on-chain marks it as "cancelled" until a spend actually consumes the coin. If a counterparty broadcasts a take-offer spend bundle for the same coin with a competitive or higher fee, the mempool's replacement logic in `can_replace()` [4](#0-3)  — which allows any conflicting spend of the same coin to replace an existing mempool entry provided it pays a sufficiently higher fee per cost and fee increase — permits the take-offer bundle to displace or race ahead of the maker's not-yet-confirmed cancellation spend.

This mirrors the reported pattern precisely: the "cancel" and the "finalizing" action both operate on the same identity/coin, and whichever transaction is mined first (influenced by fee) determines the outcome, regardless of the owner's/maker's most recent intent.

### Impact Explanation
If the maker's intent in cancelling was to prevent an already-untrusted or stale counterparty from completing a trade (e.g., they've decided the asset is worth more, or no longer trust the counterparty), a race won by the taker completes the offer settlement against the maker's wishes — resulting in the maker's offered asset being transferred/settled with a counterparty they explicitly tried to block. This is "offer settlement theft" from the maker's perspective: assets move despite an on-time cancellation attempt.

### Likelihood Explanation
Any offer taker who is monitoring the mempool or is simply faster/pays a higher fee can exploit this whenever a maker attempts to cancel an offer that is still economically favorable to the counterparty. Because Chia offers remain valid and spendable until the underlying coin is actually consumed, this race is trivially reachable by any wallet user or offer counterparty with no special privileges — likelihood is comparable to a standard replace-by-fee race, i.e., moderate-to-high whenever cancellation happens after the taker has already prepared to accept.

### Recommendation
This is consistent with Chia's documented offer semantics (an offer is valid until spent, and cancellation is just another competing spend), similar to the "intentional functionality" note in the original report for DID recovery cancellation. The same posture should be explicitly documented for offer cancellation: wallets and UIs should warn users that `cancel_offer` provides no on-chain "veto" until the cancellation transaction confirms, and that submitting the cancellation with a high fee (and/or using `cancel_offers`' fee parameter aggressively) is the mitigant, not a fix. No consensus-level fix is required since coin-spend semantics for Chia intentionally allow first-confirmed-spend-wins for a given coin.

### Proof of Concept
1. Maker publishes an offer file for coin `C`.
2. Maker decides to cancel: calls `cancel_offer`/`cancel_pending_offers`, which builds and broadcasts spend `S_cancel(C -> maker_new_ph)` with fee `f1` [3](#0-2) .
3. Before `S_cancel` is included in a block, a taker (who already has the offer file) submits `S_take(C -> settlement)` with fee `f2 > f1`.
4. Per `can_replace()`, if both spends target coin `C` and are seen as conflicting in the mempool, the higher-fee spend wins the race and gets mined [5](#0-4) ; the offer executes despite the maker's cancellation attempt.

### Citations

**File:** chia/wallet/trading/offer.py (L425-470)
```python
    def get_cancellation_coins(self) -> list[Coin]:
        # First, we're going to gather:
        dependencies: dict[bytes32, list[bytes32]] = {}  # all of the hashes that each coin depends on
        announcements: dict[bytes32, list[bytes32]] = {}  # all of the hashes of the announcement that each coin makes
        coin_names: list[bytes32] = []  # The names of all the coins
        additions = self.additions()
        for spend in [cs for cs in self._bundle.coin_spends if cs.coin not in additions]:
            name = bytes32(spend.coin.name())
            coin_names.append(name)
            dependencies[name] = []
            announcements[name] = []
            conditions: Program = run_with_cost(spend.puzzle_reveal, INFINITE_COST, spend.solution)[1]
            for condition in conditions.as_iter():
                if condition.first() == 60:  # create coin announcement
                    announcements[name].append(
                        AssertCoinAnnouncement(asserted_id=name, asserted_msg=condition.at("rf").as_python()).msg_calc
                    )
                elif condition.first() == 61:  # assert coin announcement
                    dependencies[name].append(bytes32(condition.at("rf").as_python()))

        # We now enter a loop that is attempting to express the following logic:
        # "If I am depending on another coin in the same bundle, you may as well cancel that coin instead of me"
        # By the end of the loop, we should have filtered down the list of coin_names to include only those that will
        # cancel everything else
        while True:
            removed = detect_dependent_coin(coin_names, dependencies, announcements)
            if removed is None:
                break
            removed_coin, provider = removed
            removed_announcements: list[bytes32] = announcements[removed_coin]
            remove_these_keys: list[bytes32] = [removed_coin]
            while True:
                for coin, deps in dependencies.items():
                    if set(deps) & set(removed_announcements) and coin != provider:
                        remove_these_keys.append(coin)
                removed_announcements = []
                for coin in remove_these_keys:
                    dependencies.pop(coin)
                    removed_announcements.extend(announcements.pop(coin))
                coin_names = [n for n in coin_names if n not in remove_these_keys]
                if removed_announcements == []:
                    break
                else:
                    remove_these_keys = []

        return [cs.coin for cs in self._bundle.coin_spends if cs.coin.name() in coin_names]
```

**File:** chia/wallet/trade_manager.py (L253-301)
```python
    async def cancel_pending_offers(
        self,
        trade_ids: list[bytes32],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        secure: bool = True,  # Cancel with a transaction on chain
        trade_cache: dict[bytes32, TradeRecord] = {},  # Optional pre-fetched trade records for optimization
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        """This will create a transaction that includes coins that were offered"""

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

        all_txs: list[TransactionRecord] = []
        fee_to_pay: uint64 = fee
        for trade, cancellation_coins in zip(trade_records, all_cancellation_coins):
            self.log.info(f"Secure-Cancel pending offer with id trade_id {trade.trade_id.hex()}")

            if not secure:
                action_scope.dispatch_websocket_event(self.wallet_state_manager, WebSocketEvent(name="offer_cancelled"))
                await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
                continue
```

**File:** chia/wallet/trade_manager.py (L342-356)
```python
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

**File:** chia/full_node/mempool_manager.py (L1168-1223)
```python
def can_replace(conflicting_items: list[MempoolItem], new_item: MempoolItem) -> bool:
    """
    This function implements the mempool replacement rules. Given a Mempool item
    we're attempting to insert into the mempool (new_item) and the set of existing
    mempool items that conflict with it, this function answers the question whether
    the existing items can be replaced by the new one.
    """

    conflicting_fees = 0
    conflicting_cost = 0
    assert_height: uint32 | None = None
    assert_before_height: uint32 | None = None
    assert_before_seconds: uint64 | None = None
    # we don't allow replacing mempool items with new ones that remove
    # eligibility for dedup and fast-forward. Doing so could be abused by
    # denying such spends from operating as intended
    # collect all coins that are eligible for dedup and FF in the existing items
    existing_ff_spends: set[bytes32] = set()
    existing_dedup_spends: set[bytes32] = set()

    for item in conflicting_items:
        conflicting_fees += item.fee
        conflicting_cost += item.cost

        # All coins spent in all conflicting items must also be spent in the new item. (superset rule). This is
        # important because otherwise there exists an attack. A user spends coin A. An attacker replaces the
        # bundle with AB with a higher fee. An attacker then replaces the bundle with just B with a higher
        # fee than AB therefore kicking out A altogether. The better way to solve this would be to keep a cache
        # of booted transactions like A, and retry them after they get removed from mempool due to a conflict.
        for coin_id, bcs in item.bundle_coin_spends.items():
            if coin_id not in new_item.bundle_coin_spends:
                log.debug("Rejecting conflicting tx as it does not spend conflicting coin %s", coin_id)
                return False
            if bcs.supports_fast_forward:
                existing_ff_spends.add(bytes32(coin_id))
            if bcs.eligible_for_dedup:
                existing_dedup_spends.add(bytes32(coin_id))

        assert_height = optional_max(assert_height, item.assert_height)
        assert_before_height = optional_min(assert_before_height, item.assert_before_height)
        assert_before_seconds = optional_min(assert_before_seconds, item.assert_before_seconds)

    # New item must have higher fee per cost
    conflicting_fees_per_cost = conflicting_fees / conflicting_cost
    if new_item.fee_per_cost <= conflicting_fees_per_cost:
        log.debug(
            f"Rejecting conflicting tx due to not increasing fees per cost "
            f"({new_item.fee_per_cost} <= {conflicting_fees_per_cost})"
        )
        return False

    # New item must increase the total fee at least by a certain amount
    fee_increase = new_item.fee - conflicting_fees
    if fee_increase < MEMPOOL_MIN_FEE_INCREASE:
        log.debug(f"Rejecting conflicting tx due to low fee increase ({fee_increase})")
        return False
```
