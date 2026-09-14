## Analysis

CVE‑2017‑15024 is a binutils bug where deeply nested/self-referential DWARF debug info causes unbounded recursion in `find_abstract_instance_name`, crashing the tool on a crafted file. The applicable bug class here is: **unbounded native-language recursion driven by attacker-controlled nested structure, with no depth limit, reachable by simply parsing/inspecting a crafted input.**

Chia's wallet puzzle-driver "outer puzzle" matching system has the same shape of bug, reachable by any offer counterparty.

`match_puzzle()` in [1](#0-0)  is bound as `_match` into every outer-puzzle driver (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `OwnershipOuterPuzzle`, `MetadataOuterPuzzle`, `CROuterPuzzle`) via `function_args` [2](#0-1) . Each driver's `match()` method recursively calls `self._match(UnknownPuzzle(known_program=inner_puzzle))` on the layer's inner puzzle to build the `"also"` chain, e.g. in `CATOuterPuzzle.match()` [3](#0-2) , `SingletonOuterPuzzle.match()` [4](#0-3) , `OwnershipOuterPuzzle.match()` [5](#0-4) , `MetadataOuterPuzzle.match()` [6](#0-5) , and `CROuterPuzzle.match()` [7](#0-6) . This recursion is native Python call-stack recursion with no depth cap and is not gated by CLVM cost (it operates on the raw `SExp`/curry shape, not by running the program), unlike the deliberately iterative `sha256_treehash()` in [8](#0-7)  which was specifically written non-recursively "so we don't have to worry about blowing out the python stack."

`match_puzzle()` is invoked directly on attacker-supplied `puzzle_reveal` bytes taken from an untrusted `Offer`/`SpendBundle`, e.g. `Offer._get_offered_coins()` [9](#0-8)  and `Offer.from_spend_bundle()` [10](#0-9) . These are exercised whenever a wallet user inspects, aggregates, or accepts an offer file, which is a routine unprivileged action taken on data supplied by an offer counterparty.

Because each outer layer (e.g. a CAT layer wrapped in another CAT layer, or singleton/ownership/metadata/CR layers) only needs to satisfy the fixed curry-mod pattern that `match_cat_puzzle`, `match_singleton_puzzle`, etc. check syntactically, an attacker can construct a puzzle reveal with a very large number of nested "also" layers without ever needing it to be a valid, executable, or spendable program. Presenting such an offer to a victim wallet (or otherwise getting the wallet to `match_puzzle()` a crafted coin spend) drives Python recursion depth linearly with the number of crafted layers, eventually raising `RecursionError` and crashing/halting the wallet's offer/transaction processing — the same "crafted input causes infinite/unbounded recursion → crash" bug class as CVE-2017-15024, but reached through wallet offer parsing rather than ELF debug-info parsing.

### Title
Unbounded recursive puzzle-driver matching (`match_puzzle`/outer-puzzle `.match()`) allows a crafted offer puzzle reveal to crash the wallet via Python recursion exhaustion - (File: chia/wallet/outer_puzzles.py)

### Summary
`match_puzzle()` and each outer-puzzle driver's `match()` method recurse over nested puzzle layers with no depth limit, driven purely by attacker-controlled curry structure in a `puzzle_reveal`. An offer counterparty can supply a coin spend whose puzzle reveal has many nested CAT/singleton/ownership/metadata/CR layers to blow the Python call stack when the victim wallet inspects the offer.

### Finding Description
`match_puzzle()` [1](#0-0)  is bound as the `_match` callback into `CATOuterPuzzle`, `SingletonOuterPuzzle`, `OwnershipOuterPuzzle`, `MetadataOuterPuzzle`, and `CROuterPuzzle` [2](#0-1) . Each of these driver `.match()` implementations recursively invokes `self._match(UnknownPuzzle(known_program=inner_puzzle))` on the next inner layer to populate the `"also"` chain, e.g. [11](#0-10) . There is no maximum recursion/layer-count check anywhere in this chain, unlike `sha256_treehash()`, which was deliberately made iterative for this exact reason [8](#0-7) .

`match_puzzle()` runs on raw, unexecuted `puzzle_reveal` bytes — it is a syntactic curry-shape check, not a CLVM run, so it is not bounded by CLVM cost metering. An attacker only needs the bytes to satisfy the fixed curry template checked by `match_cat_puzzle`/`match_singleton_puzzle`/etc. at each layer; the puzzle need not be valid, spendable, or ever executed.

This function is reachable directly on attacker-supplied offer data: `Offer._get_offered_coins()` calls `match_puzzle(parent_puzzle)` on each `parent_spend.puzzle_reveal` taken from the bundled `CoinSpend`s [12](#0-11) , and `Offer.from_spend_bundle()` does the same while parsing an incoming offer [10](#0-9) . Both paths are exercised when a wallet user views, summarizes, aggregates, or accepts an offer sent by a counterparty.

### Impact Explanation
An offer counterparty who is otherwise unprivileged can hand a victim wallet an offer file containing a coin spend whose `puzzle_reveal` has a deep chain of nested "also" layers. When the victim wallet inspects the offer (summary, validity check, accept flow), the recursive `match()` chain will recurse once per crafted layer with no bound, eventually hitting Python's recursion limit and raising `RecursionError`, crashing/halting the wallet's request-processing thread. This is a spend/offer-triggered transaction-processing halt matching the "no-availability" impact class accepted by this analysis (denial of service against wallet offer handling), analogous to the crafted-ELF-triggered infinite recursion crash in the source CVE.

### Likelihood Explanation
Likelihood is fairly high for anyone who can send offer files or spend bundles to a target wallet (offer counterparties, or peers relaying spend bundles containing crafted puzzle reveals that the wallet later examines via `match_puzzle`). Constructing a chain of syntactically-valid nested curry layers requires no cryptographic material and no signature — the driver `.match()` calls only check curry mod-hash shape, not validity of the whole program, so the required puzzle_reveal bytes are cheap and deterministic to construct.

### Recommendation
Add an explicit maximum layer/recursion-depth bound to `match_puzzle()`/the outer-puzzle driver chain (e.g., a depth counter threaded through `_match`/`.also()` construction that raises/returns `None` after a fixed limit), or rewrite the layer-matching loop to be iterative rather than recursive, mirroring the non-recursive design already used in `sha256_treehash()`.

### Proof of Concept
Construct a `CoinSpend` whose `puzzle_reveal` is `construct_cat_puzzle(CAT_MOD, tail, construct_cat_puzzle(CAT_MOD, tail, construct_cat_puzzle(CAT_MOD, tail, ...)))` nested to a depth well beyond Python's default recursion limit (e.g., tens of thousands of layers), matching the pattern already exercised (at small scale) in [13](#0-12) . Package this `CoinSpend` into an `Offer`/`SpendBundle` and have a victim wallet call `offer.summary()`/`offer._get_offered_coins()` (or otherwise trigger `match_puzzle()` on the reveal) to observe a `RecursionError` crash instead of a graceful rejection.

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

**File:** chia/wallet/outer_puzzles.py (L79-88)
```python
function_args = (match_puzzle, construct_puzzle, solve_puzzle, get_inner_puzzle, get_inner_solution)

driver_lookup: dict[AssetType, DriverProtocol] = {
    AssetType.CAT: CATOuterPuzzle(*function_args),
    AssetType.SINGLETON: SingletonOuterPuzzle(*function_args),
    AssetType.METADATA: MetadataOuterPuzzle(*function_args),
    AssetType.OWNERSHIP: OwnershipOuterPuzzle(*function_args),
    AssetType.ROYALTY_TRANSFER_PROGRAM: TransferProgramPuzzle(*function_args),
    AssetType.CR: CROuterPuzzle(*function_args),
    AssetType.REVOCATION_LAYER: RevocationOuterPuzzle(),
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

**File:** chia/wallet/trading/offer.py (L248-256)
```python
        for parent_spend in self._bundle.coin_spends:
            coins_for_this_spend: list[Coin] = []

            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
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

**File:** chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py (L17-23)
```python
def test_cat_outer_puzzle() -> None:
    ACS = Program.to(1)
    tail = bytes32.zeros
    cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, ACS)
    double_cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, tail, cat_puzzle)
    uncurried_cat_puzzle = UnknownPuzzle(known_program=double_cat_puzzle)
    cat_driver: PuzzleInfo | None = match_puzzle(uncurried_cat_puzzle)
```
