### Title
Front-running arbitrage extraction in offer completion (`to_valid_spend`) — analogous to `matchOrders()` front-running - ([File: chia/wallet/trading/offer.py])

### Summary
The 0x `matchOrders()` report describes a caller that pays only fees while capturing the price difference ("spread") between two matched orders, making it profitable for any observer to front-run the transaction. Chia's offer-matching path has a structurally identical property: when an `Offer` is completed via `Offer.to_valid_spend(arbitrage_ph, ...)`, any positive arbitrage (spread) left over after satisfying both sides' notarized payments is paid to an attacker-controlled `arbitrage_ph`, and the completing spends of the `OFFER_MOD` ("settlement_payments") coins require no signature from either counterparty — only that the notarized payment conditions are satisfied.

### Finding Description
`Offer.arbitrage()` computes, per asset, how much the offered amount exceeds the requested amount [1](#0-0) . When completing an offer (or an aggregation of multiple offers), `to_valid_spend` appends an extra `NotarizedPayment(arbitrage_ph, arbitrage_amount)` for any positive spread and pays it to whatever `arbitrage_ph` is passed by the caller [2](#0-1) . The actual coins being finalized are the `OFFER_MOD` ("settlement_payments") coins, which are spent via completion `CoinSpend`s built in this same function using only public information from the offer (parent spends, driver dict, and the payment list) [3](#0-2) . These completion spends do not require any signature tied to the original maker/taker keys — the settlement puzzle only cares that the required notarized payments (puzzle-hash/amount pairs) are created; any excess (the arbitrage amount) can be redirected to any puzzle hash supplied at spend time.

`TradeManager.respond_to_offer` builds a `complete_offer` by aggregating the maker's offer with the taker's own signed spend, and only then calls `to_valid_spend` to produce the final, broadcastable `SpendBundle` [4](#0-3) . Similarly, `Offer.aggregate` allows combining multiple independently signed offers into one bundle with no additional signature required to bind the arbitrageur's identity to the resulting spend [5](#0-4) .

Because the fully-signed component offers (maker offer / aggregated offers) are necessarily shared or broadcast (as `.offer` files, RPC payloads, or as a pending spend bundle in the mempool) before the final completion transaction is confirmed, any third party who observes them can independently call `to_valid_spend` themselves with `arbitrage_ph` pointed at their own address, and resubmit the resulting spend bundle to the mempool with a higher fee than the original submitter. Since the underlying signed pieces (the maker/taker `AGG_SIG` commitments) are unaffected by choice of `arbitrage_ph`, this steals the arbitrage/spread without invalidating any signature — mirroring exactly the "any caller can extract the spread and is incentivized to front-run" property described in the `matchOrders()` report.

### Impact Explanation
An observer who front-runs the completion transaction can redirect the entire positive arbitrage amount (the value spread between offered/requested payments across one or more aggregated offers) to themselves, at the cost only of paying a higher mempool fee. This is a direct value-extraction/theft vector against whichever party (taker or arbitraging relayer) was expected to receive that spread, while both original counterparties still get exactly what they asked for — so the theft is invisible to the makers/takers whose funds move as expected, but the intended profit recipient is silently front-run.

### Likelihood Explanation
This requires: (1) a scenario where an offer/aggregated offer produces non-zero arbitrage (e.g., an offer aggregator/relayer completing multiple counterparty offers, or a taker over-paying relative to notarized requirements), and (2) the completion spend bundle (or the signed component offers) being observable before confirmation (mempool visibility or shared offer files). Both conditions are realistic for any DEX/relayer/aggregator use case built on chia offers, and the underlying gas-auction dynamics are identical to the 0x report. However, exploitation is limited to cases where arbitrage > 0 is present in the completion (plain 1:1 offer takes with matching notarized payments have zero arbitrage and are not exploitable this way).

### Recommendation
As with the 0x resolution, this is inherent to the "anyone can complete the puzzle-satisfying spend" design of `OFFER_MOD`/settlement payments, and is expected to be mitigated at the application layer (e.g., relayer submits directly via trusted channel, uses a private mempool, or binds the completion to a specific solver/authorized taker) rather than in the base offer/consensus layer. If arbitrage capture needs protection, offer aggregators should avoid broadcasting unconfirmed aggregated offers publicly and should submit completion transactions through low-visibility channels, or restrict `arbitrage_ph` binding via additional puzzle conditions (e.g., an `ASSERT_MY_PUZZLEHASH`/announcement scheme) so that only the intended party's completion is valid.

### Proof of Concept
1. Party A (maker) creates a signed offer requesting less value than a counterpart is willing to pay; an arbitrageur observes and aggregates offer A with offer B from a second maker via `Offer.aggregate([offer_a, offer_b])` [5](#0-4) .
2. The arbitrageur calls `aggregated_offer.to_valid_spend(arbitrage_ph=<arbitrageur_ph>)` to build the completion spend bundle capturing the spread [2](#0-1) .
3. The arbitrageur broadcasts this spend bundle to the mempool.
4. A third-party observer sees the still-unconfirmed spend bundle (or independently obtains offer A and offer B, e.g. from shared offer files), reconstructs the same aggregation, and calls `to_valid_spend(arbitrage_ph=<attacker_ph>)`, producing a functionally equivalent, fully valid spend bundle that redirects the arbitrage amount to themselves.
5. The attacker submits this bundle with a higher fee; the mempool replacement rules (`can_replace`) allow displacing the original bundle as long as fee-per-cost increases and the coin-spend superset rule is satisfied [6](#0-5) , resulting in the attacker's version being confirmed instead and the original arbitrageur receiving nothing.

### Citations

**File:** chia/wallet/trading/offer.py (L329-342)
```python
    def arbitrage(self) -> dict[bytes32 | None, int]:
        """
        Returns a dictionary of the type of each asset and amount that is involved in the trade
        With the amount being how much their offered amount within the offer
        exceeds/falls short of their requested amount.
        """
        offered_amounts: dict[bytes32 | None, int] = self.get_offered_amounts()
        requested_amounts: dict[bytes32 | None, int] = self.get_requested_amounts()

        arbitrage_dict: dict[bytes32 | None, int] = {}
        for asset_id in [*requested_amounts.keys(), *offered_amounts.keys()]:
            arbitrage_dict[asset_id] = offered_amounts.get(asset_id, 0) - requested_amounts.get(asset_id, 0)

        return arbitrage_dict
```

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

**File:** chia/wallet/trading/offer.py (L506-523)
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

```

**File:** chia/wallet/trading/offer.py (L541-595)
```python
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

**File:** chia/wallet/trade_manager.py (L872-891)
```python
            _success, take_offer, _error = result

            complete_offer, valid_spend_solver = await self.check_for_final_modifications(
                Offer.aggregate([offer, take_offer]), solver, inner_action_scope
            )

        async with action_scope.use() as interface:
            if interface.side_effects.get_unused_derivation_record_result is not None:
                # This error is of a protection against potential misues of this band-aid solution.
                # We should put more thought into how sub-action scopes are generated and what effects
                # we might want to push. A ticket to this respect can be found [CHIA-2984].
                raise ValueError("Cannot use `respond_to_offer` with existing puzzle hash generation")
            interface.side_effects.get_unused_derivation_record_result = (
                inner_action_scope.side_effects.get_unused_derivation_record_result
            )
        self.log.info("COMPLETE OFFER: %s", complete_offer.to_bech32())
        assert complete_offer.is_valid()
        final_spend_bundle: WalletSpendBundle = complete_offer.to_valid_spend(
            solver=Solver({**valid_spend_solver.info, **solver.info})
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
