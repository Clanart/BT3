### Title
Unbounded Python recursion in outer-puzzle driver matching allows an attacker-crafted coin/offer puzzle to crash a wallet's sync/offer-processing thread - (File: chia/wallet/outer_puzzles.py)

### Summary
`chia/wallet/outer_puzzles.py`'s `match_puzzle()`, together with the per-layer `match()` implementations in `CATOuterPuzzle`, `SingletonOuterPuzzle`, `MetadataOuterPuzzle`, `OwnershipOuterPuzzle`, and `CROuterPuzzle`, recursively descends into the `also` chain of an untrusted puzzle reveal with no depth limit, no cost metering, and no exception handling for Python's native recursion limit. Because this recursion depth is fully attacker-controlled by nesting curried "outer puzzle" layers (CAT/singleton/metadata/ownership/CR wrappers), a single malicious coin, offer, or CoinSpend that a wallet processes can trigger a `RecursionError`/stack exhaustion, halting wallet-side transaction/offer processing — a Python-side analog to the GitLab RDoc DoS, where crafted structured input drove unbounded recursive rendering with no depth guard.

### Finding Description
`match_puzzle()` walks a fixed dictionary of drivers and calls `driver.match(puzzle)` for the outermost puzzle layer: [1](#0-0) 

Each driver's `match()` method, upon a successful single-layer match, recurses into the puzzle's inner layer via `self._match(UnknownPuzzle(known_program=inner_puzzle))`, which is `match_puzzle` again. This pattern repeats identically across all outer-puzzle drivers, e.g. CAT: [2](#0-1) 

ownership layer: [3](#0-2) 

singleton layer: [4](#0-3) 

and credential-restricted layer: [5](#0-4) 

Each recursive step performs a real CLVM `uncurry_puzzle()` call (via `UnknownPuzzle.mod`/`curried_args` in `chia/wallet/puzzles/puzzle_drivers.py`) but the recursion itself is plain Python function-call recursion with **no depth cap, no total-time budget, and no cost accounting** — unlike CLVM program execution, which is bounded by `INFINITE_COST`/mempool max-cost limits. An attacker can construct a puzzle reveal that nests thousands of recognized outer-puzzle mod shapes (e.g., alternating CAT/ownership/CR curry wrappers, each individually cheap and each recognized by `match_cat_puzzle`/`match_ownership_layer_puzzle`/`match_cr_layer`), producing a `Program` whose byte size is modest but whose `also`-chain depth is very large. There is no check anywhere in the codebase (`grep` for `RecursionError`, `sys.setrecursionlimit`, `MAX_*_DEPTH` in the repo returns no results) that bounds this recursion or gracefully handles Python's default recursion limit being exceeded.

This driver-matching path is reached from genuinely untrusted input: `chia/wallet/trading/offer.py` and `chia/wallet/trade_manager.py` call into `chia.wallet.outer_puzzles` to parse offer files and coin spends supplied by a trade counterparty, and `chia/wallet/wallet_state_manager.py` uses the same `UnknownPuzzle`/driver-matching machinery when determining the type of an incoming coin during sync (`determine_coin_type`), which is triggered by any coin sent to (or hinted to) the local wallet by an arbitrary peer.

### Impact Explanation
A `RecursionError` raised deep inside driver `match()`/`get_inner_puzzle()`/`construct()` calls is not specifically caught anywhere in this call chain. Depending on where the recursion bottoms out (inside offer-parsing during `take_offer`/`check_offer_validity`, or during wallet sync's coin-type determination), this can:
- Crash or hang the wallet process's async event loop task handling the offer or sync update, effectively halting further transaction processing for that wallet until restarted.
- Be triggered purely by receiving/being shown a malicious offer file or by having a malicious coin sent to (hinted at) the wallet — no cooperation or private-key exposure from the victim is required.

This matches the "spend-triggered transaction-processing halt" impact category permitted by the validation rules.

### Likelihood Explanation
Constructing deeply nested, individually-cheap curry layers that each satisfy one of `match_cat_puzzle`, `match_ownership_layer_puzzle`, `match_cr_layer`, or `match_singleton_puzzle` is straightforward chialisp/CLVM construction requiring no special privileges — only the ability to curry a puzzle with a chosen inner puzzle repeatedly and hand the result to a victim as an offer or a coin puzzle reveal. The main uncertainty is the exact number of nesting levels needed to exceed Python's default recursion limit (~1000) in this call stack, and whether any upstream size/curry-depth limit (e.g., block generator max cost/size when the coin must first be created on-chain) makes constructing a sufficiently deep, valid, on-chain coin impractical; I could not verify a concrete example that survives block/mempool cost limits versus the offer-file path, which does not require any on-chain spend at all and is likely the more directly reachable vector.

### Recommendation
Add an explicit depth/iteration limit and defensive exception handling to the recursive matching/traversal in `chia/wallet/outer_puzzles.py` and its cooperating driver `match()`/`get_inner_puzzle()`/`get_inner_solution()`/`construct()` methods, converting excessive nesting into a graceful "unrecognized puzzle" result (returning `None`) instead of unbounded Python recursion. Apply the same bound to `UncurriedNFT.uncurry()` and any other `also`-chain traversal (`chia/wallet/puzzle_drivers.py` `PuzzleInfo.also()`/`check_type()`) reachable from untrusted coin spends or offer files.

### Proof of Concept
Conceptual construction (not executed, so not independently verified against current recursion limits):
1. Build an innermost puzzle `ACS = Program.to(1)`.
2. Repeatedly wrap it thousands of times alternating `construct_cat_puzzle`, `puzzle_for_ownership_layer`, and `construct_cr_layer` (each individually matched by the corresponding `match_*` helper), producing `puzzle_N`.
3. Present `puzzle_N` either:
   - as the puzzle of an asset inside an offer file handed to a victim wallet (reaching `chia/wallet/trading/offer.py` → `outer_puzzles.match_puzzle`), or
   - as the puzzle reveal of a coin hinted to the victim's wallet, reaching `wallet_state_manager` sync logic that ultimately calls into the same recursive driver-matching code.
4. When the victim wallet calls `match_puzzle(UnknownPuzzle(known_program=puzzle_N))`, the recursive `self._match(...)` chain recurses `N` times in pure Python, exceeding the interpreter's recursion limit and raising an uncaught `RecursionError` inside wallet processing.

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
