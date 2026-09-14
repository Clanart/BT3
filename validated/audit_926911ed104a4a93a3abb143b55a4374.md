### Title
Offer.to_valid_spend() can KeyError-crash on a validated offer with a zero-amount requested payment for an asset with no offered coins - ([File: chia/wallet/trading/offer.py])

### Summary
`Offer.to_valid_spend()` assumes that every `asset_id` present in `requested_payments` also has a corresponding entry in the `dict` returned by `get_offered_coins()`. This assumption is only enforced indirectly through `is_valid()`/`arbitrage()`, and there is a gap: an asset with a zero-amount requested payment and no actually offered coins for that asset produces `arbitrage == 0` (which `is_valid()` accepts), yet `get_offered_coins()` never populates a key for that asset. The subsequent direct dict index `all_offered_coins[asset_id]` in `to_valid_spend()` then raises an unhandled `KeyError`, crashing whatever wallet/RPC code path is processing the (attacker-crafted) offer.

### Finding Description
`Offer.is_valid()` gates `to_valid_spend()`: [1](#0-0) 

It delegates to `arbitrage()`, which builds a combined dict over the union of offered and requested asset ids, using `.get(asset_id, 0)` fallbacks on both sides: [2](#0-1) 

Because both sides use `.get(..., 0)`, an asset that is *requested* with amount `0` but never actually *offered* (no settlement-payment coin exists for it, so `get_offered_coins()`/`_get_offered_coins()` never inserts that key) still yields `arbitrage_dict[asset_id] == 0`, which satisfies `is_valid()`'s `value >= 0` check for every asset: [3](#0-2) 

`__post_init__` only requires that a `driver_dict` entry exists for every requested asset id — it does not require that offered coins exist for it: [4](#0-3) 

`to_valid_spend()` then iterates `self.requested_payments.items()` and directly indexes the offered-coins map without checking membership, mirroring the TensorFlow bug's pattern of assuming a nested/derived lookup succeeds because an outer check passed: [5](#0-4) 

Since `all_offered_coins` (from `get_offered_coins()`) never contains the asset id in this crafted scenario, `offered_coins: list[Coin] = all_offered_coins[asset_id]` raises `KeyError`, an unhandled exception that propagates up through `to_valid_spend()`.

### Impact Explanation
`to_valid_spend()`/`is_valid()` are called from wallet offer-acceptance and validation flows reachable by any offer counterparty who receives/loads an externally supplied `Offer` (bech32m offer string), e.g. `TradeManager` and CLI/RPC accept-offer paths, and by `Offer.aggregate()` consumers. A malicious/crafted offer file that trips this code path causes an unhandled `KeyError` in the process handling it — a spend-triggered transaction-processing halt for the wallet accepting or validating the offer (denial of service on the offer-accept code path), matching the null-dereference/crash bug class in the report (an assumed-safe nested lookup that is not actually guaranteed by the preceding check).

### Likelihood Explanation
Constructing such an `Offer` requires crafting `requested_payments` with a zero-amount entry for an asset id whose `driver_dict` entry exists but for which no genuine settlement coin was produced in the spend bundle. This is a non-trivial but achievable construction for an attacker producing an offer file to send to a counterparty (offers are untrusted, attacker-controlled serialized data by design), and no other part of the codebase reviewed here validates that requested-payment amounts must be non-zero or that every requested asset id must have real offered coins before `to_valid_spend()` is invoked. I could not fully verify within the available context whether an earlier validation layer (e.g., `NotarizedPayment`/`CreateCoin` construction, RPC-level `Offer.from_bech32`/summary checks in the untraced parts of the code) unconditionally rejects zero-amount payments before reaching this code; this should be checked further, e.g. by tracing `NotarizedPayment.__post_init__`/`CreateCoin` validation and the RPC entry points for `take_offer`.

### Recommendation
In `Offer.to_valid_spend()`, explicitly validate that every `asset_id` key in `requested_payments` (and any positive-arbitrage overflow asset) exists in `get_offered_coins()` before indexing, raising a clear `ValueError` ("Offer is missing offered coins for requested asset") instead of allowing an unhandled `KeyError`. More robustly, strengthen `arbitrage()`/`is_valid()` so that an asset id requested with any amount, including zero, but absent from `get_offered_coins()`, is treated as invalid, closing the gap where `.get(..., 0)` on both sides of the arbitrage computation masks a missing offered-coins entry.

### Proof of Concept
1. Build `driver_dict` containing an entry for asset id `A` (e.g., a CAT asset) so `__post_init__`'s driver check passes.
2. Set `requested_payments = {A: [NotarizedPayment(ph, uint64(0), [], nonce=...)]}` — a zero-amount requested payment for asset `A`.
3. Build `_bundle` (`WalletSpendBundle`) such that no settlement-payment child coin is created for asset `A` — i.e., `_get_offered_coins()` never inserts key `A` into its returned dict.
4. Confirm `offer.is_valid()` returns `True` because `arbitrage()[A] == offered_amounts.get(A, 0) - requested_amounts.get(A, 0) == 0 - 0 == 0 >= 0`.
5. Call `offer.to_valid_spend()`; observe the unhandled `KeyError` at `all_offered_coins[asset_id]` [6](#0-5) , crashing the caller (e.g., trade-accept/RPC flow) that processes this externally supplied offer.

### Citations

**File:** chia/wallet/trading/offer.py (L150-161)
```python
    def __post_init__(self) -> None:
        # Verify that there are no duplicate payments
        for payments in self.requested_payments.values():
            payment_programs: list[bytes32] = [p.name() for p in payments]
            if len(set(payment_programs)) != len(payment_programs):
                raise ValueError("Bundle has duplicate requested payments")

        # Verify we have a type for every kind of asset
        for asset_id in self.requested_payments:
            if asset_id is not None and asset_id not in self.driver_dict:
                raise ValueError("Offer does not have enough driver information about the requested payments")

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

**File:** chia/wallet/trading/offer.py (L501-502)
```python
    def is_valid(self) -> bool:
        return all([value >= 0 for value in self.arbitrage().values()])
```

**File:** chia/wallet/trading/offer.py (L510-522)
```python
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
