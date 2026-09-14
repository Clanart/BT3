### Title
Ambiguous "guess the offered coin" heuristic in `Offer._get_offered_coins` lets an offer author manipulate which coin the taker's wallet settles - ([File: chia/wallet/trading/offer.py])

### Summary
`chia/wallet/trading/offer.py`'s `_get_offered_coins` (used by `get_offered_coins`, `get_offered_amounts`, `arbitrage`, and — critically — `to_valid_spend`) infers which child coins of an offer-maker-supplied `SpendBundle` are the actual "offered" settlement payments by *guessing*: it first tries to match additions whose `amount` equals the amount declared in a `CREATE_COIN` (opcode 51) condition to `OFFER_MOD_HASH`, and if that count doesn't line up, it falls back to matching purely by `puzzle_hash`, accepting whichever additions happen to hit that count, regardless of amount. [1](#0-0) 

### Finding Description
An `Offer` is built entirely from an untrusted, externally-supplied `WalletSpendBundle` (analogous to the `uniPool` balance in the reference report: a value the taker's code trusts but does not control) [2](#0-1) . When a taker calls `respond_to_offer`, the resulting `complete_offer` is turned into an actual on-chain-bound spend via `to_valid_spend`, which calls `get_offered_coins()`/`self.arbitrage()` to decide *which specific coin objects* to spend as the settlement payments and how much arbitrage change to route [3](#0-2) .

The coin-selection heuristic in `_get_offered_coins` is:
1. Read the `CREATE_COIN ... OFFER_MOD_HASH amount` conditions the parent puzzle emits (the "expected" offered amounts).
2. Try to find additions whose actual `.amount` matches one of those declared amounts.
3. If the counts don't match, throw away the amount filter and instead match *any* addition with the right puzzle hash, as long as the resulting count equals `expected_num_matches` [4](#0-3) .

Because a maker fully controls the `SpendBundle` they publish inside the offer file, they can construct a parent coin spend that creates *extra* `CREATE_COIN` outputs to `OFFER_MOD_HASH` (e.g., decoy/dust coins) alongside the legitimate settlement coin. This is directly analogous to the reference bug's "direct transfer to the pool": the wallet's guess of "what is the offered coin" is derived from observable coin data (amount/puzzle-hash matching) rather than from an authoritative, unambiguous binding, so an adversarial party can skew that observable data without affecting the part of the puzzle logic that is actually enforced on-chain (the `CREATE_COIN` conditions/announcements).

If the heuristic locks onto the wrong addition (a decoy instead of the intended offered coin, or vice versa), the amounts fed into `arbitrage()`/`get_offered_amounts()` (which determines how much the taker's `_create_offer_for_ids` computes it must pay, and how much leftover/arbitrage change is routed) become wrong, and `to_valid_spend()` builds a completion `CoinSpend` against a coin that does not correspond to what the taker actually intends to receive/settle [5](#0-4) .

### Impact Explanation
An adversarial offer-file author can cause a taker's `respond_to_offer` call to compute an incorrect arbitrage/settlement plan and to build a `CoinSpend` that targets the wrong settlement coin. This can (a) misroute the arbitrage remainder to a coin the taker did not intend, (b) cause the constructed spend bundle to be internally inconsistent with the actual `CREATE_COIN` conditions the maker's puzzle enforces (since the solution built for one coin's nonce/payments may be applied against a different coin), and (c) make the taker's `respond_to_offer` call fail unpredictably or settle against unintended value, without any indication to the taker that the offer they are inspecting differs from the one actually being executed. This falls squarely in "wallet user / offer counterparty" reachable surface and mirrors the reference bug class of trusting an attacker-influenceable observable (coin balance/amount matching) instead of a cryptographically bound value.

### Likelihood Explanation
Any user can craft and publish an arbitrary offer file (a raw, unsigned-except-for-settlement `WalletSpendBundle`) for another party to accept via `respond_to_offer`; no special privilege, node compromise, or timing is required — only that a victim chooses to accept the crafted offer.

### Recommendation
Do not infer offered coins by amount/puzzle-hash matching against an attacker-controlled set of additions. Instead, deterministically bind the offered coin(s) to the puzzle reveal/solution structure (e.g., require an unambiguous 1:1 mapping enforced by construction, or use the `nonce`/notarized-payment binding already present in `NotarizedPayment` to pin exact coin identities) so that extra `CREATE_COIN` outputs to `OFFER_MOD_HASH` from the same parent cannot be substituted for the intended settlement coin.

### Proof of Concept
1. Maker crafts an `Offer` whose underlying coin spend's inner puzzle emits two `CREATE_COIN` conditions to `OFFER_MOD_HASH`: the legitimate offered amount `X`, and a decoy amount `Y` (or a duplicate `X`), while genuinely wanting only one to be the "real" settlement coin.
2. Taker calls `respond_to_offer`; internally `Offer.arbitrage()`/`get_offered_coins()` runs `_get_offered_coins()` [6](#0-5) .
3. Because two additions now satisfy the amount/puzzle-hash matching criteria (or the fallback puzzle-hash-only branch is triggered), the heuristic can select the decoy coin instead of (or in addition to) the intended one.
4. `to_valid_spend()` builds its completion spend using this possibly-wrong coin set [5](#0-4) , producing a settlement transaction that does not match what the taker believed they were accepting.

### Citations

**File:** chia/wallet/trading/offer.py (L92-106)
```python
@dataclass(frozen=True, eq=False)
class Offer:
    requested_payments: dict[
        bytes32 | None, list[NotarizedPayment]
    ]  # The key is the asset id of the asset being requested
    _bundle: WalletSpendBundle
    driver_dict: dict[bytes32, PuzzleInfo]  # asset_id -> asset driver

    # this is a cache of the coin additions made by the SpendBundle (_bundle)
    # ordered by the coin being spent
    _additions: dict[Coin, list[Coin]] = field(init=False, repr=False)
    _hints: dict[bytes32, bytes32] = field(init=False)
    _offered_coins: dict[bytes32 | None, list[Coin]] = field(init=False, repr=False)
    _final_spend_bundle: WalletSpendBundle | None = field(init=False, repr=False)
    _conditions: dict[Coin, list[Condition]] | None = field(init=False)
```

**File:** chia/wallet/trading/offer.py (L244-303)
```python
    def _get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        offered_coins: dict[bytes32 | None, list[Coin]] = {}

        cost_left = INFINITE_COST
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None

                # We're going to look at the conditions created by the inner puzzle
                puzzle_cost, conditions = inner_puzzle.run_with_cost(cost_left, inner_solution)
                assert cost_left >= puzzle_cost
                cost_left -= puzzle_cost
                expected_num_matches: int = 0
                offered_amounts: list[int] = []
                for condition in conditions.as_iter():
                    if condition.first() == 51 and condition.rest().first() == OFFER_MOD_HASH:
                        expected_num_matches += 1
                        offered_amounts.append(condition.rest().rest().first().as_int())

                # Start by filtering additions that match the amount
                matching_spend_additions = [a for a in additions if a.amount in offered_amounts]

                if len(matching_spend_additions) == expected_num_matches:
                    coins_for_this_spend.extend(matching_spend_additions)
                # We didn't quite get there so now lets narrow it down by puzzle hash
                else:
                    # If we narrowed down too much, we can't trust the amounts so start over with all additions
                    if len(matching_spend_additions) < expected_num_matches:
                        matching_spend_additions = additions
                    matching_spend_additions = [
                        a
                        for a in matching_spend_additions
                        if a.puzzle_hash == construct_puzzle(puzzle_driver, OFFER_MOD).get_tree_hash()
                    ]
                    if len(matching_spend_additions) == expected_num_matches:
                        coins_for_this_spend.extend(matching_spend_additions)
                    else:
                        raise ValueError("Could not properly guess offered coins from parent spend")
            else:
                # It's much easier if the asset is bare XCH
                asset_id = None
                coins_for_this_spend.extend([a for a in additions if a.puzzle_hash == OFFER_MOD_HASH])

            # We only care about unspent coins
            coins_for_this_spend = [c for c in coins_for_this_spend if c not in self._bundle.removals()]

            if coins_for_this_spend != []:
                offered_coins.setdefault(asset_id, [])
                offered_coins[asset_id].extend(coins_for_this_spend)
        return offered_coins
```

**File:** chia/wallet/trading/offer.py (L511-539)
```python
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
```

**File:** chia/wallet/trade_manager.py (L887-891)
```python
        self.log.info("COMPLETE OFFER: %s", complete_offer.to_bech32())
        assert complete_offer.is_valid()
        final_spend_bundle: WalletSpendBundle = complete_offer.to_valid_spend(
            solver=Solver({**valid_spend_solver.info, **solver.info})
        )
```
