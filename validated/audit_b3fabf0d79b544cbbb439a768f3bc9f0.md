### Title
Uncontrolled Recursion in Outer-Puzzle Driver Matching Enables Wallet DoS via Malicious Offer/Spend - (File: `chia/wallet/outer_puzzles.py`, `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, `chia/wallet/vc_wallet/cr_outer_puzzle.py`)

### Summary
The wallet's outer-puzzle driver framework (`match_puzzle`, `CATOuterPuzzle.match`/`get_inner_puzzle`/`get_inner_solution`/`solve`, `MetadataOuterPuzzle`, `CROuterPuzzle`, `SingletonOuterPuzzle`, etc.) recursively unwraps nested puzzle "layers" (CAT, CR, metadata, ownership) with **no depth limit**, exactly analogous to the LlamaIndex `KnowledgeBaseWebReader.get_article_urls` bug class: an untrusted, attacker-supplied structure drives unbounded recursive calls that can exhaust the Python call stack. Because CAT/CR/metadata layers can legitimately wrap another CAT/CR/metadata layer as their "inner puzzle" (confirmed by the project's own `test_cat_outer_puzzle.py`, which builds a `double_cat_puzzle` = CAT-wrapping-CAT), an attacker can construct a puzzle reveal with thousands of nested layers and hand it to a victim as an **offer file** or **spend bundle** for their wallet to parse.

### Finding Description
`match_puzzle()` in `chia/wallet/outer_puzzles.py` iterates the `driver_lookup` table and calls each driver's `match()`: [1](#0-0) 

Each driver's `match`, `get_inner_puzzle`, `get_inner_solution`, and `solve` methods recurse into themselves via the `also()`/`_match`/`_get_inner_puzzle`/`_get_inner_solution`/`_solve` callback chain whenever a nested "also" layer is detected, with no bound on nesting depth: [2](#0-1) [3](#0-2) [4](#0-3) 

Nested CAT-in-CAT puzzles are explicitly a supported, legitimate shape — the test suite constructs and matches a doubly-wrapped CAT puzzle: [5](#0-4) 

Because `Program.curry()`/`uncurry()` impose no limit on nesting depth (the curry helper simply builds nested CLVM structures) and CLVM tree hashing was explicitly made *iterative* to avoid Python recursion-limit issues (a design note that only applies to `sha256_treehash`, not to the outer-puzzle driver code): [6](#0-5) [7](#0-6) 

...an attacker can build a puzzle reveal consisting of many stacked CAT/CR/metadata layers (each legitimately using its inner puzzle slot to hold another instance of the same or a different layer type) and place it in a coin spend inside a `WalletSpendBundle`/`Offer`. When the victim's wallet processes this offer (e.g., during `respond_to_offer`/`_get_offered_coins`, which calls `match_puzzle` on every parent-spend puzzle reveal) or otherwise recognizes an incoming spend, the recursive `match`/`get_inner_puzzle`/`get_inner_solution`/`solve` chain will recurse once per nested layer: [8](#0-7) 

With sufficient nesting (a few thousand layers, well within CLVM program-size/serialization limits and comfortably below any mempool/cost constraint since this recursion happens in *pure Python* before/around CLVM execution, not inside the CLVM cost-metered interpreter), this exceeds Python's default recursion limit, raising an uncaught `RecursionError`, or in the worst case exhausting the underlying C stack. Unlike `sha256_treehash`, which was deliberately rewritten to be iterative specifically to avoid this class of bug, the outer-puzzle driver dispatch was not given the same treatment.

### Impact Explanation
A wallet operator (or Data Layer/offer counterparty) who receives an untrusted offer file, or whose wallet processes an incoming coin/spend containing a maliciously deep nested-puzzle structure, can have their wallet process crash or hang while attempting to recognize/parse the puzzle. This is a spend/offer-triggered denial of service against the wallet's transaction-processing path (offer taking, coin recognition, trade management) — the wallet becomes unable to process that spend bundle/offer and, depending on where the unhandled `RecursionError` propagates, the wallet RPC worker or process itself may crash, halting further request processing until restarted. This maps directly to the "spend-triggered transaction-processing halt" impact category.

### Likelihood Explanation
Likelihood is moderate-to-high for this specific class of interaction: any actor who can hand another party an offer file (a completely normal, unprivileged wallet-to-wallet interaction) or otherwise get a spend recognized by the victim's wallet (e.g., a coin sent to an address the victim's wallet tracks) can trigger the deep-nesting condition. Constructing a puzzle with thousands of nested layers is inexpensive — it only requires chained `curry()` calls, no signature or fee is required to *construct* the offer, and no special privileges are needed to deliver it to a victim.

### Recommendation
- Impose an explicit maximum nesting depth for outer-puzzle "also" chains in `chia/wallet/outer_puzzles.py`/the individual outer-puzzle driver classes (`CATOuterPuzzle`, `MetadataOuterPuzzle`, `CROuterPuzzle`, `OwnershipOuterPuzzle`, `SingletonOuterPuzzle`, `TransferProgramPuzzle`, `RevocationOuterPuzzle`), raising a clear `ValueError` once a configurable depth limit is exceeded, mirroring the `RecursionDepthExceededError` guard already used in the Data Layer file-import path.
- Alternatively, convert the recursive `match`/`get_inner_puzzle`/`get_inner_solution`/`solve` dispatch chain into an iterative loop over the "also" stack, following the same design rationale already applied to `sha256_treehash`.
- Wrap top-level offer/spend-bundle puzzle recognition entry points (`Offer._get_offered_coins`, `TradeManager.respond_to_offer`, coin-recognition callbacks) in a guard that catches `RecursionError` and converts it into a normal validation failure rather than letting it propagate and potentially crash the process.

### Proof of Concept
Conceptual PoC (not executed, since I only have code-index access, not a live environment):
1. Build a base ACS (`(mod solution ...)`) inner puzzle.
2. Repeatedly wrap it with `construct_cat_puzzle(CAT_MOD, tail, inner_puzzle)` (or alternate with `puzzle_for_metadata_layer`/CR-layer construction) N times, where N is large enough to exceed Python's recursion limit (e.g., N = 2,000–5,000), producing `deep_puzzle`.
3. Create a `CoinSpend` whose `puzzle_reveal` is `deep_puzzle` and package it into a `WalletSpendBundle`/`Offer` (following the same pattern as `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py`, but iterating the wrap step thousands of times instead of twice).
4. Deliver the resulting offer/spend bundle to a victim wallet (e.g., via `take_offer`/`respond_to_offer`, or by having a coin using this puzzle sent to an address the victim wallet tracks).
5. When the victim wallet calls `match_puzzle()` → `CATOuterPuzzle.match()` (or the metadata/CR equivalents) on the puzzle reveal in `Offer._get_offered_coins` / trade processing, the recursive `also()`-chain unwinding recurses once per layer, exceeding the Python recursion limit and raising `RecursionError`, halting that wallet operation.

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

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L39-89)
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
        return None  # Uncomment above when match_metadata_layer_puzzle works

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return bytes32(constructor["updater_hash"])

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        return puzzle_for_metadata_layer(constructor["metadata"], constructor["updater_hash"], inner_puzzle)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_metadata_layer_puzzle(puzzle_reveal)
        if matched:
            _, _, _, inner_puzzle = curried_args
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
        my_inner_solution: Program = solution.first()
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L26-64)
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

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        args: tuple[list[bytes32], Program, Program] | None = match_cr_layer(puzzle_reveal)
        if args is None:
            raise ValueError("This driver is not for the specified puzzle reveal")  # pragma: no cover
        _, _, inner_puzzle = args
        also = constructor.also()
        if also is not None:
            deep_inner_puzzle: Program | None = self._get_inner_puzzle(
                also, UnknownPuzzle(known_program=inner_puzzle), None
            )
            return deep_inner_puzzle
        else:
            return inner_puzzle

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.at("rrrrrrf")
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution
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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** .cursor/context/types.md (L41-46)
```markdown
- `Program.from_bytes()` intentionally parses through the Rust CLVM path. This gives a Python-compatible object while using the faster Rust parser/LazyNode path.
- `Program.run()` and `run_with_cost()` default to wallet-style flags, while module-level run helpers preserve lower-level legacy semantics. This distinction matters for strict mempool/soft-fork behavior.
- `_run()`, `uncurry()`, and `make_spend()` are compatibility adapters accepting both `Program` and `SerializedProgram`. They should not silently accept arbitrary objects because callers rely on type errors to catch malformed puzzle/solution construction.
- `curry()` and `uncurry()` encode/decode the canonical CLVM curry shape. Wallet puzzle drivers and tests assume this shape when matching layered puzzles.
- `sha256_treehash()` is deliberately iterative to avoid Python recursion limits on deeply nested CLVM. Replacing it with a recursive implementation changes a robustness property.
- `get_tree_hash_precalc()` treats any atom matching a supplied `bytes32` as already hashed. That is a performance and semantic contract used by puzzle-hash construction.
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
