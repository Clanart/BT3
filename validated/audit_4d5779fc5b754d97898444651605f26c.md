### Title
Unbounded Python recursion when matching nested outer-puzzle layers in offers/coin spends can crash the wallet on attacker-supplied data - ([File: chia/wallet/outer_puzzles.py])

### Summary
`match_puzzle()` and each outer-puzzle driver's `match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` methods (CAT, metadata, ownership, CR, singleton) recurse into their own `_match`/`_get_inner_puzzle`/etc. callbacks once per curried puzzle "layer" found in an untrusted puzzle reveal, with no depth limit. An attacker who crafts a coin spend (e.g. inside an `Offer` sent to a counterparty, or a coin observed during wallet sync) with a deeply nested chain of CAT-in-CAT (or CAT/metadata/ownership) layers can drive this recursion past Python's interpreter stack limit, producing an uncaught `RecursionError` (or a genuine C-stack overflow/crash in CPython) when the victim processes the spend. This mirrors CVE-2024-45778's pattern: attacker-controlled nested structure drives unbounded recursion/loop leading to a crash of the parsing component, rather than the CLVM cost accounting (which only bounds cost, not Python call depth).

### Finding Description
`chia/wallet/outer_puzzles.py` `match_puzzle()` dispatches to each registered driver: [1](#0-0) 

`CATOuterPuzzle.match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` in `chia/wallet/cat_wallet/cat_outer_puzzle.py` each recurse into `self._match`/`self._get_inner_puzzle`/etc. (which are bound back to the same top-level `match_puzzle`/`get_inner_puzzle`/... dispatchers) whenever the constructor has an `also()` (i.e., another nested layer): [2](#0-1) 

The test suite explicitly demonstrates that a CAT puzzle can be nested inside another CAT puzzle (`construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)`), and that this doubly-nested structure is successfully matched/recursed through by the driver chain: [3](#0-2) 

The same "also"-chain recursion pattern exists in `MetadataOuterPuzzle` (`chia/wallet/nft_wallet/metadata_outer_puzzle.py`) and `OwnershipOuterPuzzle` (`chia/wallet/nft_wallet/ownership_outer_puzzle.py`), all reachable from the same `driver_lookup` table.

This machinery is exercised on wholly untrusted, wallet-supplied puzzle reveals. `Offer` (received from an untrusted offer counterparty and loaded via `Offer.from_bytes`) computes `get_offered_coins()`/`get_cancellation_coins()` by calling `match_puzzle()` on every coin spend's `puzzle_reveal` in the bundle: [4](#0-3) [5](#0-4) 

There is no depth cap anywhere in this driver-dispatch chain (`match_puzzle`, `construct_puzzle`, `get_inner_puzzle`, `get_inner_solution`), unlike `chia.types.blockchain_format.tree_hash.sha256_treehash`, which was deliberately made iterative specifically to avoid "blowing out the python stack" on deeply nested CLVM structures: [6](#0-5) 

That comment demonstrates the project is aware recursion-based CLVM tree walking is a real robustness hazard, yet the outer-puzzle driver dispatch chain (used for offers, CAT/NFT/DID/VC syncing) was not built with the same iterative-stack discipline.

### Impact Explanation
An attacker constructs an `Offer` (or a coin spend a victim wallet will sync/process) whose puzzle reveal contains thousands of nested CAT (or CAT/metadata/ownership) curry layers. When the victim wallet loads the offer and calls `get_offered_coins()`/`get_cancellation_coins()` (which happens automatically as part of offer inspection/acceptance flow in `trade_manager.py`), or when wallet sync encounters such a puzzle, the recursive `match_puzzle`/`get_inner_puzzle` chain can exceed Python's recursion limit and raise an unhandled `RecursionError`, crashing or hanging the wallet's transaction-processing path. This is a spend/offer-triggered processing halt matching the CVE class (uncontrolled loop/recursion on attacker-crafted nested structure leading to a crash), reachable by any offer counterparty or wallet-sync peer sending a crafted spend — no privileged access required.

### Likelihood Explanation
Medium. Constructing deeply nested CAT/metadata/ownership curry layers is cheap (CLVM `curry()` composition, no execution needed) and does not require large CLVM cost, since match/uncurry happens in Python outside of CLVM cost accounting. The main uncertainty is the exact number of layers needed to exceed CPython's default recursion limit through this specific call chain (which involves several stacked function calls per layer — `match_puzzle` → driver `match()` → `self._match` → `match_puzzle`, several frames per curried layer), and whether any higher-level exception handling in `trade_manager.py`/RPC layers already catches broad exceptions before this can crash the whole process rather than just fail the one operation. I was not able to fully confirm whether `respond_to_offer`/RPC offer-inspection paths wrap this in a broad try/except that would only fail the single RPC call versus crashing the wallet service; this reduces confidence in it being a full process-halting DoS versus a per-call failure.

### Recommendation
- Add an explicit depth/layer limit (or iterative rewrite, following the same pattern already used in `sha256_treehash`) to `match_puzzle()`, `construct_puzzle()`, `get_inner_puzzle()`, and `get_inner_solution()` in `chia/wallet/outer_puzzles.py` and the corresponding driver implementations (`CATOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`, `SingletonOuterPuzzle`).
- Reject offers/coin spends whose outer-puzzle layer nesting exceeds a sane bound (e.g., a small constant reflecting realistic CAT/NFT/VC layering) before attempting to match/construct/solve them.
- Wrap offer-parsing entry points (`Offer.from_bytes`, `get_offered_coins`, `get_cancellation_coins`) so that `RecursionError` is caught and converted into a normal validation failure rather than propagating and potentially destabilizing the wallet process.

### Proof of Concept
Conceptual PoC (not executed): construct a CAT puzzle nested inside itself N times via `construct_cat_puzzle(CAT_MOD, tail, construct_cat_puzzle(CAT_MOD, tail, ... ACS))` for large N (e.g. N = 2000), place it as the `puzzle_reveal` of a `CoinSpend` inside a `SpendBundle`, wrap it in an `Offer`, and call `offer.get_offered_coins()` (mirroring `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py::test_cat_outer_puzzle`, which already proves 2-layer nesting is matched/recursed through by `match_puzzle`/`CATOuterPuzzle`). At sufficiently large N this is expected to raise `RecursionError` in the `match_puzzle` → `CATOuterPuzzle.match` → `self._match` recursion chain.

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

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-63)
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

**File:** chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py (L17-33)
```python
def test_cat_outer_puzzle() -> None:
    ACS = Program.to(1)
    tail = bytes32.zeros
    cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, ACS)
    double_cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)
    uncurried_cat_puzzle = UnknownPuzzle(known_program=double_cat_puzzle)
    cat_driver: PuzzleInfo | None = match_puzzle(uncurried_cat_puzzle)

    assert cat_driver is not None
    assert cat_driver.type() == "CAT"
    assert cat_driver["tail"] == tail
    inside_cat_driver: PuzzleInfo | None = cat_driver.also()
    assert inside_cat_driver is not None
    assert inside_cat_driver.type() == "CAT"
    assert inside_cat_driver["tail"] == tail
    assert construct_puzzle(cat_driver, ACS) == double_cat_puzzle
    assert get_inner_puzzle(cat_driver, uncurried_cat_puzzle) == ACS
```

**File:** chia/wallet/trading/offer.py (L244-260)
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
```

**File:** chia/wallet/trading/offer.py (L431-443)
```python
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
