Confirmed: `Offer.from_spend_bundle` and `Offer._get_offered_coins` both call `match_puzzle()` on attacker-supplied `puzzle_reveal` data taken directly from an untrusted offer file / spend bundle, at `chia/wallet/trading/offer.py:641` and `chia/wallet/trading/offer.py:255`, before the offer is ever pushed to chain. This is a genuine analog to the go-billy bug class (CWE-674/CWE-835: uncontrolled recursion / missing depth limit over untrusted, attacker-controlled nested structure), since `match_puzzle` recurses through the outer-puzzle "also" chain with no depth cap.

### Title
Unbounded recursive puzzle-layer matching on attacker-supplied offer/coin-spend data can crash a wallet (CWE-674/CWE-835) - (File: chia/wallet/outer_puzzles.py, chia/wallet/trading/offer.py)

### Summary
`chia/wallet/outer_puzzles.py`'s `match_puzzle()` dispatches to per-asset-type outer puzzle drivers (`CATOuterPuzzle`, `OwnershipOuterPuzzle`, `SingletonOuterPuzzle`, `CROuterPuzzle`, `MetadataOuterPuzzle`) which recursively call `self._match(...)` on the inner puzzle for every curried "also" layer, with no maximum recursion/nesting-depth limit and no cycle detection. This chain is driven entirely by the structure of a `puzzle_reveal` supplied by a counterparty in an untrusted offer file or observed coin spend.

### Finding Description
`match_puzzle` ( [1](#0-0) ) is called on `UnknownPuzzle(known_program=coin_spend.puzzle_reveal)` from two reachable, untrusted-input paths:
- `Offer.from_spend_bundle()`, used when a wallet loads/parses an offer file received from any counterparty: [2](#0-1) 
- `Offer._get_offered_coins()`, used internally whenever the offer's contents are inspected (summary, validity checks, completion): [3](#0-2) 

Each driver's `match()` recurses into `self._match(UnknownPuzzle(known_program=inner_puzzle))` for a nested "also" constructor, e.g. `CATOuterPuzzle.match()` [4](#0-3)  and `OwnershipOuterPuzzle.match()` [5](#0-4) . `get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` recurse the same way. None of these code paths bound recursion depth, and a repeated codebase-wide search confirms there is no `RecursionError` handling or `sys.setrecursionlimit` anywhere in the repository.

Because CLVM's `uncurry()` only recognizes a fixed curry shape, an attacker cannot literally create a graph cycle here — but they can nest known outer-puzzle mod hashes arbitrarily deep (e.g., `CAT(CAT(CAT(...CAT(ACS)...)))` or interleaved CAT/ownership/singleton/CR layers) inside a single `puzzle_reveal`. Since the wallet parses this structure with plain Python recursion (one Python stack frame per nesting layer) rather than an iterative walk, sufficiently deep nesting will exhaust the Python call stack and raise an uncaught `RecursionError`, crashing whichever wallet code path invoked `match_puzzle`/`get_inner_puzzle`/`get_inner_solution`.

### Impact Explanation
An attacker crafts an offer file (or any spend the target wallet observes/queries, since `_get_offered_coins` is also invoked when validating/inspecting offers already recorded) whose `puzzle_reveal` recursively nests recognized outer-puzzle layers far beyond ordinary use (ordinary CAT/NFT/CR/VC nesting is at most a handful of layers, so any nesting depth beyond that is inherently adversarial). Loading, summarizing, or attempting to take this offer in a victim's wallet triggers unbounded Python recursion and an uncaught `RecursionError`, halting the wallet's transaction-processing/offer-handling code path (a spend-triggered transaction-processing halt / DoS against wallet nodes and RPC callers who load untrusted offers). This matches the "resource exhaustion (uncontrolled recursion)" bug class from the go-billy advisory, applied here to Chia's offer/trade puzzle-matching pipeline rather than a symlink filesystem walk.

### Likelihood Explanation
Likelihood is moderate: constructing such a puzzle_reveal requires only combining existing, publicly known outer-puzzle mod hashes (CAT_MOD, ownership layer, singleton, CR layer, metadata layer) via ordinary curry operations — no special privileges, keys, or chain state are needed. The victim only needs to load or inspect the malicious offer (a normal, expected wallet/RPC operation for any received offer), matching the "offer counterparty" reachability the scan scope calls out.

### Recommendation
Add an explicit recursion/nesting-depth limit (and/or convert the outer-puzzle "also" traversal to an iterative loop) in `match_puzzle`, `get_inner_puzzle`, `get_inner_solution`, `construct`, and `solve` in `chia/wallet/outer_puzzles.py` and each outer-puzzle driver (`cat_outer_puzzle.py`, `ownership_outer_puzzle.py`, `singleton_outer_puzzle.py`, `cr_outer_puzzle.py`, `metadata_outer_puzzle.py`), and reject/raise a clean error once a sane maximum layer count is exceeded, rather than allowing Python's native recursion limit to be hit.

### Proof of Concept
1. Build a `Program` for an inner "always-succeed" puzzle (`ACS = Program.to(1)`).
2. Repeatedly wrap it thousands of times with `construct_cat_puzzle(CAT_MOD, tail_hash, inner)` (see `chia/wallet/cat_wallet/cat_utils.py`) to build a deeply nested `puzzle_reveal`, e.g. `cat_puzzle_n = construct_cat_puzzle(CAT_MOD, tail, cat_puzzle_{n-1})` for `n` in the thousands (as in the existing `test_cat_outer_puzzle` test at [6](#0-5)  but with far greater nesting).
3. Embed this puzzle reveal into a `CoinSpend`/`Offer` bytes payload and have a victim wallet call `Offer.from_spend_bundle()` (e.g., via `cmds wallet take_offer` or the `take_offer` RPC) or otherwise trigger `_get_offered_coins()`/`match_puzzle()` on it.
4. Observe the process raise an uncaught `RecursionError`, terminating offer processing (and potentially the wallet process/RPC handler) instead of returning a controlled error.

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

**File:** chia/wallet/trading/offer.py (L640-645)
```python
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
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

**File:** chia/wallet/nft_wallet/ownership_outer_puzzle.py (L39-55)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_ownership_layer_puzzle(puzzle)
        if matched:
            _, current_owner, transfer_program, inner_puzzle = curried_args
            owner_bytes: bytes = current_owner.as_python()
            tp_match: PuzzleInfo | None = self._match(UnknownPuzzle(known_program=transfer_program))
            constructor_dict = {
                "type": "ownership",
                "owner": "()" if owner_bytes == b"" else "0x" + owner_bytes.hex(),
                "transfer_program": (disassemble(transfer_program) if tp_match is None else tp_match.info),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
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
