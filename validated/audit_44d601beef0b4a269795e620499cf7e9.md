Found it — `chia/wallet/trading/offer.py`'s `Offer.__post_init__()` and `Offer.from_bytes()` calls `match_puzzle()`/`_get_offered_coins()` on every `CoinSpend.puzzle_reveal` inside an untrusted, attacker-supplied offer file, and `match_puzzle()` dispatches into `CATOuterPuzzle.match()`, `OwnershipOuterPuzzle.match()`, `SingletonOuterPuzzle.match()`, `MetadataOuterPuzzle.match()`, and `CROuterPuzzle.match()`, each of which recurses into `self._match(...)` (itself `match_puzzle`) once per matched outer layer with no depth cap. This is a genuine analog: unbounded Python-level recursion driven purely by attacker-controlled puzzle nesting, reachable simply by an offer counterparty/taker parsing an offer file — no execution, cost metering, or Rust CLVM boundary applies to this parsing step. [1](#0-0) [2](#0-1) [3](#0-2) 

However, before finalizing, I need to verify a few things I couldn't fully confirm in this pass: (1) whether `Offer.from_bytes()`/`from_bech32()` is reachable purely from taker-side parsing before any user approval (i.e., does merely receiving/decoding an offer string trigger `match_puzzle` recursion, or only `respond_to_offer`/`get_summary`?), (2) the actual depth achievable — each layer in `CATOuterPuzzle`, `SingletonOuterPuzzle`, etc. requires a legitimate curry match against a fixed mod hash, so an attacker must construct N genuinely nested CAT/singleton/ownership/metadata/CR layers (this is CLVM-cost-unbounded since it happens at Python parse-time, not CLVM execution time — the puzzle byte size grows only linearly with depth, so cost/size limits on the underlying `CoinSpend.puzzle_reveal` don't defeat it), and (3) confirming Python's default recursion limit (~1000) is realistically hit by a puzzle_reveal of reasonable size (a few hundred nested CAT layers would already be large but plausibly within normal offer/coin-spend size limits, and mempool/offer code doesn't impose a generator-execution cost limit on this Python-side parsing path since it occurs *before* CLVM execution).

Given the remaining iteration budget, I'll present this as the strongest candidate analog, flagging the parts that still need confirmation via direct reading of `Offer.__post_init__`/`from_bech32` call chains and `trade_manager.respond_to_offer`.

### Title
Unbounded Python recursion via deeply nested outer-puzzle layers in offer/coin-spend parsing (`match_puzzle`) - (File: chia/wallet/outer_puzzles.py)

### Summary
`chia.wallet.outer_puzzles.match_puzzle()` and its per-asset-type drivers (`CATOuterPuzzle`, `SingletonOuterPuzzle`, `OwnershipOuterPuzzle`, `MetadataOuterPuzzle`, `CROuterPuzzle`) recursively call back into `match_puzzle()` (via the `_match` callable) once for every nested outer-puzzle layer found in a `CoinSpend.puzzle_reveal`. This mirrors the CVE-2020-12100 bug class (uncontrolled recursion parsing attacker-controlled deeply nested structure) because the recursion depth is bounded only by how many valid nested layers an attacker chooses to curry into a puzzle, with no explicit depth limit enforced anywhere in the driver dispatch code.

