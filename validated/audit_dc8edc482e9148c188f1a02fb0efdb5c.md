I found a confirmed recursive parsing analog: `match_puzzle()` in `chia/wallet/outer_puzzles.py` walks the layered driver chain (CAT/singleton/metadata/ownership/CR/revocation) recursively with **no depth limit**, and this is invoked directly on **attacker-supplied puzzle reveals from an unauthenticated `SpendBundle`/offer**, e.g. `Offer._get_offered_coins()` and `Offer.from_spend_bundle()`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unbounded recursive puzzle-driver matching on attacker-controlled curried puzzle depth causes wallet stack-exhaustion denial of service - (File: `chia/wallet/outer_puzzles.py`)

### Summary
`match_puzzle()`, and the `match`/`get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` methods of each outer-puzzle driver (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`), recurse through the "also" chain once per curried puzzle layer with no depth bound. This mirrors the QPDF CVE-2017-12595 bug class: a recursive descent parser that walks a nested data structure whose depth is fully controlled by untrusted input, with no explicit recursion-depth cap, leading to Python `RecursionError`/native stack exhaustion.

### Finding Description
`match_puzzle()` iterates all known drivers and calls `driver.match(puzzle)`. [1](#0-0) 

Each driver's `match()` uncurries one layer, then recurses into `self._match(UnknownPuzzle(known_program=inner_puzzle))` for the inner puzzle — this is `match_puzzle` again, so the recursion depth equals the number of curried "outer puzzle" layers present in the puzzle reveal: [2](#0-1) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` follow the same recursive "also" pattern and are similarly depth-unbounded.

This code is reached directly from processing an untrusted incoming offer or spend bundle: `Offer._get_offered_coins()` calls `match_puzzle(parent_puzzle)` on every coin spend's puzzle reveal taken straight from `self._bundle.coin_spends`, and `Offer.from_spend_bundle()` does the same when parsing a peer-supplied `WalletSpendBundle`. [8](#0-7) [9](#0-8) 

An attacker only needs to construct a puzzle reveal made of many nested valid CAT/singleton/metadata/ownership/CR layers (each layer is a cheap, well-formed curry of an already-known mod, so `match_cat_puzzle`/`match_singleton_puzzle`/etc. keep succeeding) around a trivial innermost puzzle. Because this is Python-level structural matching performed *before* (and independent of) CLVM cost-metered execution, it is not bounded by `MAX_BLOCK_COST_CLVM` or any of the CLVM `sha256_treehash`/Rust safeguards that the rest of the codebase carefully keeps non-recursive (contrast with `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py`, which is deliberately implemented as an explicit stack machine specifically "so we don't have to worry about blowing out the python stack"). [10](#0-9) 

Submitting an offer file, or a `WalletSpendBundle` from a trade counterparty, containing such a deeply nested puzzle reveal causes the receiving wallet's Python process to raise `RecursionError` (or crash with a native stack overflow depending on interpreter recursion limit settings) while trying to identify/parse the asset, halting offer/trade processing for that wallet.

### Impact Explanation
This is reachable by any offer counterparty or spend-bundle-supplying peer without needing valid signatures or successful CLVM execution — `match_puzzle` runs purely on the structural shape of the puzzle reveal. A crafted offer or spend bundle can crash or hang wallet-side offer/trade processing (`take_offer`, `create_offer_for_ids` paths that inspect driver dicts, and any RPC/CLI flow that calls `Offer.from_bytes`/`from_spend_bundle`/`_get_offered_coins`), producing a spend-triggered transaction-processing halt in the wallet service consuming attacker-supplied data.

### Likelihood Explanation
Building deeply nested, well-formed CAT/singleton/NFT-layer curries is cheap and mechanical (each layer is just `curry()` of a known mod around the previous layer); no signature or coin ownership is required to construct the puzzle reveal, only to have it accepted as a coin spend inside an unsigned/unvalidated `SpendBundle`/offer that gets handed to a wallet for inspection.

### Recommendation
Add an explicit maximum recursion/nesting depth to `match_puzzle()` and to each outer-puzzle driver's `match`/`get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` implementations (e.g., pass and decrement a depth counter, raising a clean error such as `ValueError("puzzle nesting too deep")` once a sane limit like 32–64 layers is exceeded), analogous to how `sha256_treehash` was deliberately made iterative to avoid Python recursion limits.

### Proof of Concept
1. Build `inner = Program.to(1)` (ACS).
2. Repeat N times (e.g. N = 5000): `inner = construct_cat_puzzle(CAT_MOD, tail, inner)` (or alternate with `puzzle_for_metadata_layer`/`puzzle_for_ownership_layer`/`puzzle_for_singleton` calls) to build a puzzle reveal with N curried outer layers around a trivial innermost puzzle.
3. Construct a `CoinSpend` using this puzzle reveal (any placeholder solution) inside a `WalletSpendBundle`, and wrap it as an `Offer` (or hand it directly to `Offer.from_spend_bundle`).
4. Call `Offer._get_offered_coins()` (or `match_puzzle(UnknownPuzzle(known_program=puzzle_reveal))` directly) — observe `RecursionError` / process crash rather than a graceful `ValueError`.

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

**File:** chia/wallet/trading/offer.py (L248-260)
```python
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

**File:** chia/wallet/trading/offer.py (L640-645)
```python
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
```

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L39-53)
```python
    def match(self, puzzle: UnknownPuzzle) -> PuzzleInfo | None:
        matched, curried_args = match_metadata_layer_puzzle(puzzle)
        if matched:
            _, metadata, updater_hash, inner_puzzle = curried_args
            constructor_dict = {
                "type": "metadata",
                "metadata": metadata,
                "updater_hash": "0x" + updater_hash.as_atom().hex(),
            }
            next_constructor = self._match(UnknownPuzzle(known_program=inner_puzzle))
            if next_constructor is not None:
                constructor_dict["also"] = next_constructor.info
            return PuzzleInfo(constructor_dict)
        else:
            return None
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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
