### Title
Uncontrolled Recursion in Wallet Outer-Puzzle Driver Matching Leads to Denial of Service via Malicious Offer/Puzzle Reveal - (File: chia/wallet/outer_puzzles.py)

### Summary
The wallet's "outer puzzle" driver framework (`chia/wallet/outer_puzzles.py` together with the per-asset drivers in `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/singleton_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`) recursively descends into nested puzzle layers by chasing each driver's `also()`/`_match`/`_construct`/`_get_inner_puzzle`/`_get_inner_solution`/`_solve` callback with no depth limit. The recursion depth is entirely controlled by an attacker who constructs a `puzzle_reveal` with an arbitrarily deep chain of curried outer layers (e.g. nested CAT layers). This is directly analogous to CWE-674 Uncontrolled Recursion: a crafted, cheaply-constructed input can force unbounded Python call-stack growth before any CLVM cost metering is even involved, since driver matching happens purely in Python before/without executing the puzzle.

### Finding Description
`match_puzzle()` in [1](#0-0)  iterates over `driver_lookup` and calls each driver's `.match()`. Each driver, once it matches its own layer, recurses into its inner puzzle via the shared `_match` callback to look for further nested layers, e.g. `CATOuterPuzzle.match()`: [2](#0-1) 

and `SingletonOuterPuzzle.match()`: [3](#0-2) 

The same unbounded recursive pattern repeats for `construct()`, `get_inner_puzzle()`, `get_inner_solution()`, and `solve()` on these drivers (e.g. `CATOuterPuzzle.get_inner_puzzle()` at [4](#0-3)  and `SingletonOuterPuzzle.get_inner_puzzle()`/`get_inner_solution()` at [5](#0-4) ).

Because `uncurry()` (via `UnknownPuzzle.curried_args`/`mod` in [6](#0-5) ) simply strips one curry layer per call, an attacker can produce a puzzle by repeatedly currying `CAT_MOD` (or any recognized outer-layer mod) around itself N times. Constructing such a puzzle is O(N) in size/CPU but very cheap (curry only wraps `(2 (1 . mod) args)`), so tens of thousands of layers can be built trivially and cheaply, unlike CLVM execution which is cost-metered. When the wallet later calls `match_puzzle()` (or `construct_puzzle`/`get_inner_puzzle`/`get_inner_solution`/`solve_puzzle`) on that `puzzle_reveal`, the Python call stack grows one frame per layer, exceeding Python's default recursion limit (1000) and raising an uncaught `RecursionError`.

This code path is reached whenever a wallet processes a puzzle reveal supplied by another party rather than one it generated itself — most notably when parsing or evaluating an **offer**. `Offer.from_spend_bundle()` calls `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))` directly on attacker-supplied coin spends: [7](#0-6) . Likewise `Offer._get_offered_coins()` calls `match_puzzle` on each `parent_spend.puzzle_reveal` from the bundle being processed: [8](#0-7) . These entry points are exercised whenever a wallet user or Data-Layer client loads/inspects/accepts an offer file (`chia wallet take_offer`, `get_offer_summary`, trade manager processing), i.e. content fully controlled by the offer counterparty.

### Impact Explanation
A malicious offer counterparty (or anyone who can get a wallet to inspect a crafted `puzzle_reveal`, e.g. via an `.offer` file, RPC `get_offer_summary`, or `take_offer`) can trigger unbounded Python recursion purely by nesting curry layers cheaply. This raises an uncaught `RecursionError` deep inside wallet processing code that is not designed to catch it, which can corrupt/abort the in-flight async task and, depending on where it is raised, potentially destabilize the wallet's event loop/coroutine handling — a denial of service against the wallet node when handling attacker-supplied trade data. This matches the reported bug class (CWE-674 Uncontrolled Recursion → excessive resource consumption / DoS on request processing) but scoped to the wallet's offer/puzzle-driver matching instead of Elasticsearch's query engine.

### Likelihood Explanation
Medium-High. Building a deeply nested curried puzzle is trivial (no CLVM execution, no cost limits apply to the construction of the reveal itself), and the vulnerable matching functions are invoked automatically whenever a wallet examines an untrusted offer (a very common workflow: sharing/opening `.offer` files, `take_offer`, `get_offer_summary`). No special privileges or signatures are required — only that the victim wallet parses the crafted offer/puzzle_reveal.

### Recommendation
- Impose an explicit maximum recursion/nesting depth (and/or convert the recursive `also()`/`_match`/`_construct`/`_get_inner_puzzle`/`_get_inner_solution`/`_solve` chains in `chia/wallet/outer_puzzles.py` and all `*_outer_puzzle.py` drivers to iterative, stack-based implementations, mirroring the approach already used for `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py`).
- Reject/short-circuit puzzle reveals whose outer-layer nesting exceeds a sane bound before attempting to match/uncurry them.
- Catch `RecursionError` at the offer-parsing/trade-manager boundary and translate it into a normal validation failure instead of letting it propagate.

### Proof of Concept
1. Programmatically build a "puzzle_reveal" `P_0 = <arbitrary inner puzzle>`, then for `i` in `1..N` (e.g. `N = 5000`) set `P_i = CAT_MOD.curry(CAT_MOD_HASH, some_tail_hash, P_{i-1})`, producing a puzzle recognized at each layer by `match_cat_puzzle()`.
2. Wrap `P_N` in a `CoinSpend` with a zero-parent coin (as used for offer notarized payments, see `chia/wallet/trading/offer.py::to_spend_bundle`) and embed it in a `WalletSpendBundle`/`Offer`.
3. Have a victim wallet load the resulting offer (`Offer.from_bech32` → `Offer.from_spend_bundle`, or `TradeManager`/RPC `get_offer_summary`) which calls `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))`.
4. Observe that `CATOuterPuzzle.match()` recurses once per curry layer (`chia/wallet/cat_wallet/cat_outer_puzzle.py:33-45`), exceeding Python's recursion limit and raising an uncaught `RecursionError`, aborting offer processing for the victim wallet.

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

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L47-63)
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

**File:** chia/wallet/puzzles/puzzle_drivers.py (L67-83)
```python
    @cached_property
    def _uncurry_result(self) -> UncurriedPuzzle:
        if self._uncurried_puzzle is not None:
            return self._uncurried_puzzle
        return uncurry_puzzle(self.program)

    @cached_property
    def mod(self) -> Program | None:
        if self._uncurry_result.mod == self.program:
            return None
        return self._uncurry_result.mod

    @cached_property
    def curried_args(self) -> list[Program] | None:
        if self.mod is None:
            return None
        return list(self._uncurry_result.args.as_iter())
```

**File:** chia/wallet/trading/offer.py (L251-259)
```python
            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
```

**File:** chia/wallet/trading/offer.py (L640-647)
```python
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
                asset_id = None
```
