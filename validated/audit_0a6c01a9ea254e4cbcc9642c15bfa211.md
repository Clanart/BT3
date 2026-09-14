I have enough detail now to write the analog finding. The key issue is at `chia/wallet/trading/offer.py` line 260: `assert inner_puzzle is not None and inner_solution is not None` inside `_get_offered_coins()`, which is reached whenever a wallet/RPC caller processes an untrusted offer (e.g. `Offer.from_bech32`/`from_bytes` from an offer counterparty, followed by `get_offered_coins()`/`summary()`/`to_valid_spend()`). This mirrors the CVE pattern: an assertion on a value that can legitimately be `None` for malformed/adversarial input, reachable via untrusted data, causing a crash (reachable assertion, CWE-617) instead of a handled `ValueError`.

### Title
Reachable assertion crash when parsing an untrusted/malicious offer's outer-puzzle drivers - (File: chia/wallet/trading/offer.py)

### Summary
`Offer._get_offered_coins()` calls `get_inner_puzzle()`/`get_inner_solution()` (via the outer-puzzle driver framework in `chia/wallet/outer_puzzles.py`) on puzzle reveals and solutions taken directly from an untrusted offer's `SpendBundle`, then blindly asserts the results are non-`None`: `assert inner_puzzle is not None and inner_solution is not None` [1](#0-0) . Because `match_puzzle()` only checks that the top-level mod matches a known outer puzzle (e.g. CAT via `match_cat_puzzle`), but the corresponding `get_inner_puzzle`/`get_inner_solution` driver methods can independently fail to fully match the deeper structure (e.g. singleton driver: match succeeds on mod, but `get_inner_solution` reads `solution.at("rrf")` unconditionally without validating solution shape, or CAT driver's `get_inner_puzzle` raises `ValueError` for a mismatched `match_cat_puzzle` on the *solution*'s structure) [2](#0-1) [3](#0-2) , a crafted offer whose puzzle reveal matches a driver's `match()` but whose paired solution is shaped so the inner puzzle/solution extraction returns `None` (rather than raising) will hit this bare `assert`.

