### Title
Unbounded Python recursion in wallet outer-puzzle driver matching allows a spend-triggered stack-overflow crash - ([File: chia/wallet/outer_puzzles.py])

### Summary
The LibSass CVE is a stack-consumption DoS caused by unbounded recursive parsing of attacker-controlled input with no depth limit. The closest structural analog in this codebase is the wallet's "outer puzzle" driver matching logic in `chia/wallet/outer_puzzles.py`, `chia/wallet/cat_wallet/cat_outer_puzzle.py`, `chia/wallet/nft_wallet/ownership_outer_puzzle.py`, `chia/wallet/nft_wallet/metadata_outer_puzzle.py`, and `chia/wallet/vc_wallet/cr_outer_puzzle.py`. Each driver's `.match()` method recurses into `match_puzzle()` for the "inner puzzle" it just unwrapped, with no bound on nesting depth.

### Finding Description
`match_puzzle()` [1](#0-0)  iterates the registered drivers and calls `driver.match(puzzle)`. Drivers such as `CATOuterPuzzle.match()` [2](#0-1) , `OwnershipOuterPuzzle.match()` [3](#0-2) , `MetadataOuterPuzzle.match()` [4](#0-3) , and `CROuterPuzzle.match()` [5](#0-4)  all call `self._match(UnknownPuzzle(known_program=inner_puzzle))`, which is bound to `match_puzzle` itself, recursing one Python stack frame per curried layer. There is no depth cap, and this codebase has no `sys.setrecursionlimit` adjustment or iterative rewrite for this code path (unlike `sha256_treehash`, which was deliberately made non-recursive specifically to avoid "blowing out the python stack" [6](#0-5) ). A puzzle can be constructed as CAT-wrapped-in-CAT-wrapped-in-CAT (or ownership/CR layers nested similarly) for an arbitrary number of layers; each additional layer costs only a cheap `curry()` operation, so thousands of layers are attainable well within normal transaction size/cost limits, comfortably exceeding CPython's default recursion limit (1000).

This code path is reachable in two ways available to attackers without special privileges:
- **Offer parsing**: `chia/wallet/trading/offer.py` imports and calls `match_puzzle` (and `get_inner_puzzle`/`get_inner_solution`, which have the same recursive "also" chain pattern) while parsing an untrusted offer file/spend bundle presented by a counterparty [7](#0-6) .
- **Wallet coin-state sync**: `CATWallet.coin_added()`/`CATWallet.identify()` call `match_cat_puzzle`/driver matching when a coin is received from any party, including via untrusted sync [8](#0-7) .

### Impact Explanation
An uncaught `RecursionError` in these code paths would abort the calling coroutine, potentially disrupting the wallet's offer-acceptance flow or the untrusted sync coin-processing loop — a spend/offer-triggered transaction-processing halt on the victim wallet, matching the accepted "spend-triggered transaction-processing halt" impact class. This is a wallet-local availability issue (crash/exception), not a consensus-breaking coin-movement or supply-inflation bug, and it does not affect full-node block/mempool validation (which is implemented in Rust/`chia_rs` and is not subject to CPython's recursion limit).

### Likelihood Explanation
Moderate-to-low. Building a deeply nested CAT/ownership/CR-wrapped puzzle that a victim will actually process (via an accepted offer or an incoming coin sent to their address) is straightforward CLVM construction, but it requires the attacker to get the victim to load/parse that specific offer or receive that specific coin. Unlike the original LibSass CVE (any attacker-supplied Sass file triggers it via the compiler's own recursive-descent parser during normal compilation), here the recursion is confined to optional/local wallet-side "recognize this puzzle as a known asset type" logic, not the mandatory consensus validation path, and does not appear to have any built-in depth limiting or defensive catch of `RecursionError`.

### Recommendation
Add an explicit depth limit (and/or catch `RecursionError`, converting it to a graceful "unrecognized puzzle" result) in `match_puzzle()`/`get_inner_puzzle()`/`get_inner_solution()` and each driver's recursive `also`-chain handling, or rewrite the traversal iteratively similar to `sha256_treehash`. Apply the same bound before processing untrusted offers in `chia/wallet/trading/offer.py` and before running driver matching on puzzles surfaced during untrusted coin-state sync in `cat_wallet.py`/`wallet_state_manager.py`.

### Proof of Concept
Construct a puzzle `P` = CAT(tail, CAT(tail, CAT(tail, ... ACS ...)))` nested N times (e.g., N = 5000) using `construct_cat_puzzle`/`CAT_MOD.curry()` repeatedly (as shown in the existing test `test_cat_outer_puzzle` which nests two CAT layers) [9](#0-8) . Present this puzzle either as an outer puzzle reveal in an offer, or as the puzzle for a coin sent to a victim wallet's address. When the victim's wallet calls `match_puzzle(UnknownPuzzle(known_program=P))` during offer parsing or coin-state sync, it recurses once per CAT layer via `CATOuterPuzzle.match` → `self._match` → `match_puzzle`, raising `RecursionError` once N exceeds the interpreter's recursion limit.

**Note on confidence**: This is an analog constructed from bug-class similarity (unbounded recursive parsing/matching of attacker-influenced structure with no depth cap), not a confirmed exploit chain reachable through consensus-critical mempool/block validation (which uses the Rust `chia_rs` CLVM engine and is not vulnerable to this class of Python-stack exhaustion). I was not able to fully trace whether callers of `Offer` parsing or `coin_added` wrap this logic in broad exception handlers that would downgrade the crash to a caught error rather than a genuine processing halt — this should be verified with a live Devin session if a definitive proof-of-concept is required.

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

**File:** chia/wallet/cat_wallet/cat_wallet.py (L371-404)
```python
    async def coin_added(
        self, coin: Coin, height: uint32, peer: WSChiaConnection, coin_data: object | None, sync_scope: WalletSyncScope
    ) -> None:
        """Notification from wallet state manager that wallet has been received."""
        self.log.info(f"CAT wallet has been notified that {coin.name().hex()} was added")
        if coin_data is not None:
            assert isinstance(coin_data, CATCoinData)

        inner_puzzle = await self.inner_puzzle_for_cat_puzhash(coin.puzzle_hash)
        lineage_proof = LineageProof(coin.parent_coin_info, inner_puzzle.get_tree_hash(), uint64(coin.amount))
        await self.add_lineage(coin.name(), lineage_proof)

        lineage = await self.get_lineage_proof_for_coin(coin)

        if lineage is None:
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
```

**File:** chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py (L17-26)
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
```
