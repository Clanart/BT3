### Title
Unbounded recursion in `PuzzleInfo`/outer-puzzle driver matching lets a crafted offer or coin spend cause a Python `RecursionError` abort during offer processing - ([File: chia/wallet/puzzle_drivers.py])

### Summary
Chia's wallet-side "outer puzzle driver" system reconstructs a `PuzzleInfo` description of a coin's puzzle by recursively unwrapping each CLVM curry layer (CAT → singleton → metadata → ownership → CR → revocation, etc.). Each layer's `match()` implementation calls back into the shared `match_puzzle()` dispatcher for its inner puzzle, and `PuzzleInfo.also()` / `PuzzleInfo.check_type()` recurse the same way over the resulting nested dict. None of these paths bound the recursion depth. An attacker who controls a coin spend's puzzle reveal (e.g., an offer counterparty, or any spend bundle a wallet is asked to inspect/accept) can nest puzzle layers arbitrarily deep, driving unbounded Python recursion and crashing the wallet process with an uncaught `RecursionError` when it parses the offer/spend. This mirrors the CVE-2016-4353 bug class: a decoder/parser that recurses once per nested element with no depth limit, causing a stack-overflow abort/DoS on attacker-supplied input.

### Finding Description
`PuzzleInfo` is the untrusted-input-facing wrapper used to describe puzzle "layers" reconstructed from real coin spends (offers, trade data, DL/NFT/CAT drivers): [1](#0-0) 

`also()` recurses to build a nested `PuzzleInfo` for however many `"also"` keys are chained, and `check_type()` recurses per layer with no depth cap.

The layer-matching dispatcher recurses through every registered outer-puzzle driver: [2](#0-1) 

Each concrete driver's `match()` calls back into `self._match` (bound to `match_puzzle`) on its own inner puzzle, building one more nested `also` layer per curried wrapper found in the puzzle reveal - e.g. singleton: [3](#0-2) 

ownership: [4](#0-3) 

and CAT: [5](#0-4) 

This chain of `match()`→`match_puzzle()`→`match()`... is driven entirely by how many CLVM curry layers are stacked in the attacker-supplied puzzle reveal; there is no limit on how many outer layers a puzzle can have, and no code anywhere in this call graph enforces a maximum nesting depth. This same recursive matching runs when an `Offer` is parsed from a spend bundle: [6](#0-5) 

The `_get_offered_coins()` path used to validate/inspect an offer similarly calls `match_puzzle` on every parent spend's puzzle reveal: [7](#0-6) 

Because CLVM programs and curry wrapping allow arbitrarily deep nesting relatively cheaply (each added layer is just another curry application around the puzzle), a malicious offer creator (or spend-bundle submitter that a wallet inspects, e.g. via `Offer.from_bech32` / `from_spend_bundle`, or `check_for_special_offer_making` / `check_for_final_modifications` which call `puzzle_info.also().also()[...]` chains) can construct a puzzle reveal with thousands of nested singleton/CAT/ownership/CR layers. When the victim wallet calls `match_puzzle()` (directly, or transitively through `Offer.from_bech32`, `_get_offered_coins`, `check_for_special_offer_making`, `check_for_final_modifications`, `maybe_create_wallets_for_offer`, etc.), Python's recursion limit will eventually be exceeded, raising an uncaught `RecursionError` that is not specifically handled by these code paths (only broad excepts wrap some outer creation flows, not the offer-taking/inspection flows), crashing or halting wallet transaction processing for that call — a spend-triggered processing halt directly analogous to libksba's BER-decoder stack-overflow abort.

### Impact Explanation
This is reachable by an unprivileged offer counterparty: simply publishing/sending a crafted offer file (bech32 offer or raw spend bundle) with deeply nested puzzle layers to a victim wallet is sufficient to trigger unbounded recursion during `Offer.from_bech32`/`from_spend_bundle` parsing or later validation (`_get_offered_coins`, `check_offer_validity`→`calculate_tx_records_for_offer`, `check_for_special_offer_making`, `check_for_final_modifications`). A `RecursionError` propagating out of these RPC-invoked wallet methods (offer take/inspect endpoints) can abort request handling and, depending on how deep the interpreter stack corruption goes relative to Python's recursion guard, degrade or crash the wallet RPC service for that request — a availability/DoS impact matching the "spend-triggered transaction-processing halt" acceptance criterion. It does not directly cause fund loss, but denies wallet service to a legitimate user processing offers, which is the same bug class and impact category (unhandled recursive-parser stack exhaustion causing abort) as CVE-2016-4353.

### Likelihood Explanation
Likelihood is high for triggering the crash: constructing deeply nested curried puzzles is cheap and requires no special access — any party able to send an offer file or spend bundle to be inspected can do it. The wallet's driver-matching code has no depth/complexity guard, unlike, e.g., the Data Layer's Rust tree code, which explicitly raises `chia_rs.datalayer.RecursionDepthExceededError` for excessive nesting — showing the project is aware of this bug class elsewhere but has not applied an equivalent safeguard to the wallet puzzle-driver recursion.

### Recommendation
- Add an explicit recursion/nesting-depth limit to `match_puzzle()` / each outer-puzzle driver's `match()`, and to `PuzzleInfo.also()`/`check_type()`, rejecting puzzle reveals or driver dicts that exceed a sane maximum layer count (mirroring the `RecursionDepthExceededError` pattern already used in the Data Layer Rust code).
- Wrap offer/spend-bundle ingestion paths (`Offer.from_bech32`, `Offer.from_spend_bundle`, `_get_offered_coins`, `check_for_special_offer_making`, `check_for_final_modifications`) so that `RecursionError` (and other parsing failures from malformed/adversarial nesting) is caught and converted into a normal validation error instead of propagating as an unhandled crash.
- Consider making the driver-matching traversal iterative (similar to the already-iterative `sha256_treehash` in `chia/types/blockchain_format/tree_hash.py`) to avoid relying on Python's call stack for untrusted-depth data at all.

### Proof of Concept
Conceptually (cannot execute in this environment):
1. Construct a coin spend whose puzzle reveal is a legitimate inner puzzle wrapped by N (e.g., 5,000+) nested singleton/CAT/ownership curry layers, each individually matchable by the corresponding outer-puzzle driver (`SingletonOuterPuzzle`, `CATOuterPuzzle`, `OwnershipOuterPuzzle`, etc.).
2. Package this spend as an offer (`Offer.from_spend_bundle`) or a raw spend bundle and deliver it to a victim wallet (e.g., as a bech32 offer file, or via RPC `take_offer`/`get_offer_summary`-equivalent calls).
3. When the victim wallet calls `Offer.from_spend_bundle` → `match_puzzle(UnknownPuzzle(...))`, the chain `match_puzzle → driver.match → self._match(=match_puzzle) → driver.match → ...` recurses once per layer; with enough layers this exceeds Python's default recursion limit (~1000), raising `RecursionError` inside wallet code that does not catch it, aborting the request/crashing the handling task.

### Citations

**File:** chia/wallet/puzzle_drivers.py (L60-79)
```python
    def also(self) -> PuzzleInfo | None:
        if "also" in self.info:
            return PuzzleInfo(self.info["also"])
        else:
            return None

    def check_type(self, types: list[str]) -> bool:
        if types == []:
            if self.also() is None:
                return True
            else:
                return False
        elif self.type() == types[0]:
            types.pop(0)
            if self.also():
                return self.also().check_type(types)  # type: ignore
            else:
                return self.check_type(types)
        else:
            return False
```

**File:** chia/wallet/outer_puzzles.py (L49-58)
```python
def match_puzzle(puzzle: UnknownPuzzle) -> PuzzleInfo | None:
    for driver in driver_lookup.values():
        potential_info: PuzzleInfo | None = driver.match(puzzle)
        if potential_info is not None:
            return potential_info
    return None


def construct_puzzle(constructor: PuzzleInfo, inner_puzzle: Program) -> Program:
    return driver_lookup[AssetType(constructor.type())].construct(constructor, inner_puzzle)
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

**File:** chia/wallet/trading/offer.py (L251-259)
```python
            parent_puzzle: UnknownPuzzle = UnknownPuzzle(known_program=parent_spend.puzzle_reveal)
            parent_solution = Program.from_serialized(parent_spend.solution)
            additions: list[Coin] = self._additions[parent_spend.coin]

            puzzle_driver = match_puzzle(parent_puzzle)
            if puzzle_driver is not None:
                asset_id = create_asset_id(puzzle_driver)
                inner_puzzle: Program | None = get_inner_puzzle(puzzle_driver, parent_puzzle, parent_solution)
                inner_solution: Program | None = get_inner_solution(puzzle_driver, parent_solution)
```

**File:** chia/wallet/trading/offer.py (L634-647)
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
                asset_id = None
```
