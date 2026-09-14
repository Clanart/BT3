I found a concrete analog. `match_puzzle()` in `chia/wallet/outer_puzzles.py` dispatches to per-layer drivers (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`, `TransferProgramPuzzle`) whose `match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` methods recurse into each other via the `also`/`_match`/`_get_inner_puzzle` callbacks with no depth bound, one Python stack frame per curried puzzle layer. This is directly analogous to the LibSass `parse_css_variable_value` uncontrolled-recursion DoS: an attacker who crafts an offer file or coin spend with a puzzle reveal containing thousands of nested recognized outer-puzzle layers (CAT-in-CAT-in-singleton-in-CR..., or an NFT/CR layer stack) can trigger unbounded Python recursion when a victim wallet parses it via `Offer.from_bytes()` → `Offer._get_offered_coins()` → `match_puzzle()`/`get_inner_puzzle()`, crashing the wallet process (`RecursionError`) before any CLVM cost limit is ever consulted, since this parsing happens entirely in Python outside the Rust CLVM cost-metered execution path. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Unbounded recursive puzzle-layer matching in wallet outer-puzzle drivers causes Python `RecursionError` DoS when parsing attacker-crafted offers/spends - (File: `chia/wallet/outer_puzzles.py`)

### Summary
`chia/wallet/outer_puzzles.py`'s `match_puzzle()`, `get_inner_puzzle()`, `get_inner_solution()`, `construct()`, and `solve()` dispatch through a table of driver objects (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`) that each call back into the same dispatch functions for the puzzle's `also`/inner layer, forming pure Python recursion with no depth limit, bounded only by the interpreter's default recursion limit (typically 1000).

### Finding Description
Each outer-puzzle driver's `match()` uncurries one layer of a puzzle reveal and, if a nested recognized layer is found, recursively calls `self._match(...)` (which is `match_puzzle`) on the inner puzzle, storing the result under `"also"` in the returned `PuzzleInfo`. The same recursive pattern exists in `get_inner_puzzle()`, `get_inner_solution()`, `construct()`, and `solve()` across `CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, and `CROuterPuzzle`. There is no maximum nesting-depth check anywhere in this call chain. [4](#0-3) [5](#0-4) 

This dispatch is reached from wallet-facing, attacker-influenced entry points: parsing an incoming offer via `Offer.from_bytes()`/`Offer.from_bech32()` and then computing `_get_offered_coins()` calls `match_puzzle(parent_puzzle)` and `get_inner_puzzle(...)` on each coin spend's puzzle reveal, which is taken directly from the (attacker-supplied) `SpendBundle` inside the offer. [6](#0-5)  `get_offered_coins()` is invoked from common wallet flows such as `TradeManager.calculate_tx_records_for_offer()` and CLI/RPC offer-summary/take-offer code paths. [7](#0-6) 

Because a puzzle reveal is just nested CLVM curry layers (e.g., CAT wrapping CAT wrapping singleton wrapping CR-layer wrapping ..., or metadata/ownership NFT layers), an attacker can construct a puzzle with an arbitrarily deep stack of recognized outer-puzzle mods. The CLVM execution of such a puzzle is cheap (curry/uncurry is O(depth) and inexpensive per layer), so it easily fits under any mempool/offer cost limits — the cost accounting in `chia_rs`/CLVM execution never sees this Python-side recursive walk, since `uncurry()`/`match()` operate on the already-parsed `Program` tree in pure Python, not inside metered CLVM execution.

This mirrors the reported bug class: an attacker-controlled nested structure drives unbounded recursive descent in a parsing routine (`Sass::Parser::parse_css_variable_value` there; `match_puzzle`/outer-puzzle-driver recursion here), exhausting the call stack and crashing the process handling the input.

### Impact Explanation
A wallet user (or GUI/CLI operator) who opens/takes/summarizes a maliciously crafted offer file, or a full node/wallet that processes a coin spend whose puzzle reveal contains a sufficiently deep chain of recognized outer-puzzle layers, will hit Python's recursion limit and raise `RecursionError`, crashing or hanging the wallet's processing task. This is a spend/offer-triggered transaction-processing halt reachable by an unprivileged offer counterparty or wallet user opening a received offer file — no privileged access is required, matching the CVSS profile of the source report (network-reachable, low complexity, requires user interaction, availability impact only).

### Likelihood Explanation
Constructing deeply nested but individually cheap CAT/singleton/CR/NFT-layer puzzles is straightforward with the existing wallet puzzle-construction helpers (`construct_cat_puzzle`, `construct_cr_layer`, singleton/ownership/metadata puzzle constructors), and wrapping them in a valid-looking `Offer`/`SpendBundle` requires no signature validation to merely trigger `match_puzzle`/`get_inner_puzzle` parsing (offer summary/take-offer code paths run this before/around signature checks). The recursion depth needed to exceed Python's default limit (~1000) is easily reachable without hitting CLVM cost limits, since each layer's curry/uncurry cost is small relative to the multi-billion cost budget.

### Recommendation
Convert the recursive `match`/`get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` dispatch in `chia/wallet/outer_puzzles.py` and the individual outer-puzzle driver classes (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`, `TransferProgramPuzzle`) to an iterative loop over layers, or add an explicit maximum nesting-depth guard that raises a handled error (not `RecursionError`) once exceeded, mirroring the deliberate non-recursive design already used for `sha256_treehash()` in `chia/types/blockchain_format/tree_hash.py` specifically to avoid Python recursion-limit crashes on deeply nested CLVM structures.

### Proof of Concept
1. Build an innermost puzzle `ACS = Program.to(1)`.
2. Repeatedly wrap it ~2000 times alternating `construct_cat_puzzle(CAT_MOD, tail, inner)` and `construct_cr_layer(providers, proofs_checker, inner)` (or any two/more recognized outer-puzzle mods) to produce `deep_puzzle`.
3. Create a `CoinSpend` with `puzzle_reveal=deep_puzzle` (paired with a matching coin/puzzle hash) and wrap it into a `WalletSpendBundle`/`Offer` (e.g., via `Offer.from_spend_bundle(...)` following the pattern in `chia/_tests/wallet/cat_wallet/test_offer_lifecycle.py`).
4. Call `Offer.get_offered_coins()` (or `match_puzzle(UnknownPuzzle(known_program=deep_puzzle))` directly, per `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py`'s usage pattern).
5. Observe `RecursionError` raised from the nested `match()`/`_match()` calls in `chia/wallet/outer_puzzles.py`, crashing the calling wallet task before any CLVM cost accounting is consulted. [4](#0-3) [5](#0-4)

### Citations

**File:** chia/wallet/outer_puzzles.py (L49-68)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None


def construct_puzzle(constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
    return driver_lookup[AssetType(constructor.type())].construct(constructor, inner_puzzle)


def solve_puzzle(constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
    return driver_lookup[AssetType(constructor.type())].solve(constructor, solver, inner_puzzle, inner_solution)


def get_inner_puzzle(
    constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
) -> Program | None:
    return driver_lookup[AssetType(constructor.type())].get_inner_puzzle(constructor, puzzle_reveal, solution)
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-62)
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

**File:** chia/wallet/trade_manager.py (L708-708)
```python
        settlement_coins: list[Coin] = [c for coins in offer.get_offered_coins().values() for c in coins]
```
