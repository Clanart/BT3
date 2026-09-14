### Title
Stack-Exhaustion DoS via Unbounded Recursive Outer-Puzzle Layer Matching in Offer Parsing - (File: `chia/wallet/outer_puzzles.py`, `chia/wallet/trading/offer.py`)

### Summary
`Offer.from_spend_bundle()` and `Offer._get_offered_coins()` call `match_puzzle()` on every coin-spend's puzzle reveal contained in an untrusted offer file received from a trade counterparty. `match_puzzle()` and the per-layer driver `.match()`/`.get_inner_puzzle()`/`.get_inner_solution()`/`.construct()`/`.solve()` implementations (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`) recurse once per nested outer-puzzle "layer" via the `also` field, with no depth limit. A malicious offer whose puzzle reveal is built from thousands of nested CAT/singleton/ownership/CR layers (a purely structural construct requiring no CLVM execution cost) will drive unbounded Python recursion when the local wallet parses the offer, exhausting the Python call stack.

### Finding Description
`match_puzzle()` in `chia/wallet/outer_puzzles.py` iterates the `driver_lookup` table and calls each driver's `.match(puzzle)`: [1](#0-0) 

Each outer-puzzle driver's `match()` implementation uncurries one layer and, if the inner puzzle also matches a driver, recurses by calling `self._match` (which is `match_puzzle` again) on the inner puzzle, embedding the result as `also` in the constructor dict — with no bound on nesting depth: [2](#0-1) [3](#0-2) [4](#0-3) 