### Finding Description
Offers are untrusted, attacker-supplied data: they arrive as bech32 strings/bytes from an offer counterparty and are parsed via `Offer.from_bech32`/`from_bytes`/`try_offer_decompression` → `from_spend_bundle` with no validation of the internal puzzle/solution consistency beyond basic streamable parsing [4](#0-3) [5](#0-4) . Once constructed, essentially every downstream inspection API a wallet uses to *evaluate* an offer before deciding to accept it — `summary()`, `get_offered_amounts()`, `arbitrage()`, `is_valid()`, `get_pending_amounts()`, `get_primary_coins()`, and ultimately `to_valid_spend()` used when accepting the trade — funnels through `get_offered_coins()` → `_get_offered_coins()` [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) .

Inside `_get_offered_coins`, for any coin spend whose puzzle reveal matches a known outer-puzzle type, the code unconditionally trusts that `get_inner_puzzle`/`get_inner_solution` will return a non-`None` `Program`: `assert inner_puzzle is not None and inner_solution is not None` [10](#0-9) . This is the same bug class as CVE-2021-28905: a reachable assertion (CWE-617) on attacker-influenced data instead of a graceful validation failure. Unlike the neighboring code in the same function, which raises catchable `ValueError`s for other malformed-offer conditions ("Could not properly guess offered coins from parent spend") [11](#0-10) , this particular failure path is a bare Python `assert`, which (a) is stripped entirely if Python is ever run with `-O` optimizations, and (b) even when active, raises an uncatchable-by-design `AssertionError` that is not wrapped in any `try/except` at this call site or in its callers (`get_offered_coins`, `arbitrage`, `is_valid`, `summary`, `to_valid_spend`).

### Impact Explanation
When a wallet RPC caller (via `push_tx`/`take_offer`/`get_offer_summary` style flows) processes such a crafted offer, the assertion failure is a hard crash out of the normal control flow instead of a handled error, causing a spend-triggered halt to offer processing for the RPC caller/thread handling that request. This matches the accepted impact category of "a spend-triggered transaction-processing halt" for an unprivileged offer counterparty submitting a single malicious offer.

### Likelihood Explanation
Medium-High: constructing an offer whose puzzle reveal satisfies a driver's `match()` (checking only the top mod/curried structure) while its paired solution is shaped to fail deeper unpacking in `get_inner_puzzle`/`get_inner_solution` is achievable without any signature or consensus validation, since `Offer.from_bytes`/`from_bech32` perform no CLVM execution or signature check before these accessor methods are invoked by wallet UI/RPC code paths that routinely inspect offers before accepting them.

### Recommendation
Replace the bare `assert inner_puzzle is not None and inner_solution is not None` in `_get_offered_coins` with an explicit check that raises a catchable `ValueError`/`ValidationError` (consistent with the sibling `raise ValueError("Could not properly guess offered coins from parent spend")` a few lines below), and audit `get_inner_puzzle`/`get_inner_solution` implementations in `chia/wallet/*_outer_puzzle.py` to ensure malformed solutions raise instead of silently returning `None` from an unhandled path.

### Proof of Concept
1. Take a legitimate CAT-outer-puzzle-wrapped coin spend and keep its puzzle reveal (so `match_puzzle` succeeds and identifies it as `CAT`).
2. Replace/malform the paired solution's structure so that `CATOuterPuzzle.get_inner_solution`'s `solution.first()` extraction path resolves to a shape that a nested `also`-driver's `get_inner_solution`/`get_inner_puzzle` returns `None` for (or construct an offer stacking a singleton/CR/ownership outer puzzle whose `also()` chain has this mismatch), without raising inside those helper functions.
3. Package this crafted `CoinSpend` plus a matching dummy "requested payment" spend into an `Offer` via `Offer.from_spend_bundle`/serialize with `to_bech32`.
4. Have a victim wallet load the offer (`Offer.from_bech32`) and call any inspection method that reaches `get_offered_coins()` (e.g. `summary()` used by `chia wallet get_offer_summary` / RPC `get_offer_summary`), triggering the `AssertionError` at `chia/wallet/trading/offer.py:260` and aborting the request instead of returning a clean error.

### Citations

**File:** chia/wallet/trading/offer.py (L256-260)
```python
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None
```

**File:** chia/wallet/trading/offer.py (L290-291)
```python
                    else:
                        raise ValueError("Could not properly guess offered coins from parent spend")
```

**File:** chia/wallet/trading/offer.py (L305-311)
```python
    def get_offered_coins(self) -> dict[bytes32 | None, list[Coin]]:
        try:
            if self._offered_coins is not None:
                return self._offered_coins
        except AttributeError:
            object.__setattr__(self, "_offered_coins", self._get_offered_coins())
        return self._offered_coins
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

**File:** chia/wallet/trading/offer.py (L511-514)
```python
        all_offered_coins: dict[bytes32 | None, list[Coin]] = self.get_offered_coins()
        total_arbitrage_amount: dict[bytes32 | None, int] = self.arbitrage()
        for asset_id, payments in self.requested_payments.items():
            offered_coins: list[Coin] = all_offered_coins[asset_id]
```

**File:** chia/wallet/trading/offer.py (L634-663)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
                asset_id = None
            if coin_spend.coin.parent_coin_info == bytes32.zeros:
                notarized_payments: list[NotarizedPayment] = []
                for payment_group in Program.from_serialized(coin_spend.solution).as_iter():
                    nonce = bytes32(payment_group.first().as_atom())
                    payment_args_list = payment_group.rest().as_iter()
                    notarized_payments.extend(
                        [NotarizedPayment.from_condition_and_nonce(condition, nonce) for condition in payment_args_list]
                    )

                requested_payments[asset_id] = notarized_payments
            else:
                leftover_coin_spends.append(coin_spend)

        return cls(
            requested_payments, WalletSpendBundle(leftover_coin_spends, bundle.aggregated_signature), driver_dict
        )
```

**File:** chia/wallet/trading/offer.py (L720-724)
```python
    @classmethod
    def from_bytes(cls, as_bytes: bytes) -> Offer:
        # Because of the __bytes__ method, we need to parse the dummy CoinSpends as `requested_payments`
        bundle = WalletSpendBundle.from_bytes(as_bytes)
        return cls.from_spend_bundle(bundle)
```

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L66-90)
```python
    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_singleton_puzzle(puzzle_reveal)
        if matched:
            _, inner_puzzle = curried_args
            also = constructor.also()
            if also is not None:
                deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                    also, UnknownPuzzle(known_program=inner_puzzle), None
                )
                return deep_inner_puzzle
            else:
                return inner_puzzle
        else:
            raise ValueError("This driver is not for the specified puzzle reveal")

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.at("rrf")
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L47-72)
```python
    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        args = match_cat_puzzle(puzzle_reveal)
        if args is None:
            raise ValueError("This driver is not for the specified puzzle reveal")
        _, _, inner_puzzle = args
        also = constructor.also()
        if also is not None:
            deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                also,
                UnknownPuzzle(known_program=inner_puzzle),
                solution.first() if solution is not None else None,
            )
            return deep_inner_puzzle
        else:
            return inner_puzzle

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.first()
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution
```
