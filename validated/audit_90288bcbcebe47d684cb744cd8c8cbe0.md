### Title
Unbounded recursion in outer-puzzle driver matching from attacker-crafted offer/coin data causes wallet-side stack overflow DoS - ([File: chia/wallet/outer_puzzles.py])

### Summary
The GPAC CVE describes a stack overflow from unbounded recursive tree traversal (`gf_node_get_name`) over attacker-controlled input, crashing the process. Chia's wallet has an analogous unbounded-recursion pattern: `match_puzzle`/`get_inner_puzzle`/`get_inner_solution`/`construct_puzzle`/`solve_puzzle` recurse through nested "outer puzzle" layers (`also()` chains) with no depth limit, driven directly by untrusted, attacker-supplied CLVM puzzle reveals (offer counterparty puzzle reveals, or CAT/DID/NFT/VC coin spends fetched from a peer).

### Finding Description
`chia.wallet.outer_puzzles.match_puzzle` recursively calls each `DriverProtocol` implementation's `.match()`, which in turn calls `self._match(...)` on the next inner puzzle layer whenever the outer layer matches (CAT, singleton, metadata, ownership, credential-restricted, etc.) [1](#0-0) . Each of the concrete drivers (e.g. `CATOuterPuzzle.match`, `CROuterPuzzle.match`, `SingletonOuterPuzzle.match`, `OwnershipOuterPuzzle.match`, `MetadataOuterPuzzle.match`) unconditionally recurses into `self._match(UnknownPuzzle(known_program=inner_puzzle))` for every curried layer found, with no bound on nesting depth [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) .

