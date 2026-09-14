### Title
Unbounded Python-level recursion when matching nested outer-puzzle layers on an untrusted offer/spend bundle causes a wallet-crashing `RecursionError` - ([File: chia/wallet/outer_puzzles.py])

### Summary
`chia/wallet/outer_puzzles.py`'s `match_puzzle()` and the various `*OuterPuzzle.match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()` implementations (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`) recurse in pure Python, once per detected "outer" layer, with no depth limit. Because the "also" chain is driven entirely by the untrusted `puzzle_reveal` bytes of a `CoinSpend` (e.g. inside an `Offer`), an attacker can nest layers (CAT-in-CAT-in-CAT..., or CR-in-CAT-in-CR...) arbitrarily deep and drive this recursion past CPython's recursion limit, crashing the wallet process that is examining the offer/spend bundle.

### Finding Description
`match_puzzle()` in `chia/wallet/outer_puzzles.py` calls `driver.match(puzzle)` for a `puzzle_reveal`: [1](#0-0) 

Each driver's `match()` recognizes its own curried mod and then recursively calls back into `match_puzzle()` (via the injected `self._match`) on the puzzle's inner argument to discover further "also" layers, e.g. in `CATOuterPuzzle.match()`: [2](#0-1) 

and identically in `SingletonOuterPuzzle.match()`, `MetadataOuterPuzzle.match()`, `OwnershipOuterPuzzle.match()`, and `CROuterPuzzle.match()`: [3](#0-2) [4](#0-3) 

`match_cat_puzzle()` only checks that the outer mod is `CAT_MOD` and returns its curried args — it places no restriction on what the inner puzzle is, so a CAT puzzle can be curried with another CAT puzzle (or CR/singleton/metadata layer) as its inner puzzle, arbitrarily deep: [5](#0-4) 

This same recursive "also" pattern also exists in `get_inner_puzzle()`/`get_inner_solution()`/`construct()` for each driver, and is exercised directly on attacker-supplied data by `Offer._get_offered_coins()`, which is invoked while parsing/examining an offer (a message an untrusted offer counterparty gives to a wallet user) — it calls `match_puzzle`, `get_inner_puzzle`, and `get_inner_solution` on every `parent_spend.puzzle_reveal` in the offer's bundle: [6](#0-5) 

None of this Python-level recursion is capped by CLVM cost accounting (that only governs the Rust CLVM VM execution of the puzzle, not the Python driver-matching logic that walks curried layers), and none of it is capped by an explicit depth counter, unlike the Data Layer's tree-loading code, which was hardened to raise `chia_rs.datalayer.RecursionDepthExceededError` for deeply nested untrusted trees (see `chia/_tests/core/data_layer/test_data_store.py::test_insert_into_data_store_from_file_line_graph_depth`). No equivalent guard exists for outer-puzzle layer matching.

### Impact Explanation
An attacker constructs a spend/`puzzle_reveal` (e.g. `CAT(tail, CAT(tail, CAT(tail, ... ACS)))`, nested past CPython's default recursion limit, ~1000 frames, multiplied by however many Python stack frames each `match()`/`get_inner_puzzle()` call consumes) and embeds it either directly in a coin spend that reaches wallet-side inspection, or in an `Offer` file sent to an offer counterparty. When the victim wallet calls `match_puzzle`/`get_inner_puzzle`/`get_inner_solution` (e.g. via `Offer._get_offered_coins()` when examining/taking the offer, or via CAT/CR wallet driver matching during syncing), the unbounded Python recursion either raises an uncaught `RecursionError` that propagates out of wallet code paths not designed to catch it, or — because CPython recursion checks interact with the real C stack — can exhaust the process stack and crash the wallet process outright. This is a spend/offer-triggered transaction-processing halt against the wallet handling the malicious input, matching the "stack recursion crash on untrusted-input parsing" bug class from the APR-util report (`apr_xml_quote_elem()` recursion crash).

### Likelihood Explanation
High: constructing a deeply nested CAT/CR/singleton/metadata/ownership puzzle reveal requires no signatures and no special privileges — it's just curried CLVM structure. Delivering it only requires sending an offer file or a coin spend for the victim's wallet to examine; this fits the explicitly in-scope "offer counterparty" and "wallet user" reachability. No mempool admission or on-chain confirmation is even necessary to trigger the crash, since `Offer._get_offered_coins()` runs during offer inspection before any push.

### Recommendation
Add an explicit recursion/depth bound (and/or convert the "also" chain traversal to an iterative loop) in `match_puzzle()`/`construct_puzzle()`/`get_inner_puzzle()`/`get_inner_solution()` in `chia/wallet/outer_puzzles.py` and all `*OuterPuzzle` driver implementations, rejecting puzzle reveals whose outer-layer nesting exceeds a small sane maximum (mirroring the depth guard already used for Data Layer tree ingestion, `chia_rs.datalayer.RecursionDepthExceededError`). Ensure `Offer` construction/inspection paths catch and convert any residual `RecursionError` into a handled `ValidationError` rather than letting it propagate and crash the process.

### Proof of Concept
1. Build `puzzle = ACS` (`Program.to(1)`).
2. Repeat N times (N large enough to approach/exceed Python's recursion limit, e.g. 2000+): `puzzle = construct_cat_puzzle(CAT_MOD, tail_hash, puzzle)` (as already demonstrated for depth 2 in `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py`'s `double_cat_puzzle` construction) — each iteration adds one more "also" layer.
3. Wrap this puzzle as a `puzzle_reveal` in a `CoinSpend`, place it in a `WalletSpendBundle`, and construct an `Offer` object from it (or otherwise pass it to `match_puzzle(UnknownPuzzle(known_program=puzzle))`).
4. Observe that `match_puzzle()` recurses N times through `CATOuterPuzzle.match()` → `self._match()` → ... until it raises `RecursionError`, uncaught by any of the surrounding wallet/offer code, which is unhandled and terminates or corrupts the wallet processing path.

(Note: I did not have the ability to run this PoC in the codebase; the depth thresholds and exact number of frames-per-layer to trigger `RecursionError` vs. a native C-stack crash are estimated from the code structure shown above and would need empirical confirmation in a live Devin session.)

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

**File:** chia/wallet/cat_wallet/cat_utils.py (L46-54)
```python
def match_cat_puzzle(puzzle: UnknownPuzzle) -> Iterator[Program] | None:
    """
    Given the curried puzzle and args, test if it's a CAT and,
    if it is, return the curried arguments
    """
    if puzzle.mod == CAT_MOD and puzzle.curried_args is not None:
        return iter(puzzle.curried_args)
    else:
        return None
```

**File:** chia/wallet/trading/offer.py (L251-260)
```python
            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
                assert inner_puzzle is not None and inner_solution is not None
```