The `get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` methods on these same drivers mirror this unbounded recursion pattern when following the `also` chain: [5](#0-4) 

This machinery is invoked directly on attacker-controlled data when a wallet parses an offer file from a counterparty. `Offer.from_spend_bundle()` calls `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))` for every coin spend in the bundle, including the dummy settlement coin spends that carry the offer's requested-payment puzzle reveal: [6](#0-5) 

`Offer._get_offered_coins()` (used when displaying/inspecting an offer) similarly calls `match_puzzle` on every parent coin spend's puzzle reveal in the bundle: [7](#0-6) 

Because puzzle "layer" nesting (e.g., stacking `CAT(CAT(CAT(...ACS...)))` or `singleton(metadata(ownership(CAT(...))))`) is a pure `curry`/`uncurry` structural relationship, an attacker can construct an arbitrarily deep chain of nested outer puzzles without paying any CLVM execution cost or exceeding mempool cost limits — the puzzle is never run during matching, only uncurried layer by layer. This is directly analogous to the Pivotick CVE-2026-66920 bug class: a recursive traversal of caller-supplied, attacker-shaped graph/tree data (nested puzzle layers here, instead of nested JSON/graph nodes) with no depth limit or cycle/depth guard, as opposed to the codebase's other tree-walking code (`sha256_treehash`, `chia/types/blockchain_format/tree_hash.py`) which is deliberately implemented iteratively specifically "so we don't have to worry about blowing out the python stack": [8](#0-7) 

No such iterative/depth-limited safeguard exists for the outer-puzzle `also`-chain matching/construction/solving code path.

### Impact Explanation
A crafted offer file (`.offer`) sent by any wallet user acting as a trade counterparty can crash or hang the receiving wallet process when it is parsed (e.g., on `Offer.from_bech32`/inspection or acceptance flow leading to `from_spend_bundle`/`_get_offered_coins`). This is a client-side denial of service against the wallet process: an uncaught `RecursionError` can propagate up through wallet RPC handling or offer-management code, disrupting the wallet's ability to process trades or requiring a restart. This matches the CVSS 6.0 profile of the referenced CVE (availability impact, no confidentiality/integrity impact) — no coin movement, signature forgery, or consensus divergence results; the impact is limited to a spend/offer-triggered processing halt in the wallet.

### Likelihood Explanation
Likelihood is moderate-to-high for any wallet that inspects or attempts to accept offers from untrusted counterparties (a normal, expected wallet workflow: viewing an offer summary or accepting an offer file received out-of-band). Constructing the malicious puzzle reveal requires only nested `curry()` calls (no signing, no real coins, no execution) and can be done offline by anyone; only the resulting spend bundle's dummy settlement coin spend needs to be included in the offer file that is handed to a victim wallet. Python's default recursion limit (~1000) is well within reach of a nesting depth achievable in bytes-serialized CLVM without hitting other structural or size limits before the recursive matching code is exercised.

### Recommendation
- Convert `match_puzzle()` and the driver `.match()`/`.get_inner_puzzle()`/`.get_inner_solution()`/`.construct()`/`.solve()` "also"-chain traversal in `chia/wallet/outer_puzzles.py`, `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/singleton_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, and `chia/wallet/vc_wallet/cr_outer_puzzle.py` to an iterative, stack-based implementation, following the existing pattern used in `chia/types/blockchain_format/tree_hash.py`.
- Alternatively/additionally, impose an explicit maximum outer-puzzle layer-nesting depth (e.g., a small constant reflecting realistic legitimate puzzle stacks) in `match_puzzle`, returning "no match"/rejecting the offer once exceeded, before recursing further.
- Apply the same bound when parsing offers in `Offer.from_spend_bundle()` and `Offer._get_offered_coins()` so a pathological puzzle reveal is rejected early rather than driving unbounded recursion.

### Proof of Concept
Conceptual construction (not executed, but structurally sound given the cited code):
1. Build `ACS = Program.to(1)`.
2. Iteratively wrap: `p = ACS`; for `i` in `range(N)` (N ≈ 1200, exceeding Python's default recursion limit): `p = construct_cat_puzzle(CAT_MOD, tail, p)` (as done for a single layer in `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py:17-23`, but repeated N times instead of twice).
3. Use this deeply-nested `p` as the puzzle reveal of a dummy settlement `CoinSpend` (parent `bytes32.zeros`, per `Offer.from_spend_bundle`) inside a `WalletSpendBundle`, and package it as an offer (e.g., via `Offer(...)`/bech32 encoding).
4. Send the resulting offer file to a victim wallet. When the victim wallet parses it — e.g., via `Offer.from_spend_bundle()` calling `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))` — the nested `CATOuterPuzzle.match()` recursion depth reaches N, exceeding Python's stack limit and raising an unhandled `RecursionError`, crashing or hanging the wallet's offer-processing code path.

### Citations

**File:** chia/wallet/outer_puzzles.py (L49-54)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-45)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        args = match_cat_puzzle(puzzle)
        if args is None:
            return None
        _, tail_hash, inner_puzzle = args
        constructor_dict: dict[str, Any] = {
            "type": "CAT",
            "tail": "0x" + tail_hash.as_atom().hex(),
        }
        next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
        if next_constructor is not None:
            constructor_dict["also"] = next_constructor.info
        return PuzzleInfo(constructor_dict)
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

**File:** chia/wallet/nft_wallet/singleton_outer_puzzle.py (L32-54)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_singleton_puzzle(puzzle)
        if matched:
            singleton_struct, inner_puzzle = curried_args
            pair = singleton_struct.pair
            assert pair is not None
            launcher_struct = pair[1].pair
            assert launcher_struct is not None
            launcher_id = launcher_struct[0].atom
            assert launcher_id is not None
            launcher_ph = launcher_struct[1].atom
            assert launcher_ph is not None
            constructor_dict: dict[str, Any] = {
                "type": "singleton",
                "launcher_id": "0x" + launcher_id.hex(),
                "launcher_ph": "0x" + launcher_ph.hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L26-39)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        args: tuple[list[bytes32], Program, Program] | None = match_cr_layer(puzzle)
        if args is None:
            return None
        authorized_providers, proofs_checker, inner_puzzle = args
        constructor_dict: dict[str, Any] = {
            "type": "credential restricted",
            "authorized_providers": ["0x" + ap.hex() for ap in authorized_providers],
            "proofs_checker": disassemble(proofs_checker),
        }
        next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
        if next_constructor is not None:
            constructor_dict["also"] = next_constructor.info
        return PuzzleInfo(constructor_dict)
```

**File:** chia/wallet/trading/offer.py (L244-259)
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
```

**File:** chia/wallet/trading/offer.py (L634-647)
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
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
