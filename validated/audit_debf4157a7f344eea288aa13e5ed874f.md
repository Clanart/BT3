### Title
Unbounded recursion in wallet outer-puzzle drivers when matching/unwrapping attacker-crafted nested puzzles causes stack-exhaustion DoS - ([File: chia/wallet/outer_puzzles.py])

### Summary
`match_puzzle()` in [1](#0-0)  and the `get_inner_puzzle`/`get_inner_solution`/`match`/`construct`/`solve` methods of every registered outer-puzzle driver (CAT, Singleton, Metadata, Ownership, CR, Royalty Transfer Program) recurse into their "inner puzzle" via `self._match`/`self._get_inner_puzzle`/`self._get_inner_solution` whenever an "also" layer is detected, e.g. [2](#0-1)  and [3](#0-2) . This mirrors the CVE-2015-8874 bug class: unbounded, input-driven recursion in a value-processing routine with no depth limit, leading to native/interpreter stack exhaustion.

### Finding Description
Each outer-puzzle driver's `match`, `get_inner_puzzle`, and `get_inner_solution` call back into the shared dispatcher (`match_puzzle`, `get_inner_puzzle`, `get_inner_solution` in `chia/wallet/outer_puzzles.py`) once per curried "layer" found in an untrusted `puzzle_reveal` (e.g. CAT-wrapped-in-singleton-wrapped-in-CAT…). There is no cap on how many layers can be nested — the recursion depth is entirely controlled by whoever constructs the puzzle reveal (a wallet counterparty in an offer, or the puzzle_reveal of a coin being processed). A crafted puzzle with thousands of trivially-curried outer layers (each satisfying one driver's `match_*_puzzle` shape) will drive Python recursion past the interpreter's default limit, raising an uncaught `RecursionError`.

This is architecturally the same class of bug as the PHP GD `imagefilltoborder` recursion: no bound is placed on recursion depth for attacker-supplied structured input, and the routine is reached directly from processing untrusted data (an offer, coin spend, or NFT/CAT puzzle reveal) rather than requiring a malicious peer or node.

This class of function is explicitly called out elsewhere in the codebase as a known risk pattern: `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py` was deliberately rewritten to be iterative "so we don't have to worry about blowing out the python stack" [4](#0-3) , and `chia/wallet/util/curry_and_treehash.py`'s `get_tree_hash()` on `Program` now delegates to the Rust implementation `tree_hash()` rather than the leftover recursive Python `_tree_hash` in `chia/types/blockchain_format/program.py` (lines 236-250), which appears otherwise dead. The outer-puzzle driver dispatch chain in `chia/wallet/outer_puzzles.py` and its consumers (e.g. `PuzzleWithRestrictions.from_memo`, `unknown_puzzles` in `chia/wallet/puzzles/custody/custody_architecture.py`, lines 271-318) were not given the same non-recursive treatment.

### Impact Explanation
An unhandled `RecursionError` while a wallet client is parsing an offer file, syncing a coin's puzzle reveal, or resolving an NFT/CAT/CR/singleton layer stack would abort the operation. Depending on where in the call stack this occurs (e.g. mid-transaction-construction or mid-offer-acceptance), it could leave wallet DB state partially updated or repeatedly crash wallet processing for a given coin/offer whenever it is retried, denying wallet service for that user (spend-triggered transaction-processing halt). This is a local/wallet-side DoS reachable by supplying a malicious offer file or coin puzzle to a wallet — not a consensus-breaking bug and not reachable by an unprivileged mempool submitter against the full node (the recursion lives in wallet-side Python driver code, not the Rust CLVM engine or mempool admission path).

### Likelihood Explanation
Medium. Constructing deeply nested curried puzzles that each match one of the recognized outer-puzzle shapes (CAT/singleton/metadata/ownership/CR) is programmatically straightforward and cheap (Python's default recursion limit is ~1000, so only on the order of a thousand nested layers are needed). The main uncertainty is whether the wallet code paths that call `match_puzzle`/`get_inner_puzzle` on externally supplied puzzle reveals (offer files, synced coin puzzle reveals) are reached before other validation (e.g., puzzle-hash/coin-id checks, cost limits) would reject the object first — this needs confirmation against `chia/wallet/trading/offer.py` and NFT/CAT wallet sync code, which I was not able to fully trace to a concrete unauthenticated entry point in the time available.

### Recommendation
- Impose an explicit maximum "also" layer depth in `match_puzzle`, `construct_puzzle`, `get_inner_puzzle`, and `get_inner_solution` (`chia/wallet/outer_puzzles.py`) and in each driver's recursive calls, rejecting puzzles/solutions that exceed a small sane bound (e.g., 16–32 layers) before recursing further.
- Alternatively, convert the driver-dispatch recursion into an explicit iterative loop/stack, following the same pattern already used for `sha256_treehash`.
- Apply the same depth cap to `PuzzleWithRestrictions.from_memo`/`unknown_puzzles` recursion in `chia/wallet/puzzles/custody/custody_architecture.py`, since `MofN` members can also nest without limit.
- Remove or guard the unused recursive `_tree_hash` helper in `chia/types/blockchain_format/program.py` to avoid future reintroduction of recursive tree-hashing on attacker data.

### Proof of Concept
Conceptual (not fully verified against a live entry point):
1. Build an innermost puzzle `P0` (e.g., `Program.to(1)`).
2. Repeatedly wrap it N times (N ≈ 2000) using `CAT_MOD.curry(...)`/`NFT_STATE_LAYER_MOD.curry(...)` shapes so each layer matches `match_cat_puzzle`/`match_metadata_layer_puzzle`.
3. Wrap the result as an `UnknownPuzzle(known_program=P_N)` and call `chia.wallet.outer_puzzles.match_puzzle(UnknownPuzzle(known_program=P_N))`.
4. Because `CATOuterPuzzle.match`/`MetadataOuterPuzzle.match` recurse via `self._match` for every "also" layer (`chia/wallet/cat_wallet/cat_outer_puzzle.py` lines 33-45, `chia/wallet/nft_wallet/metadata_outer_puzzle.py` lines 39-53), this raises `RecursionError` once N exceeds Python's recursion limit, instead of returning `None`/a bounded result.

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

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L47-61)
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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
