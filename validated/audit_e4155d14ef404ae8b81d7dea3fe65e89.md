### Title
Unbounded recursive puzzle-driver matching on attacker-controlled offer puzzle reveals causes wallet stack-overflow DoS - (File: chia/wallet/outer_puzzles.py, chia/wallet/trading/offer.py)

### Summary
CVE-2018-11254 is an excessive-recursion DoS in PoDoFo's `PdfPagesTree::GetPageNode()`, triggered by a crafted file with deeply nested/self-referential structure that blows the call stack when parsed. The reachable analog in this codebase is the outer-puzzle driver chain used to parse offer puzzle reveals: `match_puzzle()` and the corresponding `get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` methods recurse through each driver's `also` field with no depth limit, driven entirely by attacker-controlled CLVM puzzle structure inside an offer file.

### Finding Description
`chia/wallet/outer_puzzles.py` dispatches to a fixed set of outer-puzzle drivers (`CATOuterPuzzle`, `OwnershipOuterPuzzle`, `CROuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`) via `match_puzzle()` [1](#0-0) . Each driver's `match()` recursively calls back into `self._match()` on the puzzle's inner layer whenever an `also` nested driver is found — e.g. `CATOuterPuzzle.match()` [2](#0-1) , `OwnershipOuterPuzzle.match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()`/`solve()` [3](#0-2) , and `CROuterPuzzle` with the same recursive pattern [4](#0-3) . This recursion depth is determined solely by how many nested puzzle layers (curried CAT/ownership/CR/singleton constructions) appear in an untrusted `puzzle_reveal`.

This is reachable from a fully untrusted, attacker-controlled path: `Offer.from_bytes()`/`from_bech32()` builds an `Offer` from raw bytes supplied by an offer counterparty, and `Offer._get_offered_coins()` calls `match_puzzle(parent_puzzle)` on every `coin_spend.puzzle_reveal` in the bundle [5](#0-4) . `puzzle_reveal` bytes are arbitrary attacker-chosen CLVM and can be constructed with thousands of nested "also"-recognizable layers (e.g., CAT-wrapping-CAT-wrapping-ownership-wrapping-CR, repeated), forcing Python-level recursion through these driver chains far past the interpreter's default recursion limit before any CLVM execution or cost accounting is applied.

### Impact Explanation
An offer counterparty (or any party who can get a wallet operator to inspect/parse an offer file — a normal, expected workflow before accepting or displaying an offer) can craft an offer whose puzzle reveals contain deeply nested recognized layers. When the wallet parses it (e.g., to summarize or evaluate the offer via `_get_offered_coins()`/`match_puzzle()`), the unbounded Python recursion raises `RecursionError`, crashing or hanging the wallet/RPC process handling that request — a spend-triggered/offer-triggered transaction-processing halt matching the accepted impact classes (invalid spend or block acceptance is not at issue here; this is a processing-halt DoS).

### Likelihood Explanation
Medium. Exploitation requires only sending a crafted, syntactically valid offer file (standard offer format) to a wallet user or counterparty, no special privileges or on-chain confirmation needed — the recursive matching runs purely on locally-parsed, unconfirmed puzzle reveal bytes. Building an offer with deeply nested recognized layers is straightforward using standard curry/outer-puzzle construction utilities in this same codebase (`CAT_MOD.curry(...)`, `construct_cr_layer(...)`, ownership layer construction), so no novel puzzle engineering is required — only enough nesting depth to exceed Python's default recursion limit.

### Recommendation
Impose an explicit recursion/nesting depth limit in `match_puzzle()` and the recursive driver methods (`match`, `get_inner_puzzle`, `get_inner_solution`, `construct`, `solve` in `outer_puzzles.py` and its driver classes), rejecting or erroring out on offers whose "also" chains exceed a sane maximum depth before recursing. Alternatively, convert the driver traversal to an iterative loop with an explicit stack (following the existing pattern already used for `sha256_treehash()` in `chia/types/blockchain_format/tree_hash.py`, which was deliberately made non-recursive to avoid blowing the Python stack) [6](#0-5) .

### Proof of Concept
Construct a `WalletSpendBundle`/`Offer` whose single `coin_spend.puzzle_reveal` is built by repeatedly wrapping an inner puzzle with `CAT_MOD.curry(...)` and/or `construct_cr_layer(...)`/ownership-layer construction thousands of times (each wrap is individually valid CLVM and each layer is independently recognized by its respective driver's `match()`), then serialize as a normal offer file (`Offer.compress()`/`to_bech32()`). Deliver this offer to a victim wallet. When the wallet calls `Offer.from_bech32()` followed by any code path that invokes `_get_offered_coins()` (e.g. inspecting/summarizing the offer prior to acceptance), the nested `match_puzzle()`→driver`.match()`→`self._match()` chain recurses once per layer, exceeding Python's recursion limit and raising `RecursionError`, halting that RPC/processing call. Exact confirmation of which RPC endpoint calls `_get_offered_coins()` without requiring the operator's explicit "accept" action was not fully verifiable within the indexed content (trade_manager.py references to `get_offered_coins`/`_get_offered_coins` were found but the full call chain from an inbound-offer RPC handler was not traced to completion) — this should be confirmed against `chia/wallet/trade_manager.py` and `chia/wallet/wallet_rpc_api.py` directly in a full checkout.

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

**File:** chia/wallet/nft_wallet/ownership_outer_puzzle.py (L39-101)
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

    def asset_id(self, constructor: PuzzleInfo) -> bytes32 | None:
        return None

    def construct(self, constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_puzzle = self._construct(also, inner_puzzle)
        transfer_program_info: PuzzleInfo | Program = constructor["transfer_program"]
        if isinstance(transfer_program_info, Program):
            transfer_program: Program = transfer_program_info
        else:
            transfer_program = self._construct(transfer_program_info, inner_puzzle)
        return puzzle_for_ownership_layer(constructor["owner"], transfer_program, inner_puzzle)

    def get_inner_puzzle(
        self, constructor: PuzzleInfo, puzzle_reveal: UnknownPuzzle, solution: Program | None = None
    ) -> Program | None:
        matched, curried_args = match_ownership_layer_puzzle(puzzle_reveal)
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

    def solve(self, constructor: PuzzleInfo, solver: Solver, inner_puzzle: Program, inner_solution: Program) -> Program:
        also = constructor.also()
        if also is not None:
            inner_solution = self._solve(also, solver, inner_puzzle, inner_solution)
        return solution_for_ownership_layer(inner_solution)
```

**File:** chia/wallet/vc_wallet/cr_outer_puzzle.py (L26-65)
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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
