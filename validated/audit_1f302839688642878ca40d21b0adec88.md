### Title
Unbounded recursive puzzle-layer matching in wallet offer/driver code allows attacker-crafted deeply nested puzzle to crash a wallet via Python stack exhaustion - (File: chia/wallet/outer_puzzles.py)

### Summary
The ALPINE-CVE-2022-25313 bug class is a stack-exhaustion via unbounded recursion when parsing attacker-controlled nested structures (expat DTD element nesting). The closest reachable analog in this codebase is the recursive "outer puzzle" driver-matching chain used by the wallet to interpret puzzle reveals for CATs, credential-restricted (CR) CATs, NFTs, and offers. Unlike `sha256_treehash` (deliberately made iterative, per [1](#0-0) ) and the Rust CLVM parser used for `Program.from_bytes` (per [2](#0-1) ), the `outer_puzzles` driver framework recurses in pure Python with no depth limit each time a puzzle exposes an `also()` sub-driver (layered CAT/CR/ownership/metadata/singleton puzzles).

### Finding Description
`match_puzzle`, `construct_puzzle`, `solve_puzzle`, `get_inner_puzzle`, and `get_inner_solution` in [3](#0-2)  dispatch to per-asset-type driver objects (`CATOuterPuzzle`, `CROuterPuzzle`, `OwnershipOuterPuzzle`, `MetadataOuterPuzzle`, `SingletonOuterPuzzle`). Each of these drivers recognizes exactly one curried CLVM layer and then recurses into itself/the next driver for any wrapped ("also") inner layer, e.g.:
- `CATOuterPuzzle.match/get_inner_puzzle/get_inner_solution` recurse via `self._match`, `self._get_inner_puzzle`, `self._get_inner_solution` for each layer [4](#0-3) .
- `CROuterPuzzle` does the same for credential-restricted layers [5](#0-4) , and the test suite explicitly demonstrates that CR layers can be nested arbitrarily deep by wrapping one CR puzzle inside another (`double_cr_puzzle = construct_cr_layer(..., cr_puzzle)`) per [6](#0-5) .
- `OwnershipOuterPuzzle` and `MetadataOuterPuzzle` recurse identically [7](#0-6) [8](#0-7) .

Nothing in this dispatch chain enforces a maximum layer count. Because the puzzle structure being matched is derived purely from an untrusted `puzzle_reveal`/`UnknownPuzzle(known_program=...)` supplied in a coin spend or an offer, an attacker fully controls how many CAT/CR/ownership/metadata layers are curried on top of one another — the tree-hash of the resulting puzzle is computed correctly at any depth (unlike a real validity requirement), so there is no economic or structural cost preventing thousands of nested layers other than CLVM byte size. This is the direct analog of the expat bug: a parser/interpreter that recurses once per nesting level of an attacker-supplied structure, with the code's own test suite acknowledging that "malicious generator programs" causing large recursive nesting are a known concern class in this codebase, e.g. [9](#0-8) .

This driver-matching code (`chia/wallet/outer_puzzles.py`) is directly exercised by offer construction/parsing in `chia/wallet/trading/offer.py`, which imports and calls `match_puzzle`, `construct_puzzle`, `get_inner_puzzle`, `get_inner_solution`, and `solve_puzzle` [10](#0-9) , and by NFT wallet coin identification in `chia/wallet/nft_wallet/nft_wallet.py`. Offers are inherently attacker/counterparty-controlled data: a malicious offer file or a malicious puzzle reveal embedded in a coin sent to a victim can be crafted with excessive nesting depth and handed to a wallet user's node/RPC to parse.

### Impact Explanation
If an attacker can cause `outer_puzzles.match_puzzle` (or the analogous `construct_puzzle`/`get_inner_puzzle`/`get_inner_solution` calls) to recurse past Python's default recursion limit (~1000) by crafting an offer or puzzle reveal with sufficiently many nested CAT/CR/ownership/metadata layers, the wallet process raises an uncaught `RecursionError`. Depending on where this is triggered (offer parsing, trade acceptance, NFT coin-state identification during sync), this can crash the wallet daemon or halt further transaction/offer processing for the affected user — a spend/offer-triggered transaction-processing halt. This does not affect consensus-critical full-node validation (that path uses the Rust CLVM implementation and explicit `RecursionDepthExceededError` guards, e.g. Data Layer's binary-tree ingestion at [11](#0-10) ), but it is a real, unguarded Python-recursion DoS vector in wallet-side puzzle/offer interpretation.

### Likelihood Explanation
Medium. No signature, funds, or special privilege is required — an offer counterparty or anyone who can get a puzzle reveal in front of a victim's wallet (e.g., via an offer file, a spend that creates a coin owned by the victim with a deeply layered puzzle, or NFT coin-state sync) controls the puzzle structure entirely. The main uncertainty is the exact number of nesting layers needed to exceed the Python recursion limit and whether any upstream CLVM program-size/cost limits would reject the puzzle reveal bytes before the Python-side driver code runs; this codebase does not appear to enforce a nesting-depth cap in the driver-matching code, only in the unrelated Data Layer binary-tree loader, which suggests this class of guard was not applied to the offer/outer-puzzle path.

### Recommendation
Add an explicit maximum layer/recursion depth to the `outer_puzzles` driver-dispatch chain (`match`, `construct`, `solve`, `get_inner_puzzle`, `get_inner_solution` for `CATOuterPuzzle`, `CROuterPuzzle`, `OwnershipOuterPuzzle`, `MetadataOuterPuzzle`) and reject puzzles/offers that exceed it before recursing, or convert the recursive dispatch into an iterative loop analogous to `sha256_treehash`'s iterative design in `chia/types/blockchain_format/tree_hash.py`. Additionally, validate/bound the number of curried layers as part of offer/puzzle-reveal ingestion in `chia/wallet/trading/offer.py` prior to invoking `match_puzzle`.

### Proof of Concept
1. Programmatically build a nested puzzle using `construct_cr_layer(authorized_providers, proofs_checker, inner)` (or `CATOuterPuzzle`/`OwnershipOuterPuzzle` equivalents) in a loop, each iteration wrapping the previous result, for N iterations (e.g., N = 2000), analogous to the pattern already shown for two layers in [6](#0-5) .
2. Package the resulting puzzle as a `puzzle_reveal` in an offer or coin spend and present it to a victim wallet (e.g., via `chia.wallet.trading.offer` parsing or NFT coin-state sync).
3. When the wallet calls `match_puzzle(UnknownPuzzle(known_program=nested_puzzle))` (`chia/wallet/outer_puzzles.py`), each layer triggers another Python-level recursive call into `CROuterPuzzle.match`/`self._match`, eventually raising `RecursionError` and crashing/hanging the wallet's offer/sync processing thread.

### Citations

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/types/blockchain_format/program.py (L56-68)
```python
    @classmethod
    def from_bytes(cls, blob: bytes) -> Self:
        # this runs the program "1", which just returns the first argument.
        # the first argument is the buffer we want to parse. This effectively
        # leverages the rust parser and LazyNode, making it a lot faster to
        # parse serialized programs into a python compatible structure
        _cost, ret = run_chia_program(
            b"\x01",
            blob,
            50,
            0,
        )
        return cls.to(ret)
```

**File:** chia/wallet/outer_puzzles.py (L49-72)
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


def get_inner_solution(constructor: PuzzleInfo, solution: Program) -> Program | None:
    return driver_lookup[AssetType(constructor.type())].get_inner_solution(constructor, solution)
```

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L33-72)
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

**File:** chia/_tests/wallet/vc_wallet/test_cr_outer_puzzle.py (L21-30)
```python
def test_cat_outer_puzzle() -> None:
    authorized_providers: list[bytes32] = [bytes32.zeros, bytes32.zeros]
    proofs_checker: Program = Program.NIL
    ACS: Program = Program.to(1)
    cr_puzzle: Program = construct_cr_layer(authorized_providers, proofs_checker, ACS)
    double_cr_puzzle: Program = construct_cr_layer(authorized_providers, proofs_checker, cr_puzzle)
    uncurried_cr_puzzle = UnknownPuzzle(known_program=double_cr_puzzle)
    cr_driver: PuzzleInfo | None = match_puzzle(uncurried_cr_puzzle)

    assert cr_driver is not None
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

**File:** chia/_tests/core/mempool/test_mempool.py (L2620-2634)
```python
# the tests below are malicious generator programs

# this program:
# (mod (A B)
#  (defun large_string (V N)
#    (if N (large_string (concat V V) (- N 1)) V)
#  )
#  (defun iter (V N)
#    (if N (c V (iter V (- N 1))) ())
#  )
#  (iter (c (q . 83) (c (concat (large_string 0x00 A) (q . 100)) ())) B)
# )
# with A=28 and B specified as {num}

SINGLE_ARG_INT_COND = "(a (q 2 4 (c 2 (c (c (q . {opcode}) (c (concat (a 6 (c 2 (c (q . {filler}) (c 5 ())))) (q . {val})) ())) (c 11 ())))) (c (q (a (i 11 (q 4 5 (a 4 (c 2 (c 5 (c (- 11 (q . 1)) ()))))) ()) 1) 2 (i 11 (q 2 6 (c 2 (c (concat 5 5) (c (- 11 (q . 1)) ())))) (q . 5)) 1) (q 28 {num})))"  # ruff: ignore[line-too-long]
```

**File:** chia/wallet/trading/offer.py (L29-36)
```python
from chia.wallet.outer_puzzles import (
    construct_puzzle,
    create_asset_id,
    get_inner_puzzle,
    get_inner_solution,
    match_puzzle,
    solve_puzzle,
)
```

**File:** chia/_tests/core/data_layer/test_data_store.py (L1726-1765)
```python
@pytest.mark.anyio
@pytest.mark.parametrize("depth", [1000, 100_000])
async def test_insert_into_data_store_from_file_line_graph_depth(
    data_store: DataStore,
    store_id: bytes32,
    tmp_path: Path,
    depth: int,
) -> None:
    # Binary "line graph":
    #   1 -> leaf_1, 2
    #   2 -> leaf_2, 3
    #   ...
    #   depth -> leaf_depth, leaf_end
    #
    # This stresses depth/stack handling while staying strictly binary.
    #
    # Node IDs:
    # - internal nodes: 1..depth
    # - per-level leaf nodes: (depth+1)..(2*depth)
    # - final leaf: (2*depth+1)
    internal_first = 1
    internal_last = depth
    leaf_base = internal_last + 1
    leaf_end = (2 * depth) + 1

    edges: list[tuple[int, int]] = []
    for i in range(internal_first, internal_last):
        # Order matters: first edge is left child, second edge is right child.
        edges.append((i, leaf_base + (i - internal_first)))  # unique leaf for this internal node
        edges.append((i, i + 1))  # next internal node

    # last internal points to its leaf and a final leaf
    edges.append((internal_last, leaf_base + (internal_last - internal_first)))
    edges.append((internal_last, leaf_end))

    filename = tmp_path / f"line_graph_depth_{depth}.dat"
    root_hash = create_graph_util(filename, edges)

    with pytest.raises(chia_rs.datalayer.RecursionDepthExceededError):
        await data_store.insert_into_data_store_from_file(store_id, root_hash, filename)
```