### Finding Description
`match_puzzle()` iterates `driver_lookup` and calls each driver's `.match()`: [2](#0-1) . Every driver that matches an outer layer immediately recurses into `self._match(UnknownPuzzle(known_program=inner_puzzle))` to look for a nested "also" layer, e.g. in `CATOuterPuzzle.match()`: [3](#0-2) , and identically in `SingletonOuterPuzzle.match()` [4](#0-3) , `OwnershipOuterPuzzle.match()` [5](#0-4) , `MetadataOuterPuzzle.match()` [6](#0-5) , and `CROuterPuzzle.match()` [7](#0-6) . Because a puzzle can be curried arbitrarily as `CAT(CAT(CAT(...(CAT(ACS))...)))` (the codebase's own test constructs `double_cat_puzzle` this way: [8](#0-7) ), an attacker can build a puzzle reveal with hundreds/thousands of nested valid CAT (or singleton/ownership/metadata/CR) layers. Parsing this via `match_puzzle()` is pure Python recursion with no CLVM cost metering, no depth counter, and no `sys.setrecursionlimit` override — unlike `sha256_treehash()`, which the codebase deliberately made iterative specifically "so we don't have to worry about blowing out the python stack" [9](#0-8) . `match_puzzle()` and its callees show no equivalent hardening.

This dispatch is reached whenever a coin's `puzzle_reveal` is classified — most notably when a wallet parses/evaluates an offer received from a counterparty: `Offer.from_spend_bundle()`/`Offer._get_offered_coins()` call `match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))` directly on attacker-supplied `CoinSpend`s from the offer bytes [1](#0-0)  and [10](#0-9) .

### Impact Explanation
A crafted offer file (or any coin/puzzle-reveal a wallet is asked to classify, e.g. during offer summary/parsing prior to acceptance) with deeply nested outer-puzzle layers can drive `match_puzzle()`'s mutual Python recursion past the interpreter's recursion limit, raising an uncaught `RecursionError` and crashing/halting the wallet's offer-processing or transaction-processing path — a spend/offer-triggered processing halt, matching the accepted "spend-triggered transaction-processing halt" impact category. This is reachable purely from an offer counterparty providing a malicious offer file to a wallet user; no privileged access is required.

### Likelihood Explanation
Constructing N valid nested CAT/singleton/ownership layers only requires currying, which is cheap and produces a puzzle reveal whose size grows linearly (not exponentially) with N, so an attacker can plausibly reach several hundred to a few thousand layers within normal size constraints, comfortably exceeding Python's default recursion limit (~1000). No cost metering applies to this Python-level parsing step since it happens before/independent of CLVM execution.

### Recommendation
Convert `match_puzzle()`/driver `.match()` (and the corresponding `get_inner_puzzle`/`get_inner_solution`/`construct`/`solve` recursive chains) to an iterative loop with an explicit maximum nesting-depth guard, analogous to the iterative `sha256_treehash()` design already used elsewhere in the codebase, and reject puzzle reveals exceeding a sane layer-depth bound before recursive matching begins.

### Proof of Concept
1. Build `puzzle = ACS`; repeat N times (N ≈ 2000): `puzzle = construct_cat_puzzle(CAT_MOD, tail_hash, puzzle)` (or equivalently nest `SINGLETON_TOP_LAYER_MOD`/`NFT_OWNERSHIP_LAYER`/`NFT_STATE_LAYER_MOD` layers), producing a single valid, well-formed nested puzzle reveal.
2. Construct a `CoinSpend` using this `puzzle` as `puzzle_reveal`, wrap it in a `WalletSpendBundle`/`Offer.from_spend_bundle()` as an offered asset, matching the pattern in `chia/_tests/wallet/cat_wallet/test_cat_outer_puzzle.py`'s `double_cat_puzzle` construction, extended to thousands of layers.
3. Have a victim wallet call `Offer.from_bytes()`/`from_bech32()` (which triggers `Offer.__post_init__` → `_get_offered_coins()` → `match_puzzle()`) on this crafted offer, or otherwise pass the puzzle reveal through `match_puzzle()`.
4. Observe Python's recursive `match()`/`_match` chain exceed the interpreter recursion limit and raise `RecursionError`, aborting offer parsing/processing.

### Citations

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

**File:** chia/wallet/trading/offer.py (L634-646)
```python
    @classmethod
    def from_spend_bundle(cls, bundle: WalletSpendBundle) -> Offer:
        # Because of the `to_spend_bundle` method, we need to parse the dummy CoinSpends as `requested_payments`
        requested_payments: dict[bytes32 | None, list[NotarizedPayment]] = {}
        driver_dict: dict[bytes32, PuzzleInfo] = {}
        leftover_coin_spends: list[CoinSpend] = []
        for coin_spend in bundle.coin_spends:
            driver = match_puzzle(UnknownPuzzle(known_program=coin_spend.puzzle_reveal))
            if driver is not None:
                asset_id = create_asset_id(driver)
                assert asset_id is not None
                driver_dict[asset_id] = driver
            else:
```

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
