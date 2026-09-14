Confirmed: `Offer.aggregate` at [1](#0-0)  combines the underlying `WalletSpendBundle` of every offer that's merged into a single settlement transaction, and `wallet_state_manager.add_pending_transactions` similarly aggregates multiple `TransactionRecord`s' spend bundles into one bundle before pushing to the mempool at [2](#0-1) . This is the analogous "batch settlement" surface.

### Title
Aggregated offer settlement bundles allow a single spent/invalid coin to grief and revert an entire batch of unrelated trades - (File: chia/wallet/trading/offer.py)

### Summary
`Offer.aggregate()` merges the `WalletSpendBundle`s of multiple independent maker/taker offers into a single combined `SpendBundle` with one aggregated BLS signature, and `to_valid_spend()` builds one atomic settlement transaction for all the merged offers at once. When this combined bundle is submitted, the mempool/consensus layer treats it as a single unit: if any single coin among the (potentially many, unrelated) aggregated offers is already spent or otherwise invalid, the *entire* combined spend bundle is rejected with `DOUBLE_SPEND`/`INVALID_SPEND_BUNDLE`, causing every legitimate trade bundled alongside it to fail as well.

### Finding Description
`Offer.aggregate` at [1](#0-0)  takes a `list[Offer]` and folds all of their `requested_payments`, `driver_dict`, and — critically — their underlying `WalletSpendBundle`s into one combined `Offer` via `WalletSpendBundle.aggregate([total_bundle, offer._bundle])`. `to_valid_spend()` then produces one single `WalletSpendBundle` covering every offer's settlement coins at [3](#0-2) , and `respond_to_offer` in `TradeManager` pushes this single combined bundle as `final_spend_bundle` for the entire batch of trades at [4](#0-3) .

This exact aggregation flow is exercised end-to-end in `test_aggregated_trade_state`, where two independently-created offers (`trade_make_1` and `trade_make_2`) are combined with `Offer.aggregate([offer_1, offer_2])` and submitted as a single bundle via `respond_to_offer` at [5](#0-4) , and in `test_complex_offer`, which aggregates up to 3-4 independent offers into `new_offer` at [6](#0-5) .

Separately, `WalletStateManager.add_pending_transactions` performs the same kind of batching for ordinary transactions: it aggregates the spend bundles of an entire list of `tx_records` (potentially covering multiple wallets/multiple logically-independent sends) into one `agg_spend` and pushes a single merged bundle at [7](#0-6) .

Because CLVM/mempool validation of a `SpendBundle` is all-or-nothing (a single conflicting/invalid coin spend anywhere in the bundle causes `check_removals`/`validate_spend_bundle` in `mempool_manager.py` to reject the whole bundle, as demonstrated by `test_double_spend_same_bundle` at [8](#0-7) ), any single coin among many aggregated, unrelated offers/transactions being spent, double-spent, or otherwise made invalid causes the entire combined settlement to fail. There is no "NoThrow"/partial-success mode for offer or transaction aggregation analogous to what exists for block building batches (`create_block_generator2`'s per-batch commit/rollback in `mempool.py`), and no mechanism to split the aggregated bundle back into independently-submittable pieces once merged.

### Impact Explanation
A malicious counterparty (or any third party who is aware that several maker offers are about to be aggregated and taken together, e.g. via a dexie-style aggregator or `add_pending_transactions(merge_spends=True)` batching multiple sends) can front-run the aggregate settlement by spending or double-spending one of the coins used in any single offer within the aggregate (their own offered coin, or a coin they control that's referenced by an announcement/fee coin in one of the bundled transactions). Because `Offer.aggregate`/`WalletSpendBundle.aggregate` and `add_pending_transactions` fuse everything into one atomic `SpendBundle`, this single invalidated coin causes the *entire* multi-trade settlement to be rejected by the mempool, forcing every other maker/taker whose otherwise-valid trade was bundled in to have their transaction fail and need to be resubmitted (as separate, non-aggregated bundles). This is exchange/trade griefing: delays, wasted signing/build effort, and for time-sensitive offers, lost opportunity or the need to re-negotiate, all triggered by an unprivileged party's ordinary coin spend.

### Likelihood Explanation
Aggregation of offers (`Offer.aggregate`) is a documented, actively-used code path for combining multiple trades (e.g. `test_aggregated_trade_state`, `test_complex_offer`), and any offer's underlying coins remain spendable by their owners at all times until settlement is confirmed on chain. An attacker only needs to be a party (or collude with a party) to one of the offers being aggregated, and race a normal spend of one of their own committed coins against the aggregate settlement's confirmation — a straightforward, unprivileged action requiring no special access.

### Recommendation
- **Short term:** When merging multiple independent offers/transactions for batch settlement, validate that all underlying coins of every constituent offer are currently unspent and mempool-eligible immediately before final aggregation and submission, and prefer submitting large aggregates as separable partitions (or fall back to submitting offers individually) if any partition fails so that a single bad coin doesn't invalidate unrelated trades.
- **Long term:** Provide a "best effort"/partial-acceptance mode for aggregated offer settlement (mirroring the batch commit/rollback pattern already used in block building, `create_block_generator2`), so that when a combined bundle is rejected, the wallet/trade manager can automatically retry with the offending offer excluded rather than requiring manual resubmission of the whole batch.

### Proof of Concept
1. Maker A creates offer 1 (offering coin C1) and Maker B creates offer 2 (offering coin C2), both requesting XCH from a Taker, following the flow in `test_aggregated_trade_state` at [9](#0-8) .
2. Taker calls `Offer.aggregate([offer_1, offer_2])` and `respond_to_offer(agg_offer, ...)`, producing one combined `final_spend_bundle` covering both C1 and C2 (`trade_manager.py:889-915`).
3. Before the Taker's aggregate bundle is farmed, Maker B (malicious or colluding) spends C2 in a separate, non-offer transaction (e.g. sending it to themselves), which enters the mempool first.
4. When the Taker's aggregate bundle reaches the mempool, `check_removals` in `mempool_manager.py` finds C2 already spent and returns `DOUBLE_SPEND` for the *entire* combined bundle, per the mechanics shown in `test_double_spend_same_bundle` (`test_mempool.py:1886-1896`).
5. Maker A's otherwise fully valid, independent trade (offer 1) fails along with Maker B's, and must be resubmitted as a standalone (non-aggregated) offer — demonstrating the griefing of an unrelated party via batch settlement.

### Citations

**File:** chia/wallet/trading/offer.py (L472-498)
```python
    @classmethod
    def aggregate(cls, offers: list[Offer]) -> Offer:
        total_requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        total_bundle = WalletSpendBundle([], G2Element())
        total_driver_dict: dict[bytes32, PuzzleInfo] = {}
        for offer in offers:
            # First check for any overlap in inputs
            total_inputs: set[Coin] = {cs.coin for cs in total_bundle.coin_spends}
            offer_inputs: set[Coin] = {cs.coin for cs in offer._bundle.coin_spends}
            if total_inputs & offer_inputs:
                raise ValueError("The aggregated offers overlap inputs")

            # Next, do the aggregation
            for asset_id, payments in offer.requested_payments.items():
                if asset_id in total_requested_payments:
                    total_requested_payments[asset_id].extend(payments)
                else:
                    total_requested_payments[asset_id] = payments

            for key, value in offer.driver_dict.items():
                if key in total_driver_dict and total_driver_dict[key] != value:
                    raise ValueError(f"The offers to aggregate disagree on the drivers for {key.hex()}")

            total_bundle = WalletSpendBundle.aggregate([total_bundle, offer._bundle])
            total_driver_dict.update(offer.driver_dict)

        return cls(total_requested_payments, total_bundle, total_driver_dict)
```

**File:** chia/wallet/trading/offer.py (L506-595)
```python
    def to_valid_spend(self, arbitrage_ph: bytes32 | None = None, solver: Solver = Solver({})) -> WalletSpendBundle:
        if not self.is_valid():
            raise ValueError("Offer is currently incomplete")

        completion_spends: list[CoinSpend] = []
        all_offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        total_arbitrage_amount: dict[bytes32 | None, int] = self.arbitrage()
        for asset_id, payments in self.requested_payments.items():
            offered_coins: list[Coin] = all_offered_coins[asset_id]

            # Because of CAT supply laws, we must specify a place for the leftovers to go
            arbitrage_amount: int = total_arbitrage_amount[asset_id]
            all_payments: list[NotarizedPayment] = payments.copy()
            if arbitrage_amount > 0:
                assert arbitrage_amount is not None
                assert arbitrage_ph is not None
                all_payments.append(NotarizedPayment(arbitrage_ph, uint64(arbitrage_amount)))

            # Some assets need to know about siblings so we need to collect all spends first to be able to use them
            coin_to_spend_dict: dict[Coin, CoinSpend] = {}
            coin_to_solution_dict: dict[Coin, Program] = {}
            for coin in offered_coins:
                parent_spend: CoinSpend = next(
                    filter(lambda cs: cs.coin.name() == coin.parent_coin_info, self._bundle.coin_spends)
                )
                coin_to_spend_dict[coin] = parent_spend

                inner_solutions = []
                if coin == offered_coins[0]:
                    nonces: list[bytes32] = [p.nonce for p in all_payments]
                    for nonce in list(dict.fromkeys(nonces)):  # dedup without messing with order
                        nonce_payments: list[NotarizedPayment] = list(filter(lambda p: p.nonce == nonce, all_payments))
                        inner_solutions.append((nonce, [np.as_condition_args() for np in nonce_payments]))
                coin_to_solution_dict[coin] = Program.to(inner_solutions)

            for coin in offered_coins:
                if asset_id:
                    siblings: str = "("
                    sibling_spends: str = "("
                    sibling_puzzles: str = "("
                    sibling_solutions: str = "("
                    disassembled_offer_mod: str = disassemble(OFFER_MOD)
                    for sibling_coin in offered_coins:
                        if sibling_coin != coin:
                            siblings += (
                                "0x"
                                + sibling_coin.parent_coin_info.hex()
                                + sibling_coin.puzzle_hash.hex()
                                + uint64(sibling_coin.amount).stream_to_bytes().hex()
                                + " "
                            )
                            sibling_spends += "0x" + bytes(coin_to_spend_dict[sibling_coin]).hex() + " "
                            sibling_puzzles += disassembled_offer_mod + " "
                            sibling_solutions += disassemble(coin_to_solution_dict[sibling_coin]) + " "
                    siblings += ")"
                    sibling_spends += ")"
                    sibling_puzzles += ")"
                    sibling_solutions += ")"

                    solution: Program = solve_puzzle(
                        self.driver_dict[asset_id],
                        Solver(
                            {
                                "coin": "0x"
                                + coin.parent_coin_info.hex()
                                + coin.puzzle_hash.hex()
                                + uint64(coin.amount).stream_to_bytes().hex(),
                                "parent_spend": "0x" + bytes(coin_to_spend_dict[coin]).hex(),
                                "siblings": siblings,
                                "sibling_spends": sibling_spends,
                                "sibling_puzzles": sibling_puzzles,
                                "sibling_solutions": sibling_solutions,
                                **solver.info,
                            }
                        ),
                        OFFER_MOD,
                        Program.to(coin_to_solution_dict[coin]),
                    )
                else:
                    solution = Program.to(coin_to_solution_dict[coin])

                completion_spends.append(
                    make_spend(
                        coin,
                        construct_puzzle(self.driver_dict[asset_id], OFFER_MOD) if asset_id else OFFER_MOD,
                        solution,
                    )
                )

        return WalletSpendBundle.aggregate([WalletSpendBundle(completion_spends, G2Element()), self._bundle])
```

**File:** chia/wallet/wallet_state_manager.py (L1815-1827)
```python
        agg_spend = WalletSpendBundle.aggregate([tx.spend_bundle for tx in tx_records if tx.spend_bundle is not None])
        if extra_spends is not None:
            agg_spend = WalletSpendBundle.aggregate([agg_spend, *extra_spends])
        actual_spend_involved: bool = agg_spend != WalletSpendBundle([], G2Element())
        if merge_spends and actual_spend_involved:
            tx_records = [
                dataclasses.replace(
                    tx,
                    spend_bundle=agg_spend if i == 0 else None,
                    name=agg_spend.name() if i == 0 else bytes32.secret(),
                )
                for i, tx in enumerate(tx_records)
            ]
```

**File:** chia/wallet/trade_manager.py (L887-915)
```python
        self.log.info("COMPLETE OFFER: %s", complete_offer.to_bech32())
        assert complete_offer.is_valid()
        final_spend_bundle: WalletSpendBundle = complete_offer.to_valid_spend(
            solver=Solver({**valid_spend_solver.info, **solver.info})
        )
        await self.maybe_create_wallets_for_offer(complete_offer)

        tx_records: list[TransactionRecord] = await self.calculate_tx_records_for_offer(complete_offer, True)

        trade_record: TradeRecord = TradeRecord(
            confirmed_at_index=uint32(0),
            accepted_at_time=uint64(time.time()),
            created_at_time=uint64(time.time()),
            is_my_offer=False,
            sent=uint32(0),
            offer=bytes(complete_offer),
            taken_offer=bytes(offer),
            coins_of_interest=complete_offer.get_involved_coins(),
            trade_id=complete_offer.name(),
            status=uint32(TradeStatus.PENDING_CONFIRM.value),
            sent_to=[],
            valid_times=parse_timelock_info(extra_conditions),
        )

        await self.save_trade(trade_record, offer, action_scope)

        async with action_scope.use() as interface:
            interface.side_effects.transactions.extend(tx_records)
            interface.side_effects.extra_spends.append(final_spend_bundle)
```

**File:** chia/_tests/wallet/cat_wallet/test_trades.py (L2560-2595)
```python
    async with trade_manager_maker.wallet_state_manager.new_action_scope(
        wallet_environments.tx_config, push=False
    ) as action_scope:
        success, trade_make_1, error = await trade_manager_maker.create_offer_for_ids(chia_for_cat, action_scope)
    await time_out_assert(10, get_trade_and_status, TradeStatus.PENDING_ACCEPT, trade_manager_maker, trade_make_1)
    assert error is None
    assert success is True
    assert trade_make_1 is not None
    async with trade_manager_maker.wallet_state_manager.new_action_scope(
        wallet_environments.tx_config, push=False
    ) as action_scope:
        success, trade_make_2, error = await trade_manager_maker.create_offer_for_ids(cat_for_chia, action_scope)
    await time_out_assert(10, get_trade_and_status, TradeStatus.PENDING_ACCEPT, trade_manager_maker, trade_make_2)
    assert error is None
    assert success is True
    assert trade_make_2 is not None

    [offer_1], signing_response_1 = await env_maker.node.wallet_state_manager.signer.sign_offers(
        [Offer.from_bytes(trade_make_1.offer)]
    )
    [offer_2], signing_response_2 = await env_maker.node.wallet_state_manager.signer.sign_offers(
        [Offer.from_bytes(trade_make_2.offer)]
    )
    agg_offer = Offer.aggregate([offer_1, offer_2])

    peer = env_taker.node.get_full_node_peer()
    async with env_taker.wallet_state_manager.new_action_scope(
        wallet_environments.tx_config,
        push=True,
        additional_signing_responses=[*signing_response_1, *signing_response_2],
    ) as action_scope:
        await trade_manager_taker.respond_to_offer(
            agg_offer,
            peer,
            action_scope,
        )
```

**File:** chia/_tests/wallet/cat_wallet/test_offer_lifecycle.py (L217-234)
```python
        new_offer = Offer.aggregate([chia_offer, red_offer, red_offer_2])
        assert new_offer.get_offered_amounts() == {None: 1000, str_to_tail_hash("red"): 350}
        assert new_offer.get_requested_amounts() == {None: 700, str_to_tail_hash("red"): 300}
        assert new_offer.is_valid()

        # Create yet another offer of BLUE for XCH and RED
        blue_requested_payments: dict[bytes32 | None, list[CreateCoin]] = {
            None: [CreateCoin(acs_ph, uint64(200), [b"blue memo"])],
            str_to_tail_hash("red"): [CreateCoin(acs_ph, uint64(50), [b"blue memo"])],
        }
        blue_notarized_payments = Offer.notarize_payments(blue_requested_payments, blue_coins)
        blue_announcements = Offer.calculate_announcements(blue_notarized_payments, driver_dict)
        blue_secured_bundle = generate_secure_bundle(blue_coins, blue_announcements, uint64(2000), tail_str="blue")
        blue_offer = Offer(blue_notarized_payments, blue_secured_bundle, driver_dict)
        assert not blue_offer.is_valid()

        # Test a re-aggregation
        new_offer = Offer.aggregate([new_offer, blue_offer])
```

**File:** chia/_tests/core/mempool/test_mempool.py (L1886-1896)
```python
        spend_bundle_combined = SpendBundle.aggregate([spend_bundle1, spend_bundle2])

        tx = full_node_protocol.RespondTransaction(spend_bundle_combined)

        peer = await connect_and_get_peer(server_1, server_2, bt.config["self_hostname"])
        status, err = await respond_transaction(full_node_1, tx, peer, test=True)

        sb = full_node_1.full_node.mempool_manager.get_spendbundle(spend_bundle_combined.name())
        assert err == Err.DOUBLE_SPEND
        assert sb is None
        assert status == MempoolInclusionStatus.FAILED
```