The same recursive pattern exists in `get_inner_puzzle`, `get_inner_solution`, `construct`, and `solve` on each driver, which call back into the module-level dispatch functions (`get_inner_puzzle`, `get_inner_solution`, `construct_puzzle`, `solve_puzzle`) for every "also" layer [6](#0-5) . There is no `sys.setrecursionlimit` guard and no explicit depth cap anywhere in this driver dispatch code (confirmed no matches for `RecursionError`/`setrecursionlimit` in the repo), unlike `chia/types/blockchain_format/tree_hash.py`, which explicitly documents and implements an iterative, non-recursive tree-hash algorithm specifically "so we don't have to worry about blowing out the python stack" [7](#0-6) .

This recursive matching is invoked directly on attacker-controlled data:
- `Offer` objects import and use `match_puzzle`, `get_inner_puzzle`, `get_inner_solution`, `construct_puzzle`, and `solve_puzzle` from `chia.wallet.outer_puzzles` to parse a counterparty-supplied offer's `driver_dict`/puzzle reveals [8](#0-7) .
- `WalletStateManager.determine_coin_type` calls `match_cat_puzzle` and constructs `UnknownPuzzle(known_program=coin_spend.puzzle_reveal)` from a coin spend fetched from a (possibly malicious) full-node peer during normal sync, feeding directly into this uncurry/match machinery [9](#0-8) .

Because a spend bundle submitter (or offer creator) fully controls the CLVM puzzle reveal bytes, they can construct a puzzle with an arbitrarily long chain of nested CAT/singleton/metadata/ownership/CR layers (each layer is cheap to construct via `curry`), each of which triggers one additional Python recursion frame in `match`/`get_inner_puzzle`/etc. when the wallet later parses that puzzle (via an offer, or via `wallet_state_manager` syncing a coin created by such a spend). This is directly analogous to the GPAC bug class: unbounded recursive traversal of an untrusted, attacker-influenced tree/graph structure without a depth limit, leading to a Python `RecursionError`/native stack exhaustion.

### Impact Explanation
An attacker can craft a malicious offer file or coin spend with deeply nested curried outer-puzzle layers. When a victim wallet parses this offer (`Offer.from_bytes`/driver_dict resolution) or syncs a coin created from such a spend, the recursive driver-matching functions will recurse once per nesting layer. With enough nesting (bounded only by mempool/serialization limits, not by wallet-side logic), this exceeds Python's default recursion limit and raises an uncaught `RecursionError`, crashing/halting wallet transaction processing for that offer/sync operation — a spend/offer-triggered denial of service against wallet clients, matching the "no low/resource-only" bar because it produces a hard exception that halts processing rather than merely consuming extra resources.

### Likelihood Explanation
Medium. Constructing a puzzle reveal with hundreds of nested CAT/singleton/ownership/metadata/CR layers is cheap and requires no special privileges — any spend-bundle submitter or offer counterparty can do it, since these are all standard, documented outer-puzzle constructions (`construct_cat_puzzle`, `puzzle_for_singleton`, `construct_cr_layer`, `puzzle_for_ownership_layer`, `puzzle_for_metadata_layer`). The trigger paths (accepting/inspecting an offer, or syncing a peer-supplied coin spend) are routine wallet operations reachable by an unprivileged counterparty or malicious/compromised full-node peer that a wallet is connected to.

### Recommendation
Add an explicit recursion/nesting depth limit to `match_puzzle`, `get_inner_puzzle`, `get_inner_solution`, `construct_puzzle`, and `solve_puzzle` in `chia/wallet/outer_puzzles.py` and each driver implementation (CAT, singleton, ownership, metadata, CR, revocation), raising a clean `ValueError` (or similar) once a maximum outer-layer depth (e.g. 16–32) is exceeded, rather than letting Python recursion overflow uncaught. Alternatively, convert the `also()`-chain traversal to an iterative loop (similar to the existing non-recursive `sha256_treehash` implementation) to remove the stack-depth dependency entirely.

### Proof of Concept
Conceptual construction (not run, for illustration):
1. Start with `inner = Program.to(1)` (any valid ACS puzzle).
2. Repeat N times (e.g. N = 2000): `inner = construct_cat_puzzle(CAT_MOD, tail_hash, inner)` (or alternate with `puzzle_for_singleton`, `construct_cr_layer`, etc., mixing layer types), producing a puzzle reveal with N nested outer-puzzle layers.
3. Wrap this puzzle reveal in a `CoinSpend`/`Offer` and have the victim wallet call `match_puzzle(UnknownPuzzle(known_program=inner))` (as `Offer` parsing or `wallet_state_manager.determine_coin_type` does).
4. Each layer triggers one recursive call in `CATOuterPuzzle.match`/`CROuterPuzzle.match`, exceeding Python's default recursion limit (~1000) and raising an uncaught `RecursionError`, aborting offer parsing or coin-sync processing in the wallet.

**Uncertainty note:** I could not execute this PoC or confirm the exact recursion depth threshold or whether any upstream caller wraps this in a broad `try/except Exception` that would downgrade the crash to a caught error rather than a hard failure (e.g., `CATWallet.coin_added` catches generic `Exception` around some of this logic, per [10](#0-9) , which could reduce impact for that specific call path to logged failure rather than crash — but other call paths such as `Offer` parsing/`accept_offer` do not appear to have such a broad exception guard around `match_puzzle`). A full assessment would require tracing all call sites of `match_puzzle`/`get_inner_puzzle` to confirm which are unguarded against `RecursionError`.

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

**File:** chia/wallet/cat_wallet/cat_outer_puzzle.py (L47-72)
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

    def get_inner_solution(self, constructor: PuzzleInfo, solution: Program) -> Program | None:
        my_inner_solution: Program = solution.first()
        also = constructor.also()
        if also:
            deep_inner_solution: Program | None = self._get_inner_solution(also, my_inner_solution)
            return deep_inner_solution
        else:
            return my_inner_solution
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

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```

**File:** chia/wallet/trading/offer.py (L29-38)
```python
from chia.wallet.outer_puzzles import (
    construct_puzzle,
    create_asset_id,
    get_inner_puzzle,
    get_inner_solution,
    match_puzzle,
    solve_puzzle,
)
from chia.wallet.puzzle_drivers import PuzzleInfo, Solver
from chia.wallet.puzzles.puzzle_drivers import UnknownPuzzle
```

**File:** chia/wallet/wallet_state_manager.py (L937-944)
```python
        coin_spend = await fetch_coin_spend_for_coin_state(parent_coin_state, peer)

        uncurried = UnknownPuzzle(known_program=coin_spend.puzzle_reveal)

        # Check if the coin is a CAT
        cat_curried_args = match_cat_puzzle(uncurried)
        if cat_curried_args is not None:
            cat_mod_hash, tail_program_hash, cat_inner_puzzle = cat_curried_args
```

**File:** chia/wallet/cat_wallet/cat_wallet.py (L386-406)
```python
            try:
                if coin_data is None:
                    # The method is not triggered after the determine_coin_type, no pre-fetched data
                    coin_state = await self.wallet_state_manager.wallet_node.get_coin_state(
                        [coin.parent_coin_info], peer=peer
                    )
                    assert coin_state[0].coin.name() == coin.parent_coin_info
                    coin_spend = await fetch_coin_spend_for_coin_state(coin_state[0], peer)
                    cat_curried_args = match_cat_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
                    if cat_curried_args is not None:
                        cat_mod_hash, tail_program_hash, cat_inner_puzzle = cat_curried_args
                        coin_data = CATCoinData(
                            bytes32(cat_mod_hash.as_atom()),
                            bytes32(tail_program_hash.as_atom()),
                            cat_inner_puzzle,
                            coin_state[0].coin.parent_coin_info,
                            uint64(coin_state[0].coin.amount),
                        )
                await self.puzzle_solution_received(coin, coin_data)
            except Exception as e:
                self.log.debug(f"Exception: {e}, traceback: {traceback.format_exc()}")
```
